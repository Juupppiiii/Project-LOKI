"""Advisory efficiency intelligence.  RECOMMENDS, NEVER REORDERS.

This module answers "which procedure tends to resolve fastest, and how much of
it can the agent safely do?" so a human can prefer the more efficient option. It
is deliberately and strictly advisory:

* It never reorders, skips, or removes a procedure's steps. Step order and the
  risk/owner split are decided by risk.py and the planner, and this module does
  not touch them.
* It never changes which procedure the keyword matcher authoritatively picks.
  It only annotates and offers an alternative *ranking* for a human to read.

Two signals, both grounded in data we already have:

1. **Historical resolution time** per assignment group, from the closed-ticket
   export (data/closed_incidents.csv). Prefer ServiceNow's own calendar
   seconds-to-close; fall back to resolved_at - sys_created_on.

2. **Automatability share** per procedure: of its steps, how many are
   agent-eligible (R0/R1/R2 owned by the AI) vs human-only -- computed by
   reusing risk.classify, so it matches exactly what the planner will decide.

Honesty note built in: a large share of this instance's closed incidents are
machine auto-acknowledgements that "resolve" in seconds (the Liquidnet NOC
feed). Those make a raw mean meaningless, so the stats report a median, a
trimmed sample that drops implausibly short auto-resolves, and the sample size,
and they say when a group's data is dominated by auto-acks.
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import risk as risk_policy


def _raise_csv_field_limit() -> None:
    """ServiceNow exports' work_notes can exceed Python's 131072-char cap."""

    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _PROJECT_ROOT / "data" / "closed_incidents.csv"

# Resolutions faster than this are treated as auto-acknowledgements, not real
# human work, and are excluded from the "typical" (trimmed) figure.
AUTO_ACK_SECONDS = int(os.getenv("LOKI_EFFICIENCY_AUTOACK_SECONDS", "120"))


def efficiency_enabled() -> bool:
    return os.getenv("LOKI_EFFICIENCY", "1").strip().lower() not in (
        "0", "false", "no", "",
    )


# ---------------------------------------------------------------------------
# Resolution time per ticket
# ---------------------------------------------------------------------------


def _resolution_seconds(row: dict[str, str]) -> float | None:
    """Seconds to resolve. Prefer calendar_stc; fall back to timestamp delta."""

    stc = str(row.get("calendar_stc", "") or "").strip()
    if stc.isdigit():
        secs = int(stc)
        if secs > 0:
            return float(secs)

    created = str(row.get("sys_created_on", "") or "").strip()
    resolved = str(row.get("resolved_at", "") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            c = datetime.strptime(created, fmt)
            r = datetime.strptime(resolved, fmt)
            delta = (r - c).total_seconds()
            return delta if delta >= 0 else None
        except (ValueError, TypeError):
            continue
    return None


@dataclass
class GroupEfficiency:
    group: str
    sample_size: int
    median_seconds: float
    typical_seconds: float       # median of the trimmed (non-auto-ack) sample
    trimmed_size: int
    auto_ack_share: float        # fraction of the sample that was an auto-ack

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "sample_size": self.sample_size,
            "median_seconds": round(self.median_seconds, 1),
            "median_human": _human_duration(self.median_seconds),
            "typical_seconds": round(self.typical_seconds, 1),
            "typical_human": _human_duration(self.typical_seconds),
            "trimmed_size": self.trimmed_size,
            "auto_ack_share": round(self.auto_ack_share, 2),
            "note": (
                "Dominated by machine auto-acknowledgements; treat the median "
                "with caution."
                if self.auto_ack_share >= 0.5
                else "Based on human-resolved tickets where available."
            ),
        }


def _human_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{round(s / 60)}m"
    if s < 172800:
        return f"{round(s / 3600, 1)}h"
    return f"{round(s / 86400, 1)}d"


class EfficiencyIndex:
    """Per-group resolution-time stats, loaded once from the closed-ticket data."""

    def __init__(self, dataset: Path | None = None) -> None:
        self.dataset = Path(dataset) if dataset else DEFAULT_DATASET
        self._by_group: dict[str, GroupEfficiency] = {}
        self.loaded = False
        self.rows_seen = 0

    def load(self) -> "EfficiencyIndex":
        if not self.dataset.exists():
            self.loaded = False
            return self
        _raise_csv_field_limit()
        samples: dict[str, list[float]] = {}
        with self.dataset.open(newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                self.rows_seen += 1
                group = str(row.get("assignment_group", "") or "").strip()
                if not group:
                    continue
                secs = _resolution_seconds(row)
                if secs is None:
                    continue
                samples.setdefault(group, []).append(secs)

        for group, vals in samples.items():
            if not vals:
                continue
            trimmed = [v for v in vals if v >= AUTO_ACK_SECONDS]
            auto_ack_share = 1.0 - (len(trimmed) / len(vals))
            self._by_group[group] = GroupEfficiency(
                group=group,
                sample_size=len(vals),
                median_seconds=statistics.median(vals),
                typical_seconds=statistics.median(trimmed) if trimmed else statistics.median(vals),
                trimmed_size=len(trimmed),
                auto_ack_share=auto_ack_share,
            )
        self.loaded = True
        return self

    def for_group(self, group: str) -> GroupEfficiency | None:
        if not group:
            return None
        return self._by_group.get(group.strip())


# ---------------------------------------------------------------------------
# Automatability of a procedure (reuses the real risk classifier)
# ---------------------------------------------------------------------------


def automatability(procedure: Any) -> dict[str, Any]:
    """Agent-vs-human breakdown of a procedure's steps.

    Reuses risk.classify so this matches exactly what the planner will decide;
    it does not invent a second classification.
    """

    from .domain import Owner

    total = len(getattr(procedure, "steps", []) or [])
    if total == 0:
        return {"total_steps": 0, "agent_steps": 0, "human_steps": 0, "agent_share": 0.0}

    agent = 0
    for step in procedure.steps:
        _risk, owner, _policy, _reason = risk_policy.classify(step)
        if owner is Owner.AI:
            agent += 1
    return {
        "total_steps": total,
        "agent_steps": agent,
        "human_steps": total - agent,
        "agent_share": round(agent / total, 2),
    }


# ---------------------------------------------------------------------------
# Composite advisory ranking score
# ---------------------------------------------------------------------------


def efficiency_profile(procedure: Any, index: EfficiencyIndex) -> dict[str, Any]:
    """An advisory efficiency profile for one procedure.

    Combines historical resolution time (lower is better) and automatability
    (higher agent share is better) into a single advisory score in [0, 1].
    Higher = recommended for efficiency. Carries its inputs so a human sees
    *why*, not just a number.
    """

    auto = automatability(procedure)
    group = getattr(getattr(procedure, "trigger", None), "assignment_group", "") or ""
    stats = index.for_group(group) if index.loaded else None

    # Automatability component: directly the agent share (0..1).
    autom_component = auto["agent_share"]

    # Speed component: map typical resolution time to 0..1 (faster -> higher).
    # 1 minute or less -> ~1.0; a full day -> ~0.0. Log-ish bucketing keeps it
    # readable rather than precise -- this is advice, not an SLA.
    if stats is not None:
        t = max(1.0, stats.typical_seconds)
        if t <= 60:
            speed_component = 1.0
        elif t >= 86400:
            speed_component = 0.0
        else:
            import math
            speed_component = 1.0 - (math.log(t / 60) / math.log(86400 / 60))
            speed_component = max(0.0, min(1.0, speed_component))
    else:
        speed_component = None

    if speed_component is None:
        score = round(autom_component, 3)
        basis = "automatability only (no resolution-time data for this group)"
    else:
        score = round(0.5 * autom_component + 0.5 * speed_component, 3)
        basis = "automatability + historical resolution time"

    return {
        "advisory": True,
        "efficiency_score": score,
        "basis": basis,
        "automatability": auto,
        "resolution_history": stats.to_dict() if stats else None,
    }


_INDEX: EfficiencyIndex | None = None


def shared_index() -> EfficiencyIndex:
    """Process-wide index, loaded once. Reloadable by clearing _INDEX."""

    global _INDEX
    if _INDEX is None:
        _INDEX = EfficiencyIndex().load()
    return _INDEX
