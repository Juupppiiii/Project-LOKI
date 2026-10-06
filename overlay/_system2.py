"""Single place where the overlay reaches into System 2.

The System 2 modules use flat imports (`from schema import Procedure`), so the
`system2/` directory has to be on sys.path. Isolating that here keeps the hack
in one file instead of spreading sys.path edits through the codebase.

Nothing in this module executes anything. `executor.live_execute` is never
imported, so the overlay cannot reach real systems even by accident.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SYSTEM2 = _PROJECT_ROOT / "system2"

if str(_SYSTEM2) not in sys.path:
    sys.path.insert(0, str(_SYSTEM2))

# Imported after the path fix, hence the late imports.
import engine  # noqa: E402
import executor  # noqa: E402
import schema  # noqa: E402

Procedure = schema.Procedure
Step = schema.Step
Match = engine.Match

DEFAULT_PROCEDURES_PATH = _SYSTEM2 / "procedures.json"


def load_procedures(path: str | Path | None = None) -> list[Procedure]:
    return schema.load_procedures(Path(path) if path else DEFAULT_PROCEDURES_PATH)


def match_ticket(ticket: dict[str, Any], procedures: list[Procedure]):
    return engine.match_ticket(ticket, procedures)


def rank_matches(ticket: dict[str, Any], procedures: list[Procedure]):
    return engine.rank_matches(ticket, procedures)


def render_step(step: Step, params: dict[str, Any]) -> str:
    """Reuse System 2's renderer so there is one rendering implementation.

    Private by name only -- deliberately shared rather than duplicated, because
    the approval card and the runbook must show identical text.
    """

    return executor._render_step(step, params)


def fill(text: str | None, params: dict[str, Any]) -> str:
    return executor._fill(text, params)


def simulate_outcome(step: Step, params: dict[str, Any]) -> str:
    return executor._simulate_outcome(step, params)


def procedure_version(procedure: Procedure) -> str:
    """Content digest used as an immutable version.

    procedures.json carries no version field, so approvals bind to a digest of
    the procedure's content. Editing a procedure changes the digest, which
    invalidates every approval made against the old text.
    """

    payload = json.dumps(asdict(procedure), sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
