"""Tests for the Tier A read-only triage agent.

The agent is an orchestration layer over the read-only diagnostic gateway. The
guarantees under test:
  * it parses the CheckMK host-DOWN alert format (host + alert kind);
  * it runs the host-DOWN playbook as bounded, read-only tool calls;
  * reachable + no correlation -> self_resolved; neighbours flapped -> network_blip;
    active change -> planned_change;
  * UNREACHABLE -> never auto-close; routes to a human (still_down_needs_human);
  * no parseable host -> abstains (insufficient_evidence), routes to a human;
  * the step budget is honoured;
  * a planner that names an unknown tool is default-denied (never executed);
  * every host read goes through the gateway -- the agent has no other path.
"""

from __future__ import annotations

import pytest

from overlay import diagnostics
from overlay import triage_agent as ta

pytestmark = pytest.mark.unit


HOST_DOWN_TICKET = {
    "short_description": "njc1ws2471.corp.ad.tullib.com UP -> DOWN No IP packet received for 60.9 s",
    "description": "",
    "category": "Events-CMK",
}


class _StubBackend:
    """Diagnostic backend with controllable reachability; touches nothing."""

    def __init__(self, *, reachable: bool):
        self._reachable = reachable

    @property
    def name(self) -> str:
        return "stub"

    def read(self, operation_id, params):
        if operation_id == "tcp_check":
            return {"target": params["target"], "port": int(params["port"]),
                    "reachable": self._reachable, "latency_ms": 2}
        if operation_id == "dns_lookup":
            return {"name": params["name"], "addresses": ["10.0.0.5"]}
        if operation_id == "service_status":
            return {"service": params["service_name"], "status": "Running"}
        if operation_id == "disk_free":
            return {"drive": "C:", "free_gb": 40.0, "free_percent": 18.0}
        if operation_id == "event_log_query":
            return {"log": params["log_name"], "entries": []}
        raise diagnostics.DiagnosticFailed(f"no stub for {operation_id}")


def _gateway(*, reachable: bool) -> diagnostics.DiagnosticGateway:
    # Injecting a backend bypasses the live switches but keeps all validation.
    return diagnostics.DiagnosticGateway(backend=_StubBackend(reachable=reachable))


class _Context:
    def __init__(self, *, neighbors=0, change=None):
        self._neighbors = neighbors
        self._change = change

    def neighbors_flapped(self, host):
        return {"neighbors_flapped": self._neighbors > 0, "count": self._neighbors}

    def active_change(self, host):
        if self._change:
            return {"active_change": True, "change_ref": self._change}
        return {"active_change": False}

    def similar_resolutions(self, alert):
        return {"examples": []}


# --- alert parsing --------------------------------------------------------


def test_parses_host_and_kind_from_checkmk_format():
    alert = ta.parse_alert(HOST_DOWN_TICKET)
    assert alert["host"] == "njc1ws2471.corp.ad.tullib.com"
    assert alert["alert_kind"] == ta.AlertKind.HOST_DOWN.value
    assert alert["transition"] == "UP -> DOWN"


# --- happy paths ----------------------------------------------------------


def test_reachable_no_correlation_is_self_resolved():
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), context=_Context())
    result = agent.run(HOST_DOWN_TICKET)
    assert result.verdict.classification is ta.TriageClassification.SELF_RESOLVED
    assert result.verdict.routed_to_human is False
    # every host read flowed through the gateway tool set
    assert "check_connectivity" in [e["tool_id"] for e in result.transcript]


def test_neighbors_flapped_is_network_blip():
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), context=_Context(neighbors=4))
    result = agent.run(HOST_DOWN_TICKET)
    assert result.verdict.classification is ta.TriageClassification.NETWORK_BLIP
    assert result.verdict.routed_to_human is False


def test_active_change_is_planned_change():
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), context=_Context(change="CHG0171547"))
    result = agent.run(HOST_DOWN_TICKET)
    assert result.verdict.classification is ta.TriageClassification.PLANNED_CHANGE
    assert "CHG0171547" in result.verdict.draft_work_note


# --- safety paths ---------------------------------------------------------


def test_unreachable_never_auto_closes_and_routes_to_human():
    agent = ta.TriageAgent(gateway=_gateway(reachable=False), context=_Context())
    result = agent.run(HOST_DOWN_TICKET)
    assert result.verdict.classification is ta.TriageClassification.STILL_DOWN_NEEDS_HUMAN
    assert result.verdict.routed_to_human is True
    assert "close" not in result.verdict.recommended_disposition.lower()


def test_no_host_abstains_and_routes_to_human():
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), context=_Context())
    result = agent.run({"short_description": "something vague", "description": ""})
    assert result.verdict.classification is ta.TriageClassification.INSUFFICIENT_EVIDENCE
    assert result.verdict.routed_to_human is True
    assert result.steps_used == 0


# --- loop invariants ------------------------------------------------------


def test_step_budget_is_honoured():
    # A planner that always wants to run a tool must be capped by max_steps.
    class _Greedy:
        name = "greedy"

        def next_step(self, *, alert, transcript, tools, step):
            return ta.PlanDecision(is_final=False, tool_id="check_connectivity",
                                   params={"target": alert["host"], "port": 3389},
                                   reason="again")

    agent = ta.TriageAgent(gateway=_gateway(reachable=True), planner=_Greedy(), max_steps=3)
    result = agent.run(HOST_DOWN_TICKET)
    assert result.steps_used == 3


def test_unknown_tool_from_planner_is_denied_not_executed():
    class _Rogue:
        name = "rogue"
        _done = False

        def next_step(self, *, alert, transcript, tools, step):
            if not self._done:
                self._done = True
                return ta.PlanDecision(is_final=False, tool_id="rm_rf_everything",
                                       params={"target": alert["host"]}, reason="evil")
            return ta.PlanDecision(is_final=True)

    agent = ta.TriageAgent(gateway=_gateway(reachable=True), planner=_Rogue())
    result = agent.run(HOST_DOWN_TICKET)
    # the rogue tool was recorded as a failed/denied entry, never executed
    rogue = [e for e in result.transcript if e["tool_id"] == "rm_rf_everything"]
    assert rogue and rogue[0]["ok"] is False
    assert "unknown tool" in rogue[0]["error"]


def test_tools_are_all_read_only():
    assert all(t["read_only"] for t in ta.describe_tools())


def test_disabled_by_default():
    # Master switch is off unless explicitly enabled.
    assert ta.triage_agent_enabled() is False


# --- Bedrock tool-use planner (fake Converse client) ----------------------


class _FakeConverseClient:
    """Returns scripted Converse responses, one per call, to drive the planner."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            # Default: finish, so a greedy test can't loop forever.
            return _finish_response("done")
        return self._responses.pop(0)


def _tooluse_response(name, params):
    return {"output": {"message": {"content": [{"toolUse": {"name": name, "input": params}}]}}}


def _finish_response(reason):
    return {"output": {"message": {"content": [{"toolUse": {"name": "finish_triage", "input": {"reason": reason}}}]}}}


def test_toolconfig_only_exposes_registry_tools_plus_finish():
    cfg = ta._tool_config()
    names = {t["toolSpec"]["name"] for t in cfg["tools"]}
    registry = {t.tool_id for t in ta._TOOLS}
    assert registry.issubset(names)
    assert "finish_triage" in names
    # No tool outside the registry (+ sentinel) leaks into what the model may pick.
    assert names == registry | {"finish_triage"}


def test_bedrock_planner_parses_tooluse_then_finish():
    client = _FakeConverseClient([
        _tooluse_response("check_connectivity", {"target": "njc1ws2471.corp.ad.tullib.com", "port": 3389}),
        _finish_response("reachable, enough evidence"),
    ])
    planner = ta.BedrockPlanner(client=client, model_id="test-model")
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), planner=planner, context=_Context())
    result = agent.run(HOST_DOWN_TICKET)
    # The model drove exactly one read-only tool then finished.
    assert [e["tool_id"] for e in result.transcript] == ["check_connectivity"]
    assert result.verdict.classification is ta.TriageClassification.SELF_RESOLVED


def test_bedrock_planner_hallucinated_tool_is_denied_not_executed():
    client = _FakeConverseClient([
        _tooluse_response("delete_server", {"target": "njc1ws2471.corp.ad.tullib.com"}),
        _finish_response("done"),
    ])
    planner = ta.BedrockPlanner(client=client, model_id="test-model")
    agent = ta.TriageAgent(gateway=_gateway(reachable=True), planner=planner, context=_Context())
    result = agent.run(HOST_DOWN_TICKET)
    bad = [e for e in result.transcript if e["tool_id"] == "delete_server"]
    assert bad and bad[0]["ok"] is False and "unknown tool" in bad[0]["error"]


def test_agent_falls_back_to_rules_when_model_planner_fails():
    class _BrokenPlanner:
        name = "broken-model"

        def next_step(self, **kwargs):
            raise ta.PlannerUnavailable("bedrock exploded")

    agent = ta.TriageAgent(gateway=_gateway(reachable=True), planner=_BrokenPlanner(), context=_Context())
    result = agent.run(HOST_DOWN_TICKET)
    # Despite the model failure, the deterministic playbook still produced a verdict.
    assert result.verdict.classification is ta.TriageClassification.SELF_RESOLVED
    assert "fallback" in result.planner
    assert len(result.transcript) >= 1


def test_make_planner_picks_bedrock_when_llm_enabled(monkeypatch):
    monkeypatch.setenv("BUCKY_LLM_ENABLED", "1")
    planner = ta.make_planner()
    assert isinstance(planner, ta.BedrockPlanner)


def test_make_planner_offline_is_rule_based(monkeypatch):
    monkeypatch.delenv("BUCKY_LLM_ENABLED", raising=False)
    planner = ta.make_planner()
    assert isinstance(planner, ta.RuleBasedPlanner)
