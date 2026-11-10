"""Resolution synthesis via Claude (Bedrock Converse).

One job: given a ticket and retrieved evidence, call the LLM and return a
typed Recommendation. Falls back gracefully when the model is unavailable.
"""

from __future__ import annotations

import logging

from server.models import Recommendation, ResolvedTicket, TicketQuery
from server.rag.functions.index import setup_llm

from .confidence import extract_confidence
from .prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


def synthesize(
    ticket: TicketQuery, similar: list[ResolvedTicket]
) -> Recommendation:
    """Ask Claude to synthesize a resolution from the retrieved evidence."""

    if not similar:
        return Recommendation(
            summary=(
                "No similar resolved tickets found. A human should investigate "
                "this ticket from first principles."
            ),
            steps=[],
            cited_tickets=[],
            confidence="insufficient",
        )

    user_prompt = build_user_prompt(ticket, similar)

    try:
        llm = setup_llm()
        from llama_index.core.llms import ChatMessage

        response = llm.chat(
            [
                ChatMessage(role="system", content=SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_prompt),
            ]
        )
        text = str(response.message.content or "")

        return Recommendation(
            summary=text,
            steps=[],
            cited_tickets=[t.number for t in similar[:5]],
            confidence=extract_confidence(text),
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
