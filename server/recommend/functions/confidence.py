"""Confidence extraction from Claude's response text.

One job: parse the confidence label. No LLM call, no I/O.
"""

from __future__ import annotations


def extract_confidence(text: str) -> str:
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
    return "medium"
