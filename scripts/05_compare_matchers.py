"""Shadow comparison: keyword matcher vs semantic matcher.  CHANGES NOTHING.

Runs both the existing System 2 keyword matcher and the new semantic matcher
over real tickets and reports where they agree and disagree. This is how you
decide whether semantics earn their keep BEFORE trusting them: look at the
disagreements on your own data.

Nothing here approves, executes, or writes state. It only reads tickets and
prints a comparison.

    python scripts/compare_matchers.py                 # uses data/incidents.csv
    python scripts/compare_matchers.py --limit 300     # cap rows
    python scripts/compare_matchers.py --show 25       # show N disagreements
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# This script lives in scripts/; the importable packages (overlay/, system2/)
# are one level up at the project root. Put the root on sys.path so `overlay`
# resolves regardless of the current working directory.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from overlay import _system2                      # noqa: E402  (also puts system2/ on sys.path)
import engine as kw_engine                        # noqa: E402  (system2/engine.py)
from overlay import semantic_engine               # noqa: E402
from overlay.service import normalize_ticket      # noqa: E402


def load_tickets(path: Path, limit: int | None) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for i, raw in enumerate(reader):
            if limit and i >= limit:
                break
            rows.append(normalize_ticket(raw))
    return rows


def name(match) -> str:
    return match.procedure.name if match else "(no match)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "incidents.csv"))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--show", type=int, default=20)
    ap.add_argument("--threshold", type=float, default=semantic_engine.DEFAULT_THRESHOLD)
    args = ap.parse_args()

    procedures = _system2.load_procedures()
    sem = semantic_engine.build_matcher(procedures, threshold=args.threshold)

    print(f"Semantic backend : {sem.backend}")
    print(f"Threshold        : {args.threshold}")
    print(f"Procedures       : {len(procedures)}")

    tickets = load_tickets(Path(args.csv), args.limit)
    print(f"Tickets scored   : {len(tickets)}\n")

    both = kw_only = sem_only = agree = disagree = neither = 0
    sem_wins: list[tuple] = []   # keyword missed, semantic found
    conflicts: list[tuple] = []  # both matched, but different procedures

    for t in tickets:
        kw = kw_engine.match_ticket(t, procedures)
        sm = sem.match(t)

        if kw and sm:
            both += 1
            if kw.procedure.procedure_id == sm.procedure.procedure_id:
                agree += 1
            else:
                disagree += 1
                conflicts.append((t, kw, sm))
        elif kw and not sm:
            kw_only += 1
        elif sm and not kw:
            sem_only += 1
            sem_wins.append((t, sm))
        else:
            neither += 1

    total = len(tickets)
    kw_total = both + kw_only
    sem_total = both + sem_only

    print("=" * 64)
    print("RESULTS")
    print("=" * 64)
    print(f"  Keyword matched            : {kw_total:5d}  ({kw_total/total:6.1%})")
    print(f"  Semantic matched           : {sem_total:5d}  ({sem_total/total:6.1%})")
    print(f"  Both matched               : {both:5d}")
    print(f"    - agreed (same procedure): {agree:5d}")
    print(f"    - disagreed              : {disagree:5d}")
    print(f"  Keyword only               : {kw_only:5d}")
    print(f"  Semantic only (kw missed)  : {sem_only:5d}   <- recall gained")
    print(f"  Neither                    : {neither:5d}")
    print()

    if sem_wins:
        print("-" * 64)
        print(f"SEMANTIC FOUND A MATCH THE KEYWORD ENGINE MISSED (showing {min(args.show, len(sem_wins))})")
        print("-" * 64)
        for t, sm in sem_wins[: args.show]:
            sd = (t.get("short_description") or "")[:70]
            print(f"  [{t.get('number','?')}] sim={sm.score/10:.2f} -> {sm.procedure.name}")
            print(f"      ticket: {sd}")

    if conflicts:
        print()
        print("-" * 64)
        print(f"BOTH MATCHED BUT DISAGREED (showing {min(args.show, len(conflicts))})")
        print("-" * 64)
        for t, kw, sm in conflicts[: args.show]:
            sd = (t.get("short_description") or "")[:60]
            print(f"  [{t.get('number','?')}] kw={name(kw)}  |  sem={name(sm)} (sim={sm.score/10:.2f})")
            print(f"      ticket: {sd}")

    print()
    print("NOTE: this is a shadow run. No behaviour changed; a human still")
    print("approves every plan and risk.py still gates execution.")


if __name__ == "__main__":
    main()
