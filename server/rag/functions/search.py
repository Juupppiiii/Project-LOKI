"""Vector similarity search.

One job: given a ticket query, retrieve the most similar resolved tickets from
the ChromaDB index and return them as typed ResolvedTicket models.
"""

from __future__ import annotations

from server import config
from server.models import ResolvedTicket, TicketQuery

from .index import get_index


def search_similar(ticket: TicketQuery) -> list[ResolvedTicket]:
    """Retrieve the top-K most similar resolved tickets for a query."""

    index = get_index()
    query_text = f"{ticket.short_description}\n{ticket.description}".strip()

    retriever = index.as_retriever(similarity_top_k=config.TOP_K)
    nodes = retriever.retrieve(query_text)

    results: list[ResolvedTicket] = []
    for node in nodes:
        meta = node.metadata or {}
        score = float(node.score) if node.score is not None else 0.0
        if score < config.SIMILARITY_THRESHOLD:
            continue
        results.append(
            ResolvedTicket(
                number=meta.get("number", "?"),
                short_description=meta.get("short_description", ""),
                assignment_group=meta.get("assignment_group", ""),
                resolution=meta.get("resolution", node.text[:500]),
                similarity=round(min(score, 1.0), 3),
            )
        )
    return results
