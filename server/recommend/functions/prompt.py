"""Prompt construction for the resolution synthesizer.

One job: build the system and user prompts from a ticket query and retrieved
evidence. No LLM call, no I/O — pure string assembly.
"""

from __future__ import annotations

from server import config
from server.models import ResolvedTicket, TicketQuery

SYSTEM_PROMPT = (
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


def build_user_prompt(
    ticket: TicketQuery, similar: list[ResolvedTicket]
) -> str:
    """Assemble the user prompt from the ticket and retrieved evidence."""

    evidence_lines = []
    for t in similar[: config.TOP_K]:
        evidence_lines.append(
            f"- {t.number} ({t.assignment_group}): {t.short_description}\n"
            f"  Resolution: {t.resolution[:500]}\n"
            f"  Similarity: {t.similarity}"
        )
    evidence_block = "\n".join(evidence_lines)

    return (
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
