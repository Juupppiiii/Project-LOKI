"""ChromaDB vector index management.

Owns the shared index singleton. Other verticals call get_index() to query it;
the ingest vertical calls build_index() to populate it.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.embeddings.bedrock import BedrockEmbedding
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.vector_stores.chroma import ChromaVectorStore

from server import config

_index: VectorStoreIndex | None = None


def setup_llm() -> BedrockConverse:
    return BedrockConverse(
        model=config.BEDROCK_MODEL_ID,
        region_name=config.AWS_REGION,
    )


def setup_embed() -> BedrockEmbedding:
    return BedrockEmbedding(
        model_name="amazon.titan-embed-text-v2:0",
        region_name=config.AWS_REGION,
    )


def setup_chroma() -> ChromaVectorStore:
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

    Settings.embed_model = setup_embed()
    Settings.llm = setup_llm()

    vector_store = setup_chroma()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    _index = VectorStoreIndex.from_vector_store(
        vector_store,
        storage_context=storage_context,
    )
    return _index


def set_index(index: VectorStoreIndex) -> None:
    """Replace the shared index (used by the ingest pipeline after building)."""

    global _index
    _index = index


def reset_index() -> None:
    """Force re-creation on next call (for testing or after re-ingest)."""

    global _index
    _index = None
