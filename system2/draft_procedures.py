"""Auto-draft System 2 procedures from System 1 resolution data.

Reads the extracted incidents for a given assignment group, clusters tickets
into recurring problem themes, summarises how they were actually resolved
(from close_notes), and emits DRAFT procedure entries as JSON for human review.

This bridges System 1 (discovery) and System 2 (execution): instead of
hand-writing every procedure, an analyst starts from data-grounded drafts.

Drafts are written to procedures_drafts.json and are intentionally NOT loaded
by the engine until a human promotes them into procedures.json. Draft steps
are placeholders -- a human must supply the real commands.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA_INCIDENTS = HERE.parent / "data" / "incidents.csv"
DRAFTS_OUT = HERE / "procedures_drafts.json"

# Theme definitions: a theme id -> the keywords that identify it in the
# problem text. These are the recurring Wintel problem classes seen in the
# data that we do NOT already have hand-authored procedures for.
THEMES = {
    "high_cpu": {
        "name": "High CPU utilisation",
        "keywords": ["cpu", "utilization", "utilisation", "high cpu"],
        "suggested_action": "run_command",
    },
    "packet_loss": {
        "name": "Network packet loss / host unreachable",
        "keywords": ["packet loss", "packet", "unreachable", "100% packet"],
        "suggested_action": "check",
    },
    "hardware_crit": {
        "name": "Hardware component critical (storage/temperature)",
        "keywords": ["crit", "critical condition", "temperature", "controller",
                     "smart array", "component state"],
        "suggested_action": "notify",
    },
    "high_memory_swap": {
        "name": "High memory / swap usage",
        "keywords": ["swap", "memory", "high swap", "high memory"],
        "suggested_action": "run_command",
    },
    "login_access": {
        "name": "Server login / access failure",
        "keywords": ["login failed", "unable to access", "login issue",
                     "cannot access", "access denied"],
        "suggested_action": "check",
    },
}

_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return _WS.sub(" ", _HTML.sub(" ", text)).strip()


def _load_group(group_substr: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_INCIDENTS, low_memory=False)
    mask = df["assignment_group"].astype(str).str.contains(
        group_substr, case=False, na=False
    )
    return df[mask].fillna("")


def _theme_matches(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)


def draft(group_substr: str, min_frequency: int) -> list[dict]:
    rows = _load_group(group_substr)
    drafts = []

    for theme_id, spec in THEMES.items():
        hits = []
        for _, r in rows.iterrows():
            problem = f"{r.get('short_description','')} {r.get('description','')}"
            if _theme_matches(problem, spec["keywords"]):
                hits.append(r)

        if len(hits) < min_frequency:
            continue

        # Most common cleaned close_notes for this theme = how it's resolved.
        notes = Counter()
        for r in hits:
            cn = _clean(str(r.get("close_notes", "")))
            if 4 < len(cn) <= 200:
                notes[cn] += 1
        top_notes = [n for n, _ in notes.most_common(5)]

        sample_tickets = [
            str(r.get("number", "") or r.get("short_description", ""))[:60]
            for r in hits[:5]
        ]

        drafts.append({
            "procedure_id": f"wintel.{theme_id}",
            "name": spec["name"],
            "trigger": {
                "keywords": spec["keywords"],
                "assignment_group": "Global Wintel Server Support",
            },
            "parameters": [
                {"name": "server", "type": "hostname", "source": "cmdb_ci"}
            ],
            "steps": [
                {"action": "connect", "target": "{server}",
                 "description": "Open a remoting session"},
                {"action": spec["suggested_action"],
                 "description": "TODO: replace with real resolution step(s)",
                 "command": "TODO"},
                {"action": "notify",
                 "description": "Post work note with outcome"},
            ],
            "automatability": "low",
            "frequency": len(hits),
            "source_tickets": sample_tickets,
            "notes": "AUTO-DRAFT for review. Observed resolutions: "
                     + " | ".join(top_notes[:3]),
            "_observed_resolutions": top_notes,
            "_status": "draft_needs_review",
        })

    return sorted(drafts, key=lambda d: d["frequency"], reverse=True)


def main():
    parser = argparse.ArgumentParser(
        description="Auto-draft System 2 procedures from resolution data"
    )
    parser.add_argument("--group", default="Wintel",
                        help="assignment group substring")
    parser.add_argument("--min-frequency", type=int, default=5,
                        help="minimum tickets in a theme to draft it")
    args = parser.parse_args()

    if not DATA_INCIDENTS.exists():
        print(f"No data at {DATA_INCIDENTS}. Run System 1 extraction first.")
        return

    drafts = draft(args.group, args.min_frequency)

    DRAFTS_OUT.write_text(
        json.dumps({"procedures": drafts}, indent=2), encoding="utf-8"
    )

    print(f"Drafted {len(drafts)} candidate procedures -> {DRAFTS_OUT.name}\n")
    for d in drafts:
        print(f"  {d['procedure_id']:24} freq={d['frequency']:<4} {d['name']}")
        for note in d["_observed_resolutions"][:2]:
            print(f"      resolved by: {note[:70]}")
    print("\nThese are DRAFTS. Review, fill in real steps, then move the good "
          "ones into procedures.json.")


if __name__ == "__main__":
    main()
