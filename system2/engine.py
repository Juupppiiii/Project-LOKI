"""System 2 engine: match a ticket to a procedure and resolve its parameters.

The engine is domain-agnostic. All domain knowledge lives in procedures.json.
It scores each procedure against a ticket by trigger keywords and assignment
group, picks the best match, then fills the procedure's parameters from ticket
fields (with light extraction for hostnames / service names).

No execution happens here -- see executor.py.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from schema import Procedure


# Triggers describe what a ticket is ABOUT, so match only against the problem
# statement -- NOT close_notes/work_notes. Resolution text is what we learned
# procedures FROM; matching on it makes every ticket closed with a boilerplate
# note falsely match that procedure.
PROBLEM_FIELDS = ("short_description", "description", "category")

# Minimum match score required before the engine will claim a procedure.
# One bare keyword hit (score 2) is too weak on its own; require either two
# keywords, or one keyword plus the assignment-group match.
MIN_SCORE = float(os.getenv("SYS2_MIN_SCORE", "3"))


@dataclass
class Match:
    procedure: Procedure
    score: float
    matched_keywords: list[str] = field(default_factory=list)
    group_match: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _ticket_text(ticket: dict[str, Any]) -> str:
    parts = [str(ticket.get(f, "")) for f in PROBLEM_FIELDS]
    return " ".join(parts).lower()


# TP ICAP server naming: a 2-3 letter site code, a digit, then a role/segment
# of letters and digits, e.g. ldn2ws9696, njc1ws040n01, hk1cmgmt02,
# uk1cucitxvda02, uk1pbtrdsql01. Requires at least one letter-then-digit
# transition so plain words and hex codes (ff8c00) don't match.
_HOSTNAME_RE = re.compile(
    r"\b([a-z]{2,3}\d[a-z]{2,10}\d{2,4}[a-z0-9]{0,4}(?:\.[a-z0-9.\-]+)?)\b",
    re.I,
)


def _looks_like_host(token: str) -> bool:
    token = token.split(".")[0]
    # Must contain letters AND digits, and not be a pure hex string.
    if not (re.search(r"[a-z]", token, re.I) and re.search(r"\d", token)):
        return False
    if re.fullmatch(r"[0-9a-f]{6}", token, re.I):  # hex colour / id
        return False
    return True


def _extract_hostname(ticket: dict[str, Any]) -> str | None:
    """Pull a likely hostname from cmdb_ci first, else the problem text."""

    ci = str(ticket.get("cmdb_ci", "")).strip()
    if ci and ci.lower() not in ("", "nan", "none") and _looks_like_host(ci):
        return ci

    text = " ".join(
        str(ticket.get(f, "")) for f in ("short_description", "description")
    )
    for m in _HOSTNAME_RE.finditer(text):
        if _looks_like_host(m.group(1)):
            return m.group(1)
    return None


# Known service tokens seen in the data, plus generic *Service/Agent/Client.
_SERVICE_RE = re.compile(
    r"\b(netprobe\w*|bes client|sql\w*|[A-Z][A-Za-z]+(?:Service|Svc|Agent|Client))\b",
    re.I,
)


def _extract_service(ticket: dict[str, Any]) -> str | None:
    """Best-effort service name extraction from the problem text."""

    text = " ".join(
        str(ticket.get(f, "")) for f in ("short_description", "description")
    )
    m = _SERVICE_RE.search(text)
    return m.group(1).strip() if m else None


# A person's name, used for profile deletion. Prefers a name after a dash or
# common lead-in words, else the first "Firstname Lastname" pair in the text.
_NAME_AFTER_DASH = re.compile(r"[-:]\s*([A-Z][a-z]+ [A-Z][a-z]+)")
_NAME_PAIR = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")


def _extract_person(ticket: dict[str, Any]) -> str | None:
    """Extract a person's name (for profile/account actions)."""

    text = str(ticket.get("short_description", ""))
    m = _NAME_AFTER_DASH.search(text)
    if m:
        return m.group(1)
    m = _NAME_PAIR.search(text)
    return m.group(1) if m else None


# AWS EC2 instance id: "i-" followed by 8 or 17 hex chars.
_INSTANCE_ID_RE = re.compile(r"\b(i-[0-9a-f]{8}(?:[0-9a-f]{9})?)\b", re.I)


def _extract_instance_id(ticket: dict[str, Any]) -> str | None:
    """Pull an AWS EC2 instance id (i-...) from the problem text."""

    text = " ".join(
        str(ticket.get(f, "")) for f in ("short_description", "description")
    )
    m = _INSTANCE_ID_RE.search(text)
    return m.group(1) if m else None


# A message-queue name. Two shapes seen in the data:
#   * a dotted, upper-case Solace path: LDN.QA.52.Q.GTN_OE_UK_BONDS.SVC
#   * a quoted queue in prose: queue 'prod/Persistence/Queue'
_QUEUE_DOTTED_RE = re.compile(r"\b([A-Z0-9]{2,}(?:\.[A-Z0-9_]{1,}){2,})\b")
_QUEUE_QUOTED_RE = re.compile(r"[Qq]ueue\s+['\"]([^'\"]+)['\"]")


def _extract_queue_name(ticket: dict[str, Any]) -> str | None:
    """Pull a message-queue name from the problem text."""

    text = " ".join(
        str(ticket.get(f, "")) for f in ("short_description", "description")
    )
    m = _QUEUE_QUOTED_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _QUEUE_DOTTED_RE.search(text)
    return m.group(1) if m else None


# A broader hostname fallback for alert-style hosts the strict regex misses:
# an FQDN (dotted, with a letter+digit host label) like usmizic-cacr12.tpicap.net,
# or a host token carrying both letters and digits like us08ufocuaa15d.
_FQDN_RE = re.compile(r"\b([a-z0-9][a-z0-9\-]*\d[a-z0-9\-]*(?:\.[a-z0-9\-]+){1,})\b", re.I)
_HOST_TOKEN_RE = re.compile(r"\b([a-z]{2,}\d[a-z0-9]{3,})\b", re.I)


def _extract_hostname_loose(ticket: dict[str, Any]) -> str | None:
    """Fallback host extraction for alert formats the strict regex misses."""

    text = " ".join(
        str(ticket.get(f, "")) for f in ("short_description", "description")
    )
    m = _FQDN_RE.search(text)
    if m and _looks_like_host(m.group(1)):
        return m.group(1)
    for m in _HOST_TOKEN_RE.finditer(text):
        if _looks_like_host(m.group(1)):
            return m.group(1)
    return None


def _resolve_parameters(procedure: Procedure, ticket: dict[str, Any]) -> tuple[dict, list]:
    """Fill procedure parameters from ticket fields. Returns (values, missing)."""

    values: dict[str, Any] = {}
    missing: list[str] = []

    for param in procedure.parameters:
        value = None

        if param.type == "hostname":
            value = _extract_hostname(ticket) or _extract_hostname_loose(ticket)
        elif param.type == "service":
            value = _extract_service(ticket)
        elif param.type == "username":
            value = _extract_person(ticket)
        elif param.type == "instance_id" or param.name == "instance_id":
            value = _extract_instance_id(ticket)
        elif param.type == "queue_name" or param.name == "queue_name":
            value = _extract_queue_name(ticket)
        elif param.source:
            raw = ticket.get(param.source)
            value = str(raw).strip() if raw not in (None, "") else None

        # Last resort: a parameter whose exact name matches a ticket field.
        if value is None and param.source is None:
            raw = ticket.get(param.name)
            if raw not in (None, ""):
                value = str(raw).strip()

        if value:
            values[param.name] = value
        elif param.required:
            missing.append(param.name)

    return values, missing


def match_ticket(ticket: dict[str, Any], procedures: list[Procedure]) -> Match | None:
    """Return the best-matching procedure for a ticket, or None.

    Scoring: +2 per matched trigger keyword, +1 if assignment group matches.
    A procedure must match at least one keyword to be considered.
    """

    text = _ticket_text(ticket)
    ticket_group = str(ticket.get("assignment_group", "")).strip().lower()

    best: Match | None = None

    for proc in procedures:
        matched = [kw for kw in proc.trigger.keywords if kw in text]
        if not matched:
            continue

        group_match = bool(
            proc.trigger.assignment_group
            and proc.trigger.assignment_group.lower() == ticket_group
        )

        score = 2.0 * len(matched) + (1.0 if group_match else 0.0)
        params, missing = _resolve_parameters(proc, ticket)

        candidate = Match(
            procedure=proc,
            score=score,
            matched_keywords=matched,
            group_match=group_match,
            parameters=params,
            missing=missing,
        )

        if best is None or candidate.score > best.score:
            best = candidate

    # Require a minimum confidence before claiming a match.
    if best is not None and best.score < MIN_SCORE:
        return None

    return best


def rank_matches(ticket: dict[str, Any], procedures: list[Procedure]) -> list[Match]:
    """Return all matching procedures, best first (for debugging / review)."""

    text = _ticket_text(ticket)
    ticket_group = str(ticket.get("assignment_group", "")).strip().lower()

    matches: list[Match] = []
    for proc in procedures:
        matched = [kw for kw in proc.trigger.keywords if kw in text]
        if not matched:
            continue
        group_match = bool(
            proc.trigger.assignment_group
            and proc.trigger.assignment_group.lower() == ticket_group
        )
        score = 2.0 * len(matched) + (1.0 if group_match else 0.0)
        params, missing = _resolve_parameters(proc, ticket)
        matches.append(Match(proc, score, matched, group_match, params, missing))

    return sorted(matches, key=lambda m: m.score, reverse=True)
