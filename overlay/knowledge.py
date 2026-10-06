"""Parse uploaded handbooks and dictionaries into reviewable drafts.

Design rules, in priority order:

1. **An upload never changes behaviour.** Parsing produces drafts. Publishing is
   a separate, human act. This mirrors the review gate System 2 already had for
   auto-drafted procedures.
2. **Parsing is honest about ambiguity.** Free-form prose cannot be turned into
   executable steps reliably, so the parsers accept documented structures and
   report what they could not understand instead of guessing.
3. **An upload cannot quietly grant itself execution rights.** A file may
   *declare* a low risk on a step, but that claim is surfaced as a warning for
   the reviewer. Nothing is agent-executable until a human publishes it.

Supported formats

| Kind       | Formats                | Notes |
|------------|------------------------|-------|
| handbook   | json, yaml, csv, md    | json/yaml are exact; csv is tabular; md is best-effort |
| dictionary | json, yaml, csv, txt   | canonical term plus aliases |
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from . import _system2
from . import risk as risk_policy
from .domain import (
    DictionaryEntry,
    DraftStatus,
    ProcedureDraft,
    RiskClass,
    new_id,
)

try:  # optional: only needed for .yaml/.yml uploads
    import yaml

    _YAML = True
except ImportError:  # pragma: no cover
    _YAML = False


SUPPORTED_HANDBOOK = {"json", "yaml", "yml", "csv", "md", "markdown"}
SUPPORTED_DICTIONARY = {"json", "yaml", "yml", "csv", "txt"}

# Risk levels an upload may claim that would make a step agent-executable. When
# a file claims one of these we flag it, because that is exactly the path a
# careless or malicious handbook would use to get a destructive step automated.
_ESCALATING_RISKS = {"R0", "R1", "R2"}

# Actions whose safety cannot be inferred, so a claimed low risk deserves a
# second look from the reviewer.
_UNVERIFIABLE_ACTIONS = {"run_command", "reboot", "delete_profile"}


class ParseError(Exception):
    """The file could not be understood at all."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def detect_format(filename: str) -> str:
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return ext or "txt"


def decode(raw: bytes) -> str:
    """Best-effort text decode. Exported so callers can store the text too."""

    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# Kept for internal call sites.
_decode = decode


def _split_list(value: Any) -> list[str]:
    """Accept a list, or a string separated by | ; or comma."""

    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value in (None, ""):
        return []
    return [p.strip() for p in re.split(r"[|;,]", str(value)) if p.strip()]


def _load_structured(text: str, fmt: str) -> Any:
    if fmt in ("yaml", "yml"):
        if not _YAML:
            raise ParseError(
                "YAML uploads need PyYAML installed; convert the file to JSON instead"
            )
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"invalid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# handbook -> procedure drafts
# ---------------------------------------------------------------------------


def _normalise_procedure(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a loose procedure dict into the System 2 schema shape."""

    trigger = raw.get("trigger") or {}
    if not isinstance(trigger, dict):
        trigger = {}

    keywords = _split_list(
        trigger.get("keywords")
        if trigger.get("keywords") is not None
        else raw.get("keywords")
    )
    group = (
        trigger.get("assignment_group")
        or raw.get("assignment_group")
        or raw.get("group")
    )

    steps_in = raw.get("steps") or []
    steps: list[dict[str, Any]] = []
    for s in steps_in:
        if isinstance(s, str):
            # A bare string is treated as human work; we cannot infer an action.
            steps.append({"action": "manual", "description": s})
            continue
        step = {
            "action": str(s.get("action") or "manual").strip(),
            "description": str(s.get("description") or s.get("desc") or "").strip(),
        }
        for key in ("command", "target", "service", "expect", "rollback"):
            if s.get(key):
                step[key] = str(s[key]).strip()
        # Optional explicit classification, honoured by risk.py.
        if s.get("risk"):
            step["risk"] = str(s["risk"]).strip().upper()
        if s.get("owner"):
            step["owner"] = str(s["owner"]).strip().lower()
        steps.append(step)

    params_in = raw.get("parameters") or raw.get("params") or []
    parameters: list[dict[str, Any]] = []
    for p in params_in:
        if isinstance(p, str):
            parameters.append({"name": p, "type": "string"})
            continue
        parameters.append(
            {
                "name": str(p.get("name") or "").strip(),
                "type": str(p.get("type") or "string").strip(),
                "source": p.get("source"),
                "required": bool(p.get("required", True)),
                "description": str(p.get("description") or "").strip(),
            }
        )

    procedure_id = str(
        raw.get("procedure_id") or raw.get("id") or raw.get("name") or ""
    ).strip()

    return {
        "procedure_id": procedure_id,
        "name": str(raw.get("name") or procedure_id).strip(),
        "trigger": {"keywords": keywords, "assignment_group": group},
        "parameters": [p for p in parameters if p.get("name")],
        "steps": steps,
        "automatability": str(raw.get("automatability") or "medium").strip(),
        "frequency": int(raw.get("frequency") or 0),
        "source_tickets": _split_list(raw.get("source_tickets")),
        "notes": str(raw.get("notes") or "").strip(),
    }


def _parse_handbook_structured(text: str, fmt: str) -> list[dict[str, Any]]:
    data = _load_structured(text, fmt)

    if isinstance(data, dict):
        raw_list = data.get("procedures")
        if raw_list is None:
            raw_list = [data]  # a single procedure document
    elif isinstance(data, list):
        raw_list = data
    else:
        raise ParseError("expected an object or a list of procedures")

    if not isinstance(raw_list, list):
        raise ParseError("'procedures' must be a list")

    return [_normalise_procedure(r) for r in raw_list if isinstance(r, dict)]


def _parse_handbook_csv(text: str) -> list[dict[str, Any]]:
    """One row per step, grouped by procedure_id.

    Recognised columns (extras ignored):
      procedure_id, name, assignment_group, keywords,
      step (order), action, description, command, target, service, expect,
      rollback, risk, owner, parameters
    """

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ParseError("CSV has no header row")

    lower = {(f or "").strip().lower(): (f or "") for f in reader.fieldnames}
    if "procedure_id" not in lower and "name" not in lower:
        raise ParseError(
            "CSV needs at least a 'procedure_id' or 'name' column; "
            f"found: {', '.join(reader.fieldnames)}"
        )

    def cell(row: dict[str, Any], key: str) -> str:
        src = lower.get(key)
        return str(row.get(src, "") or "").strip() if src else ""

    grouped: dict[str, dict[str, Any]] = {}
    order: dict[str, list[tuple[float, dict[str, Any]]]] = {}

    for i, row in enumerate(reader):
        pid = cell(row, "procedure_id") or cell(row, "name")
        if not pid:
            continue

        proc = grouped.setdefault(
            pid,
            {
                "procedure_id": pid,
                "name": cell(row, "name") or pid,
                "keywords": cell(row, "keywords"),
                "assignment_group": cell(row, "assignment_group"),
                "parameters": [],
                "steps": [],
                "notes": cell(row, "notes"),
            },
        )
        # Later rows may carry the metadata; keep the first non-empty value.
        for key in ("keywords", "assignment_group"):
            if not proc[key]:
                proc[key] = cell(row, key)

        for pname in _split_list(cell(row, "parameters")):
            if pname not in [p["name"] for p in proc["parameters"]]:
                ptype = (
                    "hostname"
                    if pname in ("server", "host", "hostname")
                    else "service"
                    if pname in ("service", "service_name")
                    else "string"
                )
                proc["parameters"].append({"name": pname, "type": ptype})

        action = cell(row, "action")
        description = cell(row, "description")
        if not action and not description:
            continue

        step: dict[str, Any] = {
            "action": action or "manual",
            "description": description,
        }
        for key in ("command", "target", "service", "expect", "rollback", "risk", "owner"):
            if cell(row, key):
                step[key] = cell(row, key)

        try:
            seq = float(cell(row, "step") or i)
        except ValueError:
            seq = float(i)
        order.setdefault(pid, []).append((seq, step))

    for pid, steps in order.items():
        grouped[pid]["steps"] = [s for _, s in sorted(steps, key=lambda x: x[0])]

    if not grouped:
        raise ParseError("no procedures found in CSV")

    return [_normalise_procedure(p) for p in grouped.values()]


# Markdown handbook: a heading starts a procedure, `key: value` lines set
# metadata, and an ordered/bulleted list under a "steps" heading becomes steps.
_MD_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
_MD_META = re.compile(r"^\s*[-*]?\s*(keywords|group|assignment_group|notes|"
                      r"automatability|parameters|params)\s*:\s*(.+?)\s*$", re.I)
_MD_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")
# `notes: |` opens a block whose indented lines are prose, not steps.
_MD_BLOCK_START = re.compile(
    r"^\s*(keywords|group|assignment_group|notes|automatability|parameters|params)"
    r"\s*:\s*\|\s*$",
    re.I,
)
_MD_STEP_FIELDS = re.compile(r"\b(action|command|target|service|expect|risk|owner)\s*[:=]\s*", re.I)


def _parse_step_line(line: str) -> dict[str, Any]:
    """Parse `action | description | command: ...` or plain prose.

    Pipes are the field separator, which collides with shell pipes inside a
    command. Linux runbooks are full of `df -h | head`, so once a field like
    `command:` starts, the remainder of the line is taken verbatim -- including
    any further pipes -- except for a trailing `risk:` / `owner:` marker, which
    stays usable after a piped command.
    """

    known = _system2.__dict__.get("schema")
    valid_actions = getattr(known, "KNOWN_ACTIONS", None) if known else None
    if not valid_actions:
        valid_actions = {
            "connect", "run_command", "restart_service", "reboot", "check",
            "delete_profile", "notify", "manual",
        }

    parts = [p.strip() for p in line.split("|")]
    step: dict[str, Any] = {}

    if parts and parts[0].lower() in valid_actions:
        step["action"] = parts[0].lower()
        parts = parts[1:]

    described = False
    i = 0
    while i < len(parts):
        chunk = parts[i]
        m = _MD_STEP_FIELDS.match(chunk)

        if m:
            key = m.group(1).lower()
            value = chunk[m.end():].strip()

            # Value-bearing fields may legitimately contain pipes. Re-join the
            # remaining chunks, stopping if one of them opens a new field.
            if key in ("command", "expect", "target", "service"):
                j = i + 1
                while j < len(parts):
                    nxt = _MD_STEP_FIELDS.match(parts[j])
                    if nxt and nxt.group(1).lower() in (
                        "risk", "owner", "action", "command", "expect",
                        "target", "service",
                    ):
                        break
                    value = f"{value} | {parts[j]}".strip()
                    j += 1
                i = j - 1

            step[key] = value.upper() if key == "risk" else value
            i += 1
            continue

        if chunk:
            if not described:
                step["description"] = chunk
                described = True
            else:
                step["description"] = f"{step.get('description', '')} {chunk}".strip()
        i += 1

    step.setdefault("action", "manual")
    step.setdefault("description", line.strip())
    return step


def _parse_handbook_markdown(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    procedures: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_steps = False

    lines = text.splitlines()
    idx = 0

    while idx < len(lines):
        raw_line = lines[idx]
        idx += 1
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # YAML-style block scalar: `notes: |` followed by indented lines. Without
        # this, a numbered list inside the prose gets read as procedure steps.
        block = _MD_BLOCK_START.match(line)
        if block and current is not None:
            key = block.group(1).lower()
            collected: list[str] = []
            while idx < len(lines):
                nxt = lines[idx]
                if nxt.strip() and not nxt[:1].isspace():
                    break  # dedent ends the block
                collected.append(nxt.strip())
                idx += 1
            value = " ".join(c for c in collected if c).strip()
            if key in ("group", "assignment_group"):
                current["assignment_group"] = value
            else:
                current[key] = value
            continue

        heading = _MD_HEADING.match(line)
        if heading:
            title = heading.group(2).strip()
            if re.fullmatch(r"steps?", title, re.I):
                in_steps = True
                continue

            # A new procedure starts here.
            title = re.sub(r"^(procedure|runbook|handbook)\s*[:\-]\s*", "", title, flags=re.I)
            current = {
                "procedure_id": re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                "name": title,
                "keywords": "",
                "assignment_group": "",
                "parameters": [],
                "steps": [],
                "notes": "",
            }
            procedures.append(current)
            in_steps = False
            continue

        if current is None:
            continue

        meta = _MD_META.match(line)
        if meta:
            key = meta.group(1).lower()
            value = meta.group(2).strip()
            if key in ("group", "assignment_group"):
                current["assignment_group"] = value
            elif key in ("parameters", "params"):
                for pname in _split_list(value):
                    ptype = (
                        "hostname" if pname in ("server", "host", "hostname")
                        else "service" if pname in ("service", "service_name")
                        else "string"
                    )
                    current["parameters"].append({"name": pname, "type": ptype})
            else:
                current[key] = value
            continue

        step_match = _MD_STEP.match(line)
        if step_match:
            current["steps"].append(_parse_step_line(step_match.group(1)))
            in_steps = True
            continue

        # Prose inside a procedure: keep it as notes rather than inventing a step.
        if not in_steps:
            current["notes"] = f"{current['notes']} {line.strip()}".strip()

    if not procedures:
        raise ParseError(
            "no headings found. A Markdown handbook needs a heading per "
            "procedure (for example '## Restart a Windows service')"
        )

    for p in procedures:
        if not p["steps"]:
            warnings.append(
                f"'{p['name']}' has no recognisable steps; add a numbered or "
                "bulleted list under the heading"
            )
        if not p["keywords"]:
            warnings.append(
                f"'{p['name']}' has no 'keywords:' line, so it will rarely match"
            )

    return [_normalise_procedure(p) for p in procedures], warnings


def parse_handbook(
    raw: bytes, filename: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse an uploaded handbook into normalised procedure dicts."""

    fmt = detect_format(filename)
    if fmt not in SUPPORTED_HANDBOOK:
        raise ParseError(
            f"unsupported handbook format '.{fmt}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_HANDBOOK))}"
        )

    text = _decode(raw)
    if not text.strip():
        raise ParseError("the file is empty")

    if fmt in ("md", "markdown"):
        return _parse_handbook_markdown(text)
    if fmt == "csv":
        return _parse_handbook_csv(text), []
    return _parse_handbook_structured(text, fmt), []


# ---------------------------------------------------------------------------
# validation + risk review
# ---------------------------------------------------------------------------


def validate_procedure(payload: dict[str, Any]) -> list[str]:
    """Validate against the real System 2 schema, so publish cannot break load."""

    errors: list[str] = []

    if not payload.get("procedure_id"):
        errors.append("missing procedure_id")
    if not payload.get("steps"):
        errors.append("no steps")
    if not (payload.get("trigger") or {}).get("keywords"):
        errors.append("no trigger keywords, so the procedure can never match")

    try:
        proc = _system2.Procedure.from_dict(payload)
        errors.extend(proc.validate())
    except Exception as exc:
        errors.append(f"does not fit the procedure schema: {exc}")

    # Parameters referenced by steps must exist, or rendering shows <param?>.
    declared = {p.get("name") for p in payload.get("parameters", [])}
    referenced: set[str] = set()
    for step in payload.get("steps", []):
        for field in ("command", "target", "service", "expect", "description"):
            referenced.update(re.findall(r"\{(\w+)\}", str(step.get(field) or "")))
    for missing in sorted(referenced - declared):
        errors.append(f"step uses {{{missing}}} but no such parameter is declared")

    return errors


def review_risk(payload: dict[str, Any]) -> list[str]:
    """Flag claims that would make a step agent-executable.

    Publishing is a human decision; this makes sure the human sees the part that
    matters instead of skimming past it.
    """

    warnings: list[str] = []

    for i, step in enumerate(payload.get("steps", []), start=1):
        action = str(step.get("action") or "").strip()
        claimed = str(step.get("risk") or "").strip().upper()

        if claimed and claimed in _ESCALATING_RISKS and action in _UNVERIFIABLE_ACTIONS:
            warnings.append(
                f"step {i} ({action}) claims risk {claimed}, which would let the "
                f"agent run it. Confirm the command is genuinely safe and "
                f"reversible before publishing."
            )

        if action == "run_command" and not claimed:
            # Not a problem, just worth stating plainly.
            warnings.append(
                f"step {i} is run_command with no risk declared, so it stays "
                f"human-only (RX)."
            )

        if claimed and claimed not in {r.value for r in RiskClass}:
            warnings.append(
                f"step {i} declares unknown risk '{claimed}'; it will be treated "
                f"as unclassified (human-only)."
            )

    return warnings


def summarise_effect(payload: dict[str, Any]) -> dict[str, Any]:
    """What this procedure would mean in practice, for the review screen."""

    try:
        proc = _system2.Procedure.from_dict(payload)
    except Exception:
        return {"steps": 0, "agent_steps": 0, "human_steps": 0, "breakdown": []}

    breakdown = []
    agent = 0
    for i, step in enumerate(proc.steps, start=1):
        risk, owner, policy, reason = risk_policy.classify(step)
        if owner.value == "ai" and policy.value != "manual_only":
            agent += 1
        breakdown.append(
            {
                "sequence": i,
                "action": step.action,
                "description": step.description,
                "risk": risk.value,
                "risk_label": risk.label,
                "owner": owner.value,
                "policy": policy.value,
                "reason": reason,
            }
        )

    return {
        "steps": len(proc.steps),
        "agent_steps": agent,
        "human_steps": len(proc.steps) - agent,
        "breakdown": breakdown,
    }


def build_drafts(
    source_id: str, procedures: list[dict[str, Any]]
) -> list[ProcedureDraft]:
    drafts: list[ProcedureDraft] = []
    for payload in procedures:
        drafts.append(
            ProcedureDraft(
                draft_id=new_id("drf"),
                source_id=source_id,
                procedure_id=payload.get("procedure_id") or "(unnamed)",
                name=payload.get("name") or payload.get("procedure_id") or "(unnamed)",
                payload=payload,
                status=DraftStatus.DRAFT,
                validation_errors=validate_procedure(payload),
                risk_warnings=review_risk(payload),
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# dictionary
# ---------------------------------------------------------------------------


def parse_dictionary(
    raw: bytes, filename: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a dictionary upload into {canonical, aliases, note} records."""

    fmt = detect_format(filename)
    if fmt not in SUPPORTED_DICTIONARY:
        raise ParseError(
            f"unsupported dictionary format '.{fmt}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_DICTIONARY))}"
        )

    text = _decode(raw)
    if not text.strip():
        raise ParseError("the file is empty")

    warnings: list[str] = []
    entries: list[dict[str, Any]] = []

    if fmt in ("json", "yaml", "yml"):
        data = _load_structured(text, fmt)
        if isinstance(data, dict):
            # Either {"entries": [...]} or {"canonical": ["alias", ...]}
            if isinstance(data.get("entries"), list):
                items = data["entries"]
            else:
                items = [
                    {"canonical": k, "aliases": _split_list(v)}
                    for k, v in data.items()
                ]
        elif isinstance(data, list):
            items = data
        else:
            raise ParseError("expected an object or list")

        for item in items:
            if not isinstance(item, dict):
                continue
            canonical = str(
                item.get("canonical") or item.get("term") or ""
            ).strip()
            if not canonical:
                continue
            entries.append(
                {
                    "canonical": canonical,
                    "aliases": _split_list(
                        item.get("aliases") or item.get("synonyms")
                    ),
                    "note": str(item.get("note") or "").strip(),
                }
            )

    elif fmt == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ParseError("CSV has no header row")
        lower = {(f or "").strip().lower(): (f or "") for f in reader.fieldnames}
        ckey = lower.get("canonical") or lower.get("term")
        akey = lower.get("aliases") or lower.get("synonyms") or lower.get("alias")
        if not ckey:
            raise ParseError("CSV needs a 'canonical' (or 'term') column")
        for row in reader:
            canonical = str(row.get(ckey, "") or "").strip()
            if not canonical:
                continue
            entries.append(
                {
                    "canonical": canonical,
                    "aliases": _split_list(row.get(akey) if akey else ""),
                    "note": str(row.get(lower.get("note", ""), "") or "").strip(),
                }
            )

    else:  # txt:  canonical = alias, alias
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                canonical, aliases = line.split("=", 1)
            elif ":" in line:
                canonical, aliases = line.split(":", 1)
            else:
                warnings.append(f"line {lineno} skipped: expected 'term = alias, alias'")
                continue
            canonical = canonical.strip()
            if canonical:
                entries.append(
                    {
                        "canonical": canonical,
                        "aliases": _split_list(aliases),
                        "note": "",
                    }
                )

    if not entries:
        raise ParseError("no dictionary entries found")

    for e in entries:
        if not e["aliases"]:
            warnings.append(
                f"'{e['canonical']}' has no aliases, so it changes nothing"
            )

    return entries, warnings


def build_dictionary_entries(
    source_id: str, records: list[dict[str, Any]]
) -> list[DictionaryEntry]:
    return [
        DictionaryEntry(
            entry_id=new_id("dic"),
            source_id=source_id,
            canonical=r["canonical"],
            aliases=r.get("aliases", []),
            note=r.get("note", ""),
        )
        for r in records
    ]
