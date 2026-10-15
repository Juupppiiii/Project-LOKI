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


# --- Richer signals: error codes and historical resolutions ---------------


def test_error_code_extraction_finds_common_shapes():
    from overlay import service as svc

    codes = svc._extract_error_codes(
        "Service failed with Event ID 1069 and exit code 7, HTTP 503, 0x80070005, ORA-00942"
    )
    assert "1069" in codes
    assert "7" in codes
    assert "503" in codes
    assert "0x80070005" in codes
    assert "ora-00942" in codes


def test_error_code_signal_matches_shared_code():
    from overlay import service as svc

    class _Trig:
        keywords = ["service", "event id 1069", "crash"]

    class _Proc:
        name = "Fix service crash"
        notes = ""
        trigger = _Trig()

    shared = svc._error_code_signal({"1069"}, _Proc())
    assert "1069" in shared
    # A ticket code the procedure never mentions is not a shared signal.
    assert svc._error_code_signal({"9999"}, _Proc()) == set()


def test_historical_signal_corroborates_from_solved_tickets():
    from overlay import service as svc
    from overlay.knowledge_base import SolvedTicket

    class _Trig:
        keywords = ["netprobe", "restart"]

    class _Proc:
        name = "Restart Netprobe service"
        trigger = _Trig()

    solved = [
        SolvedTicket(
            number="INC1",
            short_description="netprobe down on host",
            assignment_group="Wintel",
            close_code="Solved",
            resolution="restarted the netprobe service and confirmed running",
            similarity=0.8,
        )
    ]
    strength, cites = svc._historical_signal(solved, _Proc())
    assert strength > 0
    assert cites and cites[0]["number"] == "INC1"
    # No solved tickets -> no corroboration.
    assert svc._historical_signal([], _Proc()) == (0.0, [])


def test_error_code_boosts_confidence_and_adds_signal(service):
    # A ticket carrying an error code that a procedure references should surface
    # the error_code signal in the evidence when that procedure is a candidate.
    ticket = {
        "number": "INC9300",
        "short_description": "Netprobe service stopped, Event ID 7024 on ldn2ws9696",
        "description": "service not running",
        "assignment_group": "Global Wintel Server Support",
        "cmdb_ci": "ldn2ws9696",
    }
    result = service.recommend_candidates(ticket)
    # The evidence structure must now expose the new fields on every candidate.
    for c in result["candidates"]:
        assert "error_codes" in c["evidence"]
        assert "historical_tickets" in c["evidence"]
        assert isinstance(c["evidence"]["error_codes"], list)
        assert isinstance(c["evidence"]["historical_tickets"], list)


# --- Configuration-item signal and resolution clustering ------------------


def test_ci_signal_matches_resolved_host():
    from overlay import service as svc

    class _M:
        parameters = {"server": "ldn2ws9696"}

    assert svc._ci_signal({"cmdb_ci": "ldn2ws9696"}, _M()) == "ldn2ws9696"
    # Host label matches even with a domain suffix on the ticket side.
    assert svc._ci_signal({"cmdb_ci": "ldn2ws9696.tpicap.net"}, _M()) == "ldn2ws9696"
    # A different CI is not a match; no CI returns None.
    assert svc._ci_signal({"cmdb_ci": "other01"}, _M()) is None
    assert svc._ci_signal({}, _M()) is None
    assert svc._ci_signal({"cmdb_ci": "x"}, None) is None


def test_ci_and_group_appear_as_signals(service, restart_ticket):
    # restart_ticket has cmdb_ci ldn2ws9696 and the Wintel group; the matched
    # procedure resolves that host, so both signals should fire on the winner.
    result = service.recommend_candidates(restart_ticket)
    top = result["candidates"][0]
    assert "assignment_group" in top["evidence"]["signals"]
    assert "configuration_item" in top["evidence"]["signals"]
    assert top["evidence"]["configuration_item"]


def test_cluster_resolutions_groups_near_duplicates():
    from overlay import service as svc
    from overlay.knowledge_base import SolvedTicket

    def mk(num, text):
        return SolvedTicket(num, "svc down", "Wintel", "Solved", text, 0.5)

    solved = [
        mk("INC1", "Restarted the Netprobe service on ldn2ws9696 and confirmed running"),
        mk("INC2", "Restarted the Netprobe service on njc1ws040 and confirmed running"),
        mk("INC3", "Rebuilt the Outlook profile and restored mailbox access"),
    ]
    clusters = svc.cluster_resolutions(solved, threshold=0.4)
    # The two near-identical restarts collapse into one cluster of 2; the
    # Outlook one stands alone. Largest cluster first.
    assert clusters[0]["count"] == 2
    assert len(clusters) == 2
    assert "INC1" in clusters[0]["tickets"]


def test_resolution_clusters_service_shape(service, restart_ticket):
    result = service.resolution_clusters(restart_ticket, top=10)
    assert result["advisory"] is True
    assert "clusters" in result and isinstance(result["clusters"], list)
    assert "retrieved" in result
    # Advisory read-only: it must not create a run.
    before = len(service.list_runs())
    service.resolution_clusters(restart_ticket)
    assert len(service.list_runs()) == before
