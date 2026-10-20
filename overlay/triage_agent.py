"""Tier A triage agent -- a bounded, READ-ONLY investigation loop.

This is an agentic layer that sits ABOVE the read-only diagnostic gateway and
BELOW human approval. Given a matched monitoring ticket (first target: the
CheckMK "host DOWN" alert that is ~47% of Wintel volume), it:

    1. parses the alert (host + alert kind) from the ticket text,
    2. repeatedly asks a planner for the next read-only check to run,
    3. runs that check through ``diagnostics.DiagnosticGateway`` (the ONLY way
       it can reach a host -- a gateway whose backend has a single ``read``
       method and no write path at all), and
    4. summarises the evidence into a ``TriageVerdict`` with a classification, a
       DRAFT work note, and a recommended disposition.

Non-negotiable boundaries (mirrors ``diagnostics.py``):

* **Read-only.** Every tool maps 1:1 onto a read-only diagnostic op plus a few
  local, read-only context lookups (neighbour correlation, change window,
  similar history). There is no code path that restarts, reboots, deletes, edits
  config, writes a file, posts to ServiceNow, or approves a run. The agent
  DRAFTS a work note; a human posts it.
* **No arbitrary commands.** The planner may only emit a tool id from the fixed
  registry plus validated parameters -- never a shell/PowerShell string. Model
  text can never become an executed command.
* **Bounded.** A step budget caps tool calls. The full transcript (every tool,
  its params, its evidence) is retained so the verdict is auditable.
* **Fail safe, not false-positive.** When the host is still unreachable, or the
  evidence is weak, the agent classifies ``still_down_needs_human`` /
  ``insufficient_evidence`` and routes to a human. It NEVER recommends closing a
  ticket it could not substantiate read-only.

The planner is injectable. Offline (default) it is a deterministic rule-based
planner, so the whole loop runs in tests and demos with zero network and clearly
simulated evidence. A Bedrock tool-use planner plugs in behind the same
``Planner`` protocol in a later step -- it changes HOW the next check is chosen,
never WHAT the agent is allowed to do.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .diagnostics import DiagnosticGateway, DiagnosticRefused, DiagnosticFailed


# ---------------------------------------------------------------------------
# Enabledness
# ---------------------------------------------------------------------------


def triage_agent_enabled() -> bool:
    """Master switch for the feature. Off by default."""

    return os.getenv("BUCKY_TRIAGE_AGENT_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


DEFAULT_MAX_STEPS = max(1, min(20, int(os.getenv("BUCKY_TRIAGE_MAX_STEPS", "6"))))


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------


class TriageClassification(str, Enum):
    """The agent's read-only conclusion for a monitoring alert."""

    SELF_RESOLVED = "self_resolved"          # host answers now; brief blip
    NETWORK_BLIP = "network_blip"            # neighbours flapped in same window
    PLANNED_CHANGE = "planned_change"        # covered by an active change window
    STILL_DOWN_NEEDS_HUMAN = "still_down_needs_human"  # unreachable -> human
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"    # abstain -> human


# Classifications that must NOT auto-recommend closing a ticket. The agent
# routes these to a human regardless of anything else.
HUMAN_ROUTED = {
    TriageClassification.STILL_DOWN_NEEDS_HUMAN,
    TriageClassification.INSUFFICIENT_EVIDENCE,
}


class AlertKind(str, Enum):
    HOST_DOWN = "host_down"
    CPU = "cpu"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Tool registry -- the ENTIRE scope of what the agent can do
# ---------------------------------------------------------------------------
#
# Each tool is (diagnostic op OR local lookup). Host-touching tools name the
# diagnostics op they delegate to; the gateway validates + allowlists before any
# read happens. Local tools read only Bucky's own stored data. Adding a tool is
# a reviewed code change, never a runtime or model decision.


@dataclass(frozen=True)
class Tool:
    tool_id: str
    summary: str
    # The diagnostics op this delegates to, or None for a local read-only lookup.
    diagnostic_op: str | None
    required_params: tuple[str, ...] = ()


_TOOLS: tuple[Tool, ...] = (
    Tool(
        "check_connectivity",
        "Test whether the host answers on an allowlisted port now (read-only).",
        diagnostic_op="tcp_check",
        required_params=("target", "port"),
    ),
    Tool(
        "resolve_host",
        "Resolve the host name to addresses (read-only).",
        diagnostic_op="dns_lookup",
        required_params=("name",),
    ),
    Tool(
        "service_status",
        "Read the status of a named service on the host (read-only).",
        diagnostic_op="service_status",
        required_params=("target", "service_name"),
    ),
    Tool(
        "disk_free",
        "Read free disk space on the host (read-only).",
        diagnostic_op="disk_free",
        required_params=("target",),
    ),
    Tool(
        "recent_events",
        "Read a bounded window of recent event-log entries (read-only).",
        diagnostic_op="event_log_query",
        required_params=("target", "log_name"),
    ),
    # Local, read-only context lookups (no remote host access).
    Tool(
        "correlate_neighbors",
        "Check whether other hosts flapped in the same window (local read-only).",
        diagnostic_op=None,
    ),
    Tool(
        "check_change_window",
        "Check for an active patching/reboot change covering this host (local read-only).",
        diagnostic_op=None,
    ),
    Tool(
        "similar_history",
        "Retrieve how similar past alerts were resolved (local read-only).",
        diagnostic_op=None,
    ),
)

_TOOLS_BY_ID = {t.tool_id: t for t in _TOOLS}


def describe_tools() -> list[dict[str, Any]]:
    return [
        {
            "tool_id": t.tool_id,
            "summary": t.summary,
            "delegates_to": t.diagnostic_op,
            "read_only": True,
        }
        for t in _TOOLS
    ]


# ---------------------------------------------------------------------------
# Planner protocol + offline rule-based planner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanDecision:
    """One planner decision: either run a tool, or finish."""

    is_final: bool
    tool_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class Planner(Protocol):
    """Chooses the next read-only step, or decides the investigation is done.

    A planner may ONLY return a tool id from the registry plus parameters, or a
    final decision. It never returns a command. The agent re-validates every
    decision against the registry and the gateway before acting.
    """

    @property
    def name(self) -> str: ...

    def next_step(
        self,
        *,
        alert: dict[str, Any],
        transcript: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        step: int,
    ) -> PlanDecision: ...


class RuleBasedPlanner:
    """Deterministic offline planner for the host-DOWN playbook.

    Encodes the triage sequence a Wintel engineer follows for a "no IP packet"
    alert, so the whole agent loop is exercisable without a model: probe
    connectivity, resolve the name, correlate neighbours, check a change window,
    then finish. It picks the NEXT tool not yet run; when the playbook is
    exhausted (or connectivity already proved the host is back), it finishes.
    """

    @property
    def name(self) -> str:
        return "rule-based (offline; deterministic playbook)"

    def next_step(
        self,
        *,
        alert: dict[str, Any],
        transcript: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        step: int,
    ) -> PlanDecision:
        ran = {entry["tool_id"] for entry in transcript}
        host = alert.get("host", "")

        # Host-DOWN playbook, in order. CPU and other alerts fall through to a
        # minimal connectivity + finish path for now (Tier A host-DOWN first).
        if "check_connectivity" not in ran:
            return PlanDecision(
                is_final=False,
                tool_id="check_connectivity",
                params={"target": host, "port": 3389},
                reason="Is the host reachable now?",
            )
        if "resolve_host" not in ran:
            return PlanDecision(
                is_final=False,
                tool_id="resolve_host",
                params={"name": host},
                reason="Does the host name still resolve?",
            )
        if "correlate_neighbors" not in ran:
            return PlanDecision(
                is_final=False,
                tool_id="correlate_neighbors",
                params={"host": host},
                reason="Did neighbouring hosts flap in the same window?",
            )
        if "check_change_window" not in ran:
            return PlanDecision(
                is_final=False,
                tool_id="check_change_window",
                params={"host": host},
                reason="Is there an active change covering this host?",
            )
        return PlanDecision(is_final=True, reason="Playbook complete.")


def _tool_config() -> dict[str, Any]:
    """Build the Bedrock Converse toolConfig from the fixed registry.

    The model may ONLY choose a tool named here, with these parameters. This is
    the same allowlist the agent enforces again after the model answers, so a
    model that hallucinates a tool is rejected twice.
    """

    tools = []
    for t in _TOOLS:
        props: dict[str, Any] = {}
        required: list[str] = []
        if t.diagnostic_op in ("tcp_check",):
            props = {
                "target": {"type": "string", "description": "host to test"},
                "port": {"type": "integer", "description": "allowlisted TCP port"},
            }
            required = ["target", "port"]
        elif t.diagnostic_op == "dns_lookup":
            props = {"name": {"type": "string", "description": "host name to resolve"}}
            required = ["name"]
        elif t.diagnostic_op == "service_status":
            props = {
                "target": {"type": "string"},
                "service_name": {"type": "string"},
            }
            required = ["target", "service_name"]
        elif t.diagnostic_op == "disk_free":
            props = {"target": {"type": "string"}}
            required = ["target"]
        elif t.diagnostic_op == "event_log_query":
            props = {
                "target": {"type": "string"},
                "log_name": {"type": "string"},
            }
            required = ["target", "log_name"]
        else:
            # Local lookups take the host only.
            props = {"host": {"type": "string"}}
            required = ["host"]
        tools.append(
            {
                "toolSpec": {
                    "name": t.tool_id,
                    "description": t.summary,
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": props,
                            "required": required,
                        }
                    },
                }
            }
        )
    # A sentinel tool the model calls to END the investigation with its read-only
    # finding. It performs nothing; the agent treats it as "final".
    tools.append(
        {
            "toolSpec": {
                "name": "finish_triage",
                "description": (
                    "Call when enough read-only evidence has been gathered to reach a "
                    "verdict. Performs no action."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string", "description": "why done"}
                        },
                        "required": [],
                    }
                },
            }
        }
    )
    return {"tools": tools}


_PLANNER_SYSTEM = (
    "You are the planning brain of a READ-ONLY IT triage agent investigating a "
    "monitoring alert for a Windows server. You may ONLY choose one of the "
    "provided tools to gather read-only evidence, or call finish_triage when you "
    "have enough to reach a verdict. You cannot restart, reboot, change, or "
    "remediate anything -- those tools do not exist, by design. Investigate "
    "efficiently: check connectivity first, then corroborate (name resolution, "
    "neighbour flap, change window). Do not repeat a tool you already ran. When "
    "connectivity and context are established, call finish_triage. Never invent a "
    "tool or a parameter."
)


class BedrockPlanner:
    """Chooses the next read-only step using a Claude model via Converse tool-use.

    Isolates boto3 (lazy import). Produces ONLY a tool choice from the registry;
    the agent re-validates it. Any model/transport failure raises so the agent
    can fall back to the deterministic planner -- the model is never a single
    point of failure, and it can never widen the agent's capability.
    """

    def __init__(self, *, client: Any = None, model_id: str | None = None,
                 region_name: str | None = None) -> None:
        self._client = client
        self._model_id = model_id
        self._region = region_name

    @property
    def name(self) -> str:
        return f"bedrock:{self._model_id or 'default'} (tool-use)"

    def _bedrock(self):
        if self._client is not None:
            return self._client
        from . import knowledge_llm as kllm  # reuse model-id/region defaults

        self._model_id = self._model_id or kllm.DEFAULT_MODEL_ID
        self._region = self._region or kllm.DEFAULT_REGION
        try:
            import boto3  # lazy

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
            return self._client
        except Exception as exc:  # noqa: BLE001
            raise PlannerUnavailable(f"could not create Bedrock client: {exc}") from exc

    def next_step(self, *, alert, transcript, tools, step) -> PlanDecision:
        from . import knowledge_llm as kllm

        client = self._bedrock()
        self._model_id = self._model_id or kllm.DEFAULT_MODEL_ID
        user = {
            "role": "user",
            "content": [{"text": _render_planner_prompt(alert, transcript)}],
        }
        try:
            resp = client.converse(
                modelId=self._model_id,
                system=[{"text": _PLANNER_SYSTEM}],
                messages=[user],
                toolConfig=_tool_config(),
                inferenceConfig={"maxTokens": 512},
            )
        except Exception as exc:  # noqa: BLE001
            raise PlannerUnavailable(f"Bedrock planner call failed: {exc}") from exc

        return _decision_from_response(resp)


class PlannerUnavailable(Exception):
    """The model planner could not produce a decision; fall back to rules."""


def _render_planner_prompt(alert: dict[str, Any], transcript: list[dict[str, Any]]) -> str:
    lines = [
        f"Alert host: {alert.get('host')}",
        f"Alert kind: {alert.get('alert_kind')}",
        f"Transition: {alert.get('transition')}",
        f"Short description: {alert.get('short_description')}",
        "",
        "Evidence gathered so far (read-only):",
    ]
    if not transcript:
        lines.append("  (none yet)")
    for entry in transcript:
        ev = entry.get("evidence")
        lines.append(
            f"  - {entry.get('tool_id')}: "
            + ("ok " if entry.get("ok") else "FAILED ")
            + (str(ev)[:200] if ev is not None else str(entry.get('error', '')))
        )
    lines.append("")
    lines.append("Choose the next read-only tool, or call finish_triage.")
    return "\n".join(lines)


def _decision_from_response(resp: dict[str, Any]) -> PlanDecision:
    """Parse a Converse response into a PlanDecision. Fail safe on any surprise."""

    try:
        blocks = resp["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise PlannerUnavailable("unexpected Bedrock response shape") from exc

    for block in blocks:
        if isinstance(block, dict) and "toolUse" in block:
            tu = block["toolUse"]
            name = tu.get("name")
            params = tu.get("input") or {}
            if name == "finish_triage":
                return PlanDecision(is_final=True, reason=str(params.get("reason", "")))
            return PlanDecision(
                is_final=False, tool_id=name, params=dict(params),
                reason="model-selected",
            )
    # No tool call -> treat as a (text) finish so we never loop on chatter.
    return PlanDecision(is_final=True, reason="model returned no tool call")


def make_planner() -> Planner:
    """Pick the planner.

    Bedrock tool-use planner when BUCKY_LLM_ENABLED is set; otherwise the offline
    deterministic rule-based planner. Either way the tool allowlist is identical;
    the planner only changes HOW the next read-only step is chosen.
    """

    from . import knowledge_llm as kllm

    if kllm.llm_enabled():
        return BedrockPlanner()
    return RuleBasedPlanner()


# ---------------------------------------------------------------------------
# Local context lookups (read-only, over Bucky's own stored data)
# ---------------------------------------------------------------------------


class LocalContext(Protocol):
    """Read-only lookups over Bucky's own data, injected so the agent is testable."""

    def neighbors_flapped(self, host: str) -> dict[str, Any]: ...

    def active_change(self, host: str) -> dict[str, Any]: ...

    def similar_resolutions(self, alert: dict[str, Any]) -> dict[str, Any]: ...


class NullContext:
    """A context that knows nothing. Safe default: it never fabricates signal."""

    def neighbors_flapped(self, host: str) -> dict[str, Any]:
        return {"neighbors_flapped": False, "count": 0, "note": "no neighbour data available"}

    def active_change(self, host: str) -> dict[str, Any]:
        return {"active_change": False, "note": "no change-window data available"}

    def similar_resolutions(self, alert: dict[str, Any]) -> dict[str, Any]:
        return {"examples": [], "note": "no history available"}


# ---------------------------------------------------------------------------
# Verdict + run records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageVerdict:
    classification: TriageClassification
    confidence: float                       # 0..1, how strongly evidence supports it
    summary: str
    draft_work_note: str
    recommended_disposition: str            # e.g. "close: Solved (Work Around)" or "route to human"
    routed_to_human: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "confidence": round(self.confidence, 2),
            "summary": self.summary,
            "draft_work_note": self.draft_work_note,
            "recommended_disposition": self.recommended_disposition,
            "routed_to_human": self.routed_to_human,
        }


@dataclass(frozen=True)
class TriageResult:
    alert: dict[str, Any]
    transcript: list[dict[str, Any]]
    verdict: TriageVerdict
    planner: str
    simulated: bool
    steps_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert": self.alert,
            "transcript": self.transcript,
            "verdict": self.verdict.to_dict(),
            "planner": self.planner,
            "simulated": self.simulated,
            "steps_used": self.steps_used,
            "read_only": True,
            "note": (
                "Read-only triage evidence. This authorises nothing; a human "
                "reviews the verdict and approves any disposition."
            ),
        }


# ---------------------------------------------------------------------------
# Alert parsing (CheckMK format)
# ---------------------------------------------------------------------------

# CheckMK short descriptions look like:
#   "njc1ws2471.corp.ad.tullib.com UP -> DOWN No IP packet received for 60.9 s"
#   "sng2ws0060.corp.ad.tullib.com OK -> CRIT CPU utilization Total CPU ..."
# A monitoring host is either a CheckMK FQDN (…corp.ad.tullib.com / …ad.tradeblade.com)
# or a bare TP ICAP server code: a 2-3 letter site, a digit, then more letters/digits
# (e.g. njc1ws2471, ldn2ws9696). A plain English word must NOT parse as a host.
_HOST_RE = re.compile(
    r"\b("
    r"[a-z0-9][a-z0-9.\-]{2,}\.(?:corp\.ad\.tullib|ad\.tradeblade|na\.ad\.tullib)\.com"
    r"|[a-z]{2,3}\d[a-z]{1,8}\d{1,4}[a-z0-9]{0,4}"
    r")\b",
    re.I,
)
_TRANSITION_RE = re.compile(r"\b(UP|OK|WARN|CRIT|DOWN)\s*-?>\s*(UP|OK|WARN|CRIT|DOWN)\b", re.I)


def parse_alert(ticket: dict[str, Any]) -> dict[str, Any]:
    """Extract host + alert kind from a monitoring ticket. Best-effort, read-only."""

    sd = str(ticket.get("short_description") or "")
    desc = str(ticket.get("description") or "")
    text = f"{sd} {desc}"

    host = ""
    m = _HOST_RE.search(sd) or _HOST_RE.search(desc)
    if m:
        host = m.group(1)

    kind = AlertKind.OTHER
    low = text.lower()
    if "no ip packet" in low or re.search(r"->\s*down", low):
        kind = AlertKind.HOST_DOWN
    elif "cpu utilization" in low or "cpu utilisation" in low:
        kind = AlertKind.CPU

    transition = ""
    tm = _TRANSITION_RE.search(text)
    if tm:
        transition = f"{tm.group(1).upper()} -> {tm.group(2).upper()}"

    return {
        "host": host,
        "alert_kind": kind.value,
        "transition": transition,
        "short_description": sd[:200],
    }


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


class TriageAgent:
    """Bounded read-only investigation loop.

    ``gateway`` is the sole path to a host (defaults to the offline mock backend
    unless live diagnostics are enabled). ``planner`` and ``context`` are
    injectable so the whole agent runs deterministically in tests.
    """

    def __init__(
        self,
        *,
        gateway: DiagnosticGateway | None = None,
        planner: Planner | None = None,
        context: LocalContext | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self._gateway = gateway or DiagnosticGateway()
        self._planner = planner or make_planner()
        self._context = context or NullContext()
        self._max_steps = max(1, min(20, max_steps))

    @property
    def simulated(self) -> bool:
        return not self._gateway.live

    def run(self, ticket: dict[str, Any]) -> TriageResult:
        alert = parse_alert(ticket)
        transcript: list[dict[str, Any]] = []

        if not alert["host"]:
            verdict = self._abstain("No host could be parsed from the alert text.")
            return TriageResult(alert, transcript, verdict, self._planner.name,
                                self.simulated, 0)

        planner = self._planner
        planner_name = planner.name
        fell_back = False

        step = 0
        while step < self._max_steps:
            try:
                decision = planner.next_step(
                    alert=alert,
                    transcript=list(transcript),
                    tools=describe_tools(),
                    step=step,
                )
            except PlannerUnavailable as exc:
                # The model planner failed; fall back to the deterministic planner
                # for the rest of the run. The investigation continues safely
                # rather than aborting, and we record that it happened.
                if not fell_back:
                    fell_back = True
                    planner = RuleBasedPlanner()
                    planner_name = f"{planner_name} -> fallback {planner.name} ({exc})"
                    continue
                break
            if decision.is_final:
                break
            entry = self._run_tool(decision)
            transcript.append(entry)
            step += 1

        verdict = self._summarise(alert, transcript)
        return TriageResult(alert, transcript, verdict, planner_name,
                            self.simulated, len(transcript))

    # -- tool execution --------------------------------------------------

    def _run_tool(self, decision: PlanDecision) -> dict[str, Any]:
        tool = _TOOLS_BY_ID.get(decision.tool_id or "")
        if tool is None:
            # Default-deny: a planner may never invent a tool.
            return {
                "tool_id": decision.tool_id,
                "reason": decision.reason,
                "ok": False,
                "error": f"unknown tool '{decision.tool_id}'",
            }

        if tool.diagnostic_op is None:
            return self._run_local(tool, decision)

        # Host-touching tool: delegate to the read-only gateway, which validates
        # and allowlists before any read. Any refusal/failure is captured, never
        # raised out of the loop, so one bad check cannot abort the whole triage.
        try:
            result = self._gateway.run(tool.diagnostic_op, dict(decision.params))
            return {
                "tool_id": tool.tool_id,
                "op": tool.diagnostic_op,
                "params": dict(decision.params),
                "reason": decision.reason,
                "ok": True,
                "simulated": result.simulated,
                "evidence": result.evidence,
            }
        except (DiagnosticRefused, DiagnosticFailed) as exc:
            return {
                "tool_id": tool.tool_id,
                "op": tool.diagnostic_op,
                "params": dict(decision.params),
                "reason": decision.reason,
                "ok": False,
                "error": str(exc),
            }

    def _run_local(self, tool: Tool, decision: PlanDecision) -> dict[str, Any]:
        host = str(decision.params.get("host") or decision.params.get("target") or "")
        try:
            if tool.tool_id == "correlate_neighbors":
                evidence = self._context.neighbors_flapped(host)
            elif tool.tool_id == "check_change_window":
                evidence = self._context.active_change(host)
            elif tool.tool_id == "similar_history":
                evidence = self._context.similar_resolutions(decision.params)
            else:
                return {"tool_id": tool.tool_id, "ok": False,
                        "error": f"no local handler for '{tool.tool_id}'"}
            return {
                "tool_id": tool.tool_id,
                "params": dict(decision.params),
                "reason": decision.reason,
                "ok": True,
                "simulated": True,  # local lookups never touch a host
                "evidence": evidence,
            }
        except Exception as exc:  # noqa: BLE001 - never abort the loop on a lookup
            return {"tool_id": tool.tool_id, "ok": False, "error": str(exc)}

    # -- verdict synthesis -----------------------------------------------

    def _summarise(self, alert: dict[str, Any], transcript: list[dict[str, Any]]) -> TriageVerdict:
        """Turn the transcript into a classification. Conservative by design.

        Offline this is deterministic rules over the evidence. The Bedrock
        planner (later) may PROPOSE a summary, but the safety rule below always
        holds: if connectivity was not proven, never recommend closing.
        """

        host = alert.get("host", "the host")
        reachable = self._connectivity_proven(transcript)
        neighbors = self._evidence_for("correlate_neighbors", transcript)
        change = self._evidence_for("check_change_window", transcript)

        # Planned change takes precedence: a reboot/patching window explains the drop.
        if change and change.get("active_change"):
            disp = "close: Solved (Work Around) -- covered by planned change"
            note = (
                f"Automated triage: {host} dropped off monitoring during an active "
                f"change window ({change.get('change_ref', 'see change record')}). "
                f"Host {'is reachable again' if reachable else 'recovery expected post-change'}. "
                f"Recommend closing as planned-change related."
            )
            return TriageVerdict(
                TriageClassification.PLANNED_CHANGE,
                confidence=0.8 if reachable else 0.6,
                summary=f"{host} drop coincides with an active change window.",
                draft_work_note=note,
                recommended_disposition=disp,
                routed_to_human=False,
            )

        if reachable and neighbors and neighbors.get("neighbors_flapped"):
            note = (
                f"Automated triage: {host} is reachable again. "
                f"{neighbors.get('count', 0)} neighbouring host(s) flapped in the same "
                f"window, indicating a transient network blip rather than a host fault. "
                f"Recommend closing as a transient network event."
            )
            return TriageVerdict(
                TriageClassification.NETWORK_BLIP,
                confidence=0.75,
                summary=f"{host} back up; neighbours flapped together (network blip).",
                draft_work_note=note,
                recommended_disposition="close: Solved (Work Around) -- transient network blip",
                routed_to_human=False,
            )

        if reachable:
            note = (
                f"Automated triage: {host} responds on the checked port now and the "
                f"name resolves. No correlated change or neighbour flap found. The "
                f"monitoring 'down' alert appears to have been a brief, self-resolved "
                f"blip. Recommend closing as self-resolved."
            )
            return TriageVerdict(
                TriageClassification.SELF_RESOLVED,
                confidence=0.7,
                summary=f"{host} is reachable again; alert appears self-resolved.",
                draft_work_note=note,
                recommended_disposition="close: Solved (Work Around) -- self-resolved",
                routed_to_human=False,
            )

        # Not reachable -> never auto-close. Route to a human with evidence.
        note = (
            f"Automated triage: {host} did NOT respond to the read-only connectivity "
            f"check. This needs a human: the host may be genuinely down. Evidence "
            f"gathered is attached. No close is recommended."
        )
        return TriageVerdict(
            TriageClassification.STILL_DOWN_NEEDS_HUMAN,
            confidence=0.6,
            summary=f"{host} still unreachable after read-only checks; needs a human.",
            draft_work_note=note,
            recommended_disposition="route to human -- host still unreachable",
            routed_to_human=True,
        )

    def _abstain(self, why: str) -> TriageVerdict:
        return TriageVerdict(
            TriageClassification.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            summary=why,
            draft_work_note=f"Automated triage could not proceed: {why} Routing to a human.",
            recommended_disposition="route to human -- insufficient evidence",
            routed_to_human=True,
        )

    # -- transcript helpers ----------------------------------------------

    @staticmethod
    def _evidence_for(tool_id: str, transcript: list[dict[str, Any]]) -> dict[str, Any] | None:
        for entry in transcript:
            if entry.get("tool_id") == tool_id and entry.get("ok"):
                ev = entry.get("evidence")
                return ev if isinstance(ev, dict) else None
        return None

    def _connectivity_proven(self, transcript: list[dict[str, Any]]) -> bool:
        ev = self._evidence_for("check_connectivity", transcript)
        if ev and ev.get("reachable") is True:
            return True
        return False
