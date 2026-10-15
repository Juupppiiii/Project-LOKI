"""Tests for evidence-based ranked recommendations with an abstain path.

`recommend_candidates` fuses the authoritative keyword matcher and the advisory
semantic matcher into one ranked list. The guarantees under test:

* It is advisory: it never creates a run, classifies risk, or authorises.
* Every candidate cites concrete evidence (keywords, group, params, signals).
* It ABSTAINS (decision == 'insufficient_evidence') when the top candidate is
  below the confidence floor, rather than asserting a weak match.
* The confidence floor is tunable via LOKI_RECOMMEND_MIN_CONFIDENCE.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.unit


def test_recommends_on_a_strong_keyword_match(service, restart_ticket):
    before = len(service.list_runs())
    result = service.recommend_candidates(restart_ticket, top=5)

    assert result["advisory"] is True
    assert result["decision"] == "recommend"
    assert result["abstained"] is False
    assert result["candidates"], "a strong match should surface candidates"

    top = result["candidates"][0]
    assert 0.0 <= top["confidence"] <= 1.0
    assert top["confidence"] >= result["confidence_floor"]
    assert top["confidence_band"] in ("high", "medium", "low")
    # Keyword signal must be among the evidence for a known keyword match.
    assert "keyword" in top["evidence"]["signals"]
    assert top["evidence"]["matched_keywords"]

    # Advisory: recommending must never create a run.
    assert len(service.list_runs()) == before


def test_candidate_evidence_has_the_full_shape(service, restart_ticket):
    result = service.recommend_candidates(restart_ticket)
    for c in result["candidates"]:
        assert {
            "procedure_id",
            "procedure_name",
            "confidence",
            "confidence_band",
            "keyword_score",
            "semantic_similarity",
            "evidence",
        } <= set(c)
        ev = c["evidence"]
        assert {
            "matched_keywords",
            "assignment_group_match",
            "resolved_parameters",
            "missing_parameters",
            "signals",
        } <= set(ev)
        assert isinstance(ev["signals"], list)


def test_abstains_on_gibberish(service):
    ticket = {
        "number": "INC9200",
        "short_description": "zxqw plover mimsy borogove frumious",
        "description": "qqq wwww eeee rrrr tttt",
        "assignment_group": "Nonexistent Group",
    }
    result = service.recommend_candidates(ticket)
    assert result["decision"] == "insufficient_evidence"
    assert result["abstained"] is True
    # Abstaining still explains itself.
    assert isinstance(result["reason"], str) and result["reason"]


def test_authoritative_match_mirrors_keyword_engine(service, restart_ticket):
    kw = service.preview_match(restart_ticket)
    rec = service.recommend_candidates(restart_ticket)
    assert rec["authoritative_match"] is not None
    assert rec["authoritative_match"]["procedure_id"] == kw["procedure_id"]


def test_floor_can_be_tuned_to_force_abstain(service, restart_ticket, monkeypatch):
    # A floor above 1.0 can never be met, so even a strong match abstains.
    monkeypatch.setenv("LOKI_RECOMMEND_MIN_CONFIDENCE", "1.01")
    result = service.recommend_candidates(restart_ticket)
    assert result["decision"] == "insufficient_evidence"
    assert result["abstained"] is True
    # Candidates are still returned (abstaining is about the decision, not recall).
    assert result["candidates"]


def test_candidates_are_sorted_by_confidence_desc(service, restart_ticket):
    result = service.recommend_candidates(restart_ticket, top=5)
    confidences = [c["confidence"] for c in result["candidates"]]
    assert confidences == sorted(confidences, reverse=True)


def test_recommending_creates_nothing_even_when_abstaining(service):
    before = len(service.list_runs())
    service.recommend_candidates({"number": "INC9201", "short_description": "noise"})
    assert len(service.list_runs()) == before
