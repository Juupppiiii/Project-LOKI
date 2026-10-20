"""Tests for advisory efficiency intelligence.

The guarantees under test are mostly about what efficiency must NOT do:

* it never changes which procedure the keyword matcher authoritatively picks;
* its ``efficiency_ranked`` view is a separate, advisory ordering, not the
  authoritative one;
* ``plan_efficiency_advice`` annotates a plan without reordering, removing, or
  otherwise mutating any step;
* the automatability figure matches the planner's real risk classification;
* resolution-time stats honestly flag auto-acknowledgement-dominated groups.
"""

from __future__ import annotations

import pytest

from overlay import efficiency
from overlay.domain import Owner

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Efficiency index + profile (unit, no service)
# ---------------------------------------------------------------------------


def test_resolution_seconds_prefers_calendar_stc():
    assert efficiency._resolution_seconds({"calendar_stc": "300"}) == 300.0


def test_resolution_seconds_falls_back_to_timestamp_delta():
    row = {
        "calendar_stc": "0",
        "sys_created_on": "2026-01-01 10:00:00",
        "resolved_at": "2026-01-01 10:05:00",
    }
    assert efficiency._resolution_seconds(row) == 300.0


def test_resolution_seconds_none_when_unparseable():
    assert efficiency._resolution_seconds({"calendar_stc": "", "sys_created_on": "", "resolved_at": ""}) is None


def test_automatability_matches_risk_classifier(service):
    # The restart procedure is fully agent-runnable; disk cleanup is mixed.
    procs = {p.procedure_id: p for p in service.registry.procedures}
    restart = procs.get("wintel.service_restart")
    assert restart is not None
    auto = efficiency.automatability(restart)
    assert auto["total_steps"] == auto["agent_steps"]  # all agent
    assert auto["agent_share"] == 1.0


def test_auto_ack_group_is_flagged(tmp_path):
    # A group whose resolutions are all sub-threshold is flagged as auto-ack.
    csv_path = tmp_path / "closed.csv"
    csv_path.write_text(
        "assignment_group,calendar_stc,sys_created_on,resolved_at\n"
        "NOC,5,,\nNOC,8,,\nNOC,3,,\n",
        encoding="utf-8",
    )
    idx = efficiency.EfficiencyIndex(csv_path).load()
    g = idx.for_group("NOC")
    assert g is not None
    assert g.auto_ack_share == 1.0  # all below AUTO_ACK_SECONDS


# ---------------------------------------------------------------------------
# Service: match carries efficiency but keeps the authoritative pick
# ---------------------------------------------------------------------------


def test_match_pick_is_unchanged_by_efficiency(service, restart_ticket):
    result = service.preview_match(restart_ticket)
    assert result["matched"] is True
    assert result["procedure_id"] == "wintel.service_restart"
    # Efficiency data rides along but does not replace the authoritative pick.
    assert result["efficiency_enabled"] is True
    assert result["efficiency_advisory"] is True
    assert "efficiency" in result
    assert result["efficiency"]["advisory"] is True


def test_efficiency_ranked_is_separate_from_keyword_alternatives(service, restart_ticket):
    result = service.preview_match(restart_ticket)
    # Keyword alternatives keep their keyword order; efficiency_ranked is its
    # own sorted view. Both exist; the authoritative pick is still procedure_id.
    assert "alternatives" in result
    assert "efficiency_ranked" in result
    if result["efficiency_ranked"]:
        scores = [x["efficiency_score"] for x in result["efficiency_ranked"]]
        assert scores == sorted(scores, reverse=True)  # sorted by efficiency


def test_match_without_efficiency_toggle_off(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("BUCKY_EFFICIENCY", "0")
    result = service.preview_match(restart_ticket)
    assert result["matched"] is True
    assert result["efficiency_enabled"] is False
    assert "efficiency_ranked" not in result


# ---------------------------------------------------------------------------
# Service: plan advice annotates but never mutates
# ---------------------------------------------------------------------------


def _agent_run(service, restart_ticket):
    return service.propose(restart_ticket, actor="alice")


def test_plan_advice_does_not_mutate_the_plan(service, restart_ticket):
    run = _agent_run(service, restart_ticket)
    before = [(s.step_id, s.sequence, s.action, s.state) for s in service.get_run(run.run_id).steps]
    advice = service.plan_efficiency_advice(run.run_id)
    after = [(s.step_id, s.sequence, s.action, s.state) for s in service.get_run(run.run_id).steps]
    assert advice["mutates_plan"] is False
    assert before == after  # identical step list, order, and state


def test_plan_advice_annotates_every_step(service, restart_ticket):
    run = _agent_run(service, restart_ticket)
    advice = service.plan_efficiency_advice(run.run_id)
    run2 = service.get_run(run.run_id)
    assert len(advice["steps"]) == len(run2.steps)
    for note, step in zip(advice["steps"], run2.steps):
        assert note["step_id"] == step.step_id
        assert note["sequence"] == step.sequence
        # Category matches the real owner classification.
        expected = "automatable" if step.owner is Owner.AI else "human_decision"
        assert note["category"] == expected


def test_plan_advice_reports_human_handoff_on_mixed_plan(service):
    # Disk cleanup splits agent/human/agent; the first human step is the handoff.
    run = service.propose(
        {
            "number": "INC_DC",
            "short_description": "Low disk space on C drive ldn2ws9696",
            "description": "disk cleanup needed",
            "assignment_group": "Global Wintel Server Support",
        },
        actor="alice",
    )
    advice = service.plan_efficiency_advice(run.run_id)
    assert advice["human_steps"] >= 1
    assert advice["first_human_handoff_sequence"] is not None
