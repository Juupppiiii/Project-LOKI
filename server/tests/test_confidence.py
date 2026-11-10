"""Tests for the recommend/functions/confidence.py — confidence extraction."""

from server.recommend.functions.confidence import extract_confidence


def test_extract_confidence():
    assert extract_confidence("High confidence: this matches well") == "high"
    assert extract_confidence("Confidence: medium based on 3 tickets") == "medium"
    assert extract_confidence("Low confidence, only one match") == "low"
    assert extract_confidence("Insufficient evidence to recommend") == "insufficient"
    assert extract_confidence("Here is a recommendation.") == "medium"
