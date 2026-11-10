"""Ticket-to-Document conversion.

One job: turn a resolved ticket dict into a LlamaIndex Document suitable for
embedding and retrieval. No index access, no I/O — pure transformation.
"""

from __future__ import annotations

from typing import Any

from llama_index.core import Document


def ticket_to_document(ticket: dict[str, Any]) -> Document | None:
    """Convert a resolved ticket dict into a LlamaIndex Document.

    Returns None for tickets without a usable short_description + resolution,
    so callers can filter silently.
    """

    resolution = (
        ticket.get("resolution_clean")
        or ticket.get("close_notes_raw")
        or ""
    ).strip()
    short_desc = (ticket.get("short_description") or "").strip()

    if not resolution or not short_desc:
        return None

    text = f"Ticket: {short_desc}\n\nResolution: {resolution}"

    return Document(
        text=text,
        metadata={
            "number": ticket.get("number", ""),
            "short_description": short_desc,
            "assignment_group": ticket.get("assignment_group", ""),
            "category": ticket.get("category", ""),
            "close_code": ticket.get("close_code", ""),
            "resolution": resolution[:2000],
            "resolved_by": ticket.get("resolved_by", ""),
        },
        excluded_embed_metadata_keys=["resolution", "resolved_by"],
    )
