"""Corpus loading from knowledge JSON files.

One job: read ticket data from disk and convert it to LlamaIndex Documents
using the rag vertical's document converter. No index writes here — that is
the indexer's job.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from llama_index.core import Document

from server.rag.functions.document import ticket_to_document

logger = logging.getLogger(__name__)


def load_knowledge_dir(knowledge_dir: str | Path) -> list[Document]:
    """Load resolved tickets from knowledge/tickets/by_group/*.json."""

    by_group = Path(knowledge_dir) / "tickets" / "by_group"
    if not by_group.is_dir():
        logger.warning("No by_group directory found at %s", by_group)
        return []

    documents: list[Document] = []
    seen: set[str] = set()

    for json_file in sorted(by_group.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s: %s", json_file.name, exc)
            continue

        for ticket in data.get("tickets", []):
            number = ticket.get("number", "")
            if number in seen:
                continue
            seen.add(number)

            doc = ticket_to_document(ticket)
            if doc is not None:
                documents.append(doc)

    return documents


def load_json_file(filepath: str | Path) -> list[Document]:
    """Load resolved tickets from a flat JSON file."""

    path = Path(filepath)
    if not path.is_file():
        logger.warning("File not found: %s", path)
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    tickets = data if isinstance(data, list) else data.get("tickets", [])

    documents = []
    for ticket in tickets:
        doc = ticket_to_document(ticket)
        if doc is not None:
            documents.append(doc)

    return documents
