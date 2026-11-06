"""RAG engine — the core of Bucky.

Ingest resolved tickets → build a vector index → retrieve similar resolutions
→ synthesize a recommendation with Claude. Built on LlamaIndex + ChromaDB +
Amazon Bedrock.

The engine has two modes:
  - ingest: load ticket data into the vector store (run once, then on updates)
  - query: given a new ticket, retrieve + synthesize a resolution recommendation
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import chromadb
from llama_index.core import (
    Document,
    Settings,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.bedrock import BedrockEmbedding
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.vector_stores.chroma import ChromaVectorStore

from . import config
from .models import QueryResponse, Recommendation, ResolvedTicket, TicketQuery

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LlamaIndex setup (lazy, reused across requests)
# ---------------------------------------------------------------------------

_index: VectorStoreIndex | None = None


def _setup_llm() -> BedrockConverse:
    return BedrockConverse(
        model=config.BEDROCK_MODEL_ID,
        region_name=config.AWS_REGION,
        # Let the model use its default temperature.
    )


def _setup_embed() -> BedrockEmbedding:
    return BedrockEmbedding(
        model_name="amazon.titan-embed-text-v2:0",
        region_name=config.AWS_REGION,
    )


def _setup_chroma() -> ChromaVectorStore:
    persist_dir = config.CHROMA_PERSIST_DIR
    Path(persist_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(
        name="resolved_tickets",
        metadata={"hnsw:space": "cosine"},
    )
    return ChromaVectorStore(chroma_collection=collection)


def get_index() -> VectorStoreIndex:
    """Return the shared vector index, creating it on first call."""

    global _index
    if _index is not None:
        return _index

    embed = _setup_embed()
    llm = _setup_llm()
    Settings.embed_model = embed
    Settings.llm = llm

    vector_store = _setup_chroma()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    _index = VectorStoreIndex.from_vector_store(
        vector_store,
        storage_context=storage_context,
    )
    return _index


def reset_index() -> None:
    """Force re-creation on next call (for testing or after re-ingest)."""

    global _index
    _index = None


# ---------------------------------------------------------------------------
# Ingest — load resolved tickets into the vector store
# ---------------------------------------------------------------------------


def _ticket_to_document(ticket: dict[str, Any]) -> Document | None:
    """Convert a resolved ticket dict into a LlamaIndex Document.

    The text is a combination of the short description and the resolution.
    Metadata carries the structured fields for filtering and display.
    """

    resolution = (
        ticket.get("resolution_clean")
        or ticket.get("close_notes_raw")
        or ""
    ).strip()
    short_desc = (ticket.get("short_description") or "").strip()

    if not resolution or not short_desc:
        return None  # skip tickets with no usable content

    text = f"Ticket: {short_desc}\n\nResolution: {resolution}"

    return Document(
        text=text,
        metadata={
            "number": ticket.get("number", ""),
            "short_description": short_desc,
            "assignment_group": ticket.get("assignment_group", ""),
            "category": ticket.get("category", ""),
            "close_code": ticket.get("close_code", ""),
            "resolution": resolution[:2000],  # cap for metadata storage
            "resolved_by": ticket.get("resolved_by", ""),
        },
        excluded_embed_metadata_keys=["resolution", "resolved_by"],
    )


def ingest_knowledge_json(knowledge_dir: str | Path) -> int:
    """Ingest resolved tickets from the knowledge JSON files.

    Reads the by_group/*.json files (the structured ticket corpus) and indexes
    them into ChromaDB. Returns the number of documents indexed.
    """

    knowledge_path = Path(knowledge_dir)
    by_group = knowledge_path / "tickets" / "by_group"

    if not by_group.is_dir():
        logger.warning("No by_group directory found at %s", by_group)
        return 0

    documents: list[Document] = []
    seen_numbers: set[str] = set()

    for json_file in sorted(by_group.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", json_file.name, exc)
            continue

        tickets = data.get("tickets", [])
        for ticket in tickets:
            number = ticket.get("number", "")
            if number in seen_numbers:
                continue  # dedup across group files
            seen_numbers.add(number)

            doc = _ticket_to_document(ticket)
            if doc is not None:
                documents.append(doc)

    if not documents:
        logger.warning("No indexable documents found in %s", knowledge_path)
        return 0

    logger.info("Indexing %d resolved tickets from %s", len(documents), by_group)

    # Build a fresh index with the documents.
    embed = _setup_embed()
    llm = _setup_llm()
    Settings.embed_model = embed
    Settings.llm = llm

    vector_store = _setup_chroma()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

    global _index
    _index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        transformations=[splitter],
        show_progress=True,
    )

    logger.info("Indexed %d documents into ChromaDB", len(documents))
    return len(documents)


def ingest_resolved_tickets_json(filepath: str | Path) -> int:
    """Ingest from the flat resolved_tickets.json (alternative to by_group)."""

    path = Path(filepath)
    if not path.is_file():
        logger.warning("File not found: %s", path)
        return 0

    data = json.loads(path.read_text(encoding="utf-8"))
    tickets = data if isinstance(data, list) else data.get("tickets", [])

    documents = []
    for ticket in tickets:
        doc = _ticket_to_document(ticket)
        if doc is not None:
            documents.append(doc)

    if not documents:
        return 0

    embed = _setup_embed()
    llm = _setup_llm()
    Settings.embed_model = embed
    Settings.llm = llm

    vector_store = _setup_chroma()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)

    global _index
    _index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        transformations=[splitter],
        show_progress=True,
    )

    return len(documents)


# ---------------------------------------------------------------------------
# Query — retrieve similar tickets + synthesize recommendation
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are Bucky, an IT operations assistant. Given a new ticket and a set "
    "of historically resolved similar tickets, your job is to recommend the "
    "best resolution approach.\n\n"
    "Rules:\n"
    "- Ground every recommendation in the retrieved ticket evidence. Cite "
    "ticket numbers when referencing a resolution.\n"
    "- Provide clear, actionable steps an engineer can follow.\n"
    "- If the evidence is weak or contradictory, say so honestly rather than "
    "guessing.\n"
    "- Rate your confidence: high (strong pattern across multiple tickets), "
    "medium (a few similar cases), low (weak matches), or insufficient "
    "(nothing relevant found).\n"
    "- Never fabricate ticket numbers or resolutions.\n"
    "- Be concise and direct."
)


def query(ticket: TicketQuery) -> QueryResponse:
    """Given a new ticket, retrieve similar resolutions and synthesize advice."""

    index = get_index()
    query_text = f"{ticket.short_description}\n{ticket.description}".strip()

    # Retrieve similar tickets from the vector store.
    retriever = index.as_retriever(
        similarity_top_k=config.TOP_K,
    )
    nodes = retriever.retrieve(query_text)

    # Build the similar-tickets list from retrieved nodes.
    similar: list[ResolvedTicket] = []
    for node in nodes:
        meta = node.metadata or {}
        score = float(node.score) if node.score is not None else 0.0
        if score < config.SIMILARITY_THRESHOLD:
            continue
        similar.append(
            ResolvedTicket(
                number=meta.get("number", "?"),
                short_description=meta.get("short_description", ""),
                assignment_group=meta.get("assignment_group", ""),
                resolution=meta.get("resolution", node.text[:500]),
                similarity=round(min(score, 1.0), 3),
            )
        )

    # Synthesize a recommendation using Claude.
    recommendation = _synthesize(ticket, similar)

    return QueryResponse(
        query=ticket,
        similar_tickets=similar,
        recommendation=recommendation,
        model=config.BEDROCK_MODEL_ID,
        retrieval_count=len(similar),
    )


def _synthesize(ticket: TicketQuery, similar: list[ResolvedTicket]) -> Recommendation:
    """Ask Claude to synthesize a resolution from the retrieved evidence."""

    if not similar:
        return Recommendation(
            summary="No similar resolved tickets found. A human should investigate this ticket from first principles.",
            steps=[],
            cited_tickets=[],
            confidence="insufficient",
        )

    evidence_lines = []
    for t in similar[:config.TOP_K]:
        evidence_lines.append(
            f"- {t.number} ({t.assignment_group}): {t.short_description}\n"
            f"  Resolution: {t.resolution[:500]}\n"
            f"  Similarity: {t.similarity}"
        )
    evidence_block = "\n".join(evidence_lines)

    user_prompt = (
        f"New ticket:\n"
        f"  Short description: {ticket.short_description}\n"
        f"  Description: {ticket.description or '(none)'}\n"
        f"  Category: {ticket.category or '(none)'}\n"
        f"  Assignment group: {ticket.assignment_group or '(none)'}\n\n"
        f"Similar resolved tickets:\n{evidence_block}\n\n"
        f"Based on these historical resolutions, recommend the best approach "
        f"to resolve this new ticket. Provide clear steps, cite the ticket "
        f"numbers you relied on, and rate your confidence."
    )

    try:
        llm = _setup_llm()
        from llama_index.core.llms import ChatMessage

        response = llm.chat(
            [
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ]
        )
        text = str(response.message.content or "")

        return Recommendation(
            summary=text,
            steps=[],  # Claude includes steps inline in the summary
            cited_tickets=[t.number for t in similar[:5]],
            confidence=_extract_confidence(text),
        )
    except Exception as exc:
        logger.exception("LLM synthesis failed")
        return Recommendation(
            summary=(
                f"LLM synthesis unavailable ({exc}). The retrieved tickets "
                f"above show how similar issues were resolved historically."
            ),
            steps=[],
            cited_tickets=[t.number for t in similar[:5]],
            confidence="low",
        )


def _extract_confidence(text: str) -> str:
    """Best-effort extraction of the confidence label from Claude's response."""

    lower = text.lower()
    if "high confidence" in lower or "confidence: high" in lower:
        return "high"
    if "medium confidence" in lower or "confidence: medium" in lower:
        return "medium"
    if "low confidence" in lower or "confidence: low" in lower:
        return "low"
    if "insufficient" in lower:
        return "insufficient"
    return "medium"  # default when Claude doesn't label it
