"""System 2 CLI: feed a ticket to the engine and simulate the resolution.

SIMULATION ONLY. No real system is ever contacted.

Examples:
  # Simulate a ticket described inline
  python run_engine.py --desc "TPVOL ldn2ws9696 C Disk Space" --ci ldn2ws9696

  # Render the human runbook instead of a dry-run
  python run_engine.py --desc "Netprobe disconnected on njc1ws7051" --mode runbook

  # Sample real Wintel incidents from the extracted data
  python run_engine.py --sample 5

  # List the available procedures
  python run_engine.py --list
"""

from __future__ import annotations

import argparse
from pathlib import Path

from schema import load_procedures
from engine import match_ticket
from executor import render_runbook, dry_run, format_result

HERE = Path(__file__).resolve().parent
PROCEDURES = HERE / "procedures.json"
# data/incidents.csv lives one level up from system2/
DATA_INCIDENTS = HERE.parent / "data" / "incidents.csv"


def _simulate_ticket(ticket, procedures, mode):
    print("\n" + "#" * 64)
    print("TICKET:", ticket.get("short_description", "(no description)"))
    if ticket.get("assignment_group"):
        print("GROUP :", ticket["assignment_group"])
    print("#" * 64)

    match = match_ticket(ticket, procedures)
    if match is None:
        print("No matching procedure. (No automation known for this ticket.)")
        return

    print(f"MATCH : {match.procedure.name}  [{match.procedure.procedure_id}]")
    print(f"        score={match.score:.0f}  keywords={match.matched_keywords}"
          f"  automatability={match.procedure.automatability}")
    print()

    if mode == "runbook":
        result = render_runbook(match.procedure, match.parameters)
    else:
        result = dry_run(match.procedure, match.parameters, match.missing)

    print(format_result(result))


def _sample_from_data(n):
    try:
        import pandas as pd
    except ImportError:
        print("pandas required for --sample")
        return []

    if not DATA_INCIDENTS.exists():
        print(f"No data at {DATA_INCIDENTS}. Run System 1 extraction first.")
        return []

    df = pd.read_csv(DATA_INCIDENTS, low_memory=False)
    if "assignment_group" in df.columns:
        df = df[df["assignment_group"].astype(str)
                .str.contains("Wintel", case=False, na=False)]

    cols = [c for c in ("short_description", "description", "close_notes",
                        "assignment_group", "cmdb_ci") if c in df.columns]
    return df[cols].head(n).fillna("").to_dict("records")


def main():
    parser = argparse.ArgumentParser(description="System 2 engine (simulation only)")
    parser.add_argument("--desc", help="ticket short description")
    parser.add_argument("--ci", help="cmdb_ci / hostname")
    parser.add_argument("--group", default="Global Wintel Server Support",
                        help="assignment group")
    parser.add_argument("--mode", choices=["dry_run", "runbook"], default="dry_run")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="sample N real Wintel incidents from the data")
    parser.add_argument("--list", action="store_true", help="list procedures")
    args = parser.parse_args()

    procedures = load_procedures(PROCEDURES)

    if args.list:
        print("Available procedures (the 'rules' driving the engine):\n")
        for p in procedures:
            print(f"  {p.procedure_id:30} {p.name}")
            print(f"  {'':30} triggers: {', '.join(p.trigger.keywords[:5])}")
            print(f"  {'':30} steps: {len(p.steps)}  automatability: {p.automatability}\n")
        return

    if args.sample:
        tickets = _sample_from_data(args.sample)
        if not tickets:
            return
        for ticket in tickets:
            _simulate_ticket(ticket, procedures, args.mode)
        return

    if args.desc:
        ticket = {
            "short_description": args.desc,
            "assignment_group": args.group,
            "cmdb_ci": args.ci or "",
        }
        _simulate_ticket(ticket, procedures, args.mode)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
