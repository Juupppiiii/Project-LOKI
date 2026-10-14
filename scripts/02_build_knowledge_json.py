"""Pull this year's closed human-resolved incidents and build structured JSON
knowledge files showing how each was resolved.

Read-only against ServiceNow (GET only). Produces:
  knowledge/tickets/resolved_tickets.json     — all pulled tickets, structured
  knowledge/tickets/by_group/*.json           — one file per assignment group
  knowledge/tickets/resolution_patterns.json  — clustered recurring fix patterns
  knowledge/tickets/summary.json              — overview stats

Run:
    python scripts/build_knowledge_json.py
    python scripts/build_knowledge_json.py --limit 5000    # cap for testing
    python scripts/build_knowledge_json.py --all            # pull all ~22k
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for p in (str(ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
import extractor  # noqa: E402
from requests.auth import HTTPBasicAuth  # noqa: E402

# Fields that carry resolution knowledge.
FIELDS = [
    "number", "sys_id", "short_description", "description",
    "close_notes", "work_notes", "comments", "close_code", "resolution_code",
    "resolved_at", "resolved_by", "assignment_group", "assigned_to",
    "cmdb_ci", "category", "subcategory", "priority", "state",
    "contact_type", "reopen_count", "reassignment_count",
    "calendar_stc", "business_stc",
    "sys_created_on", "sys_updated_on",
]

# This year, closed, human-raised, with close_notes.
QUERY = (
    "sys_created_onONThis year"
    "@javascript:gs.beginningOfThisYear()"
    "@javascript:gs.endOfThisYear()"
    "^active=false"
    "^u_record_type=incident"
    "^contact_type!=Automated"
    "^close_notesISNOTEMPTY"
    "^ORDERBYDESCsys_updated_on"
)

_WS = re.compile(r"\s+")
_RESOLVED_BY_STAMP = re.compile(r"Resolved (from|by)\s+\S+\s+Resolved By:\s*\S+", re.I)
_BOILERPLATE = re.compile(
    r"^(this is ok|this is oks|ok|oks|test|raised|already raised|no action required|"
    r"resolved|closed by caller)\.?\s*$",
    re.I,
)


def _norm(s: object) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def _clean_resolution(close_notes: str, work_notes: str) -> str:
    cn = _RESOLVED_BY_STAMP.sub("", close_notes).strip()
    cn = _BOILERPLATE.sub("", cn).strip(" .-")
    wn = _BOILERPLATE.sub("", work_notes).strip(" .-")
    return _norm(f"{cn} {wn}").strip()


def _is_substantive(resolution: str) -> bool:
    return len(resolution) >= 30 and bool(re.search(r"[a-zA-Z]{4,}", resolution))


def _resolution_seconds(row: dict) -> float | None:
    stc = str(row.get("calendar_stc") or "").strip()
    if stc.isdigit() and int(stc) > 0:
        return float(stc)
    created = str(row.get("sys_created_on") or "").strip()
    resolved = str(row.get("resolved_at") or "").strip()
    try:
        c = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
        r = datetime.strptime(resolved, "%Y-%m-%d %H:%M:%S")
        return max(0.0, (r - c).total_seconds())
    except (ValueError, TypeError):
        return None


def structure_ticket(raw: dict) -> dict:
    cn = _norm(raw.get("close_notes"))
    wn = _norm(raw.get("work_notes"))
    resolution = _clean_resolution(cn, wn)
    secs = _resolution_seconds(raw)
    return {
        "number": _norm(raw.get("number")),
        "short_description": _norm(raw.get("short_description")),
        "description": _norm(raw.get("description"))[:500],
        "assignment_group": _norm(raw.get("assignment_group")),
        "cmdb_ci": _norm(raw.get("cmdb_ci")),
        "category": _norm(raw.get("category")),
        "subcategory": _norm(raw.get("subcategory")),
        "priority": _norm(raw.get("priority")),
        "close_code": _norm(raw.get("close_code")),
        "resolved_by": _norm(raw.get("resolved_by")),
        "resolved_at": _norm(raw.get("resolved_at")),
        "close_notes_raw": cn[:500],
        "work_notes_raw": wn[:1000],
        "resolution_clean": resolution[:500],
        "resolution_substantive": _is_substantive(resolution),
        "resolution_seconds": round(secs, 1) if secs is not None else None,
        "reopen_count": int(raw.get("reopen_count") or 0),
        "reassignment_count": int(raw.get("reassignment_count") or 0),
        "sys_created_on": _norm(raw.get("sys_created_on")),
    }


def pull(limit: int, auth: HTTPBasicAuth) -> list[dict]:
    url = f"https://{config.INSTANCE}/api/now/table/incident"
    page_size = min(config.PAGE_SIZE, 1000)
    base = {
        "sysparm_limit": page_size,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join(FIELDS),
        "sysparm_query": QUERY,
    }
    rows: list[dict] = []
    offset = 0
    while len(rows) < limit:
        params = dict(base, sysparm_offset=offset)
        page = extractor._request_page(url, params, auth)
        if not page:
            break
        rows.extend(page)
        print(f"  offset {offset:>7,}: +{len(page):,} (total {len(rows):,})")
        offset += len(page)
    return rows[:limit]


def build_by_group(tickets: list[dict], out_dir: Path) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        g = t["assignment_group"] or "(none)"
        groups[g].append(t)

    group_dir = out_dir / "by_group"
    group_dir.mkdir(parents=True, exist_ok=True)
    stats = {}
    for g, tix in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        safe_name = re.sub(r"[^\w\-]", "_", g)[:60].strip("_") or "none"
        path = group_dir / f"{safe_name}.json"
        substantive = [t for t in tix if t["resolution_substantive"]]
        payload = {
            "assignment_group": g,
            "total_tickets": len(tix),
            "substantive_resolutions": len(substantive),
            "tickets": tix,
        }
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        stats[g] = {"total": len(tix), "substantive": len(substantive)}
    return stats


def build_patterns(tickets: list[dict]) -> list[dict]:
    """Cluster substantive tickets by close_code + category for recurring patterns."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in tickets:
        if not t["resolution_substantive"]:
            continue
        key = f"{t['close_code']}|{t['category']}|{t['assignment_group']}"
        buckets[key].append(t)

    patterns = []
    for key, tix in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        if len(tix) < 3:
            continue
        sample = tix[:5]
        patterns.append({
            "close_code": tix[0]["close_code"],
            "category": tix[0]["category"],
            "assignment_group": tix[0]["assignment_group"],
            "count": len(tix),
            "example_numbers": [t["number"] for t in sample],
            "example_resolutions": [t["resolution_clean"][:200] for t in sample],
        })
    return patterns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=10000, help="max tickets to pull")
    ap.add_argument("--all", action="store_true", help="pull all (~22k)")
    args = ap.parse_args()

    if args.all:
        args.limit = 999999

    if not config.PASSWORD:
        print("ServiceNow credentials not configured. Set SERVICENOW_PASSWORD.", file=sys.stderr)
        return 2

    auth = HTTPBasicAuth(config.USERNAME, config.PASSWORD)
    out_dir = ROOT / "knowledge" / "tickets"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Instance : {config.INSTANCE}")
    print("Query    : this year, closed, human-raised, with close_notes")
    print(f"Target   : up to {args.limit:,} rows (read-only GET)\n")

    raw = pull(args.limit, auth)
    print(f"\nPulled {len(raw):,} raw tickets. Structuring...\n")

    tickets = [structure_ticket(r) for r in raw]
    substantive = [t for t in tickets if t["resolution_substantive"]]

    # Save all tickets as one big JSON.
    all_path = out_dir / "resolved_tickets.json"
    all_path.write_text(json.dumps(tickets, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(tickets):,} structured tickets -> {all_path}")

    # Per-group files.
    group_stats = build_by_group(tickets, out_dir)
    print(f"Saved {len(group_stats)} group files -> {out_dir / 'by_group'}")

    # Resolution patterns.
    patterns = build_patterns(tickets)
    patterns_path = out_dir / "resolution_patterns.json"
    patterns_path.write_text(json.dumps(patterns, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(patterns)} resolution patterns -> {patterns_path}")

    # Summary.
    summary = {
        "pulled_at": datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "instance": config.INSTANCE,
        "query_scope": "this year, closed, human-raised, close_notes present",
        "total_pulled": len(tickets),
        "substantive_resolutions": len(substantive),
        "substantive_rate": round(len(substantive) / max(len(tickets), 1), 3),
        "assignment_groups": len(group_stats),
        "resolution_patterns": len(patterns),
        "top_groups": sorted(group_stats.items(), key=lambda kv: -kv[1]["total"])[:15],
        "top_close_codes": _top_values(tickets, "close_code", 10),
        "top_categories": _top_values(tickets, "category", 10),
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved summary -> {summary_path}")

    print(f"\n{'='*60}")
    print("KNOWLEDGE BUILD COMPLETE")
    print(f"{'='*60}")
    print(f"  total tickets      : {len(tickets):,}")
    print(f"  substantive fixes  : {len(substantive):,} ({summary['substantive_rate']:.0%})")
    print(f"  assignment groups  : {len(group_stats)}")
    print(f"  resolution patterns: {len(patterns)}")
    print(f"  output             : {out_dir}")
    return 0


def _top_values(tickets: list[dict], field: str, n: int) -> list[list]:
    counts: dict[str, int] = defaultdict(int)
    for t in tickets:
        counts[t.get(field) or "(none)"] += 1
    return [[k, v] for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]


if __name__ == "__main__":
    sys.exit(main())
