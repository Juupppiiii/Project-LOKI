"""Measure the quality of evidence-based recommendations.

This turns "recommend_candidates ranks something" into "it ranks measurably
well". It runs a small LABELED set of tickets through the live service and
reports:

  * precision@1   -- of the tickets we DID recommend on, how often the top
                     candidate was the expected procedure.
  * recall        -- of the tickets that have a correct procedure, how often we
                     surfaced it at rank 1 (abstentions count against recall).
  * abstain rate  -- how often we declined to recommend.
  * abstain correctness -- for the deliberately-unmatchable tickets, did we
                     correctly abstain? (A good system abstains on noise.)
  * signal coverage -- how often each signal (keyword / semantic / error_code /
                     historical_resolution / assignment_group) contributed.

The labeled set is intentionally small and hand-checked. It is a regression
and calibration harness, NOT a claim of production accuracy -- expand it group
by group against real tickets, exactly as the roadmap advises. Nothing here
proposes, approves, or executes anything; it only reads recommendations.

Usage:
    python scripts/07_calibrate_recommendations.py
    python scripts/07_calibrate_recommendations.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Each case: a ticket, and the procedure_id we expect at rank 1. `None` means
# "should abstain" -- the ticket is deliberately out of scope / noise.
LABELED_SET = [
    {
        "name": "service restart (classic)",
        "ticket": {
            "short_description": "Netprobe service stopped on ldn2ws9696",
            "description": "agent down, service not running",
            "assignment_group": "Global Wintel Server Support",
            "cmdb_ci": "ldn2ws9696",
        },
        "expected": "wintel.service_restart",
    },
    {
        "name": "disk space low",
        "ticket": {
            "short_description": "Low disk space on C: drive, server uk1pbtrdsql01",
            "description": "disk almost full, cleanup needed",
            "assignment_group": "Global Wintel Server Support",
        },
        "expected": "wintel.disk_cleanup",
    },
    {
        "name": "high CPU",
        "ticket": {
            "short_description": "High CPU utilisation on njc1ws040n01",
            "description": "cpu pegged at 100 percent, process investigation needed",
            "assignment_group": "Global Wintel Server Support",
        },
        "expected": "wintel.high_cpu",
    },
    {
        "name": "server reboot",
        "ticket": {
            "short_description": "Server unresponsive, needs reboot - hk1cmgmt02",
            "description": "host not responding, reboot windows server",
            "assignment_group": "Global Wintel Server Support",
        },
        "expected": "wintel.server_reboot",
    },
    {
        "name": "message queue backlog",
        "ticket": {
            "short_description": "Message queue backlog alert on LDN.QA.52.Q.GTN_OE_UK_BONDS.SVC",
            "description": "queue depth growing, backlog building",
            "assignment_group": "Middleware Support",
        },
        "expected": "triage_a_message_queue_backlog_alert",
    },
    {
        "name": "AWS spot recalled",
        "ticket": {
            "short_description": "AWS Spot instance recalled i-0abc123def4567890",
            "description": "spot instance reclaimed, needs handling",
            "assignment_group": "Cloud Operations",
        },
        "expected": "handle_an_aws_spot_recalled_instance",
    },
    # --- deliberately out of scope: the system SHOULD abstain ---
    {
        "name": "noise / gibberish",
        "ticket": {
            "short_description": "zxqw plover mimsy borogove frumious",
            "description": "qqq wwww eeee rrrr",
            "assignment_group": "Nowhere",
        },
        "expected": None,
    },
    {
        "name": "unrelated HR request",
        "ticket": {
            "short_description": "Please update my emergency contact details",
            "description": "HR record change request",
            "assignment_group": "People Team",
        },
        "expected": None,
    },
]


def run() -> dict:
    from overlay.service import OverlayService

    service = OverlayService()
    try:
        # The procedures actually loaded in this environment. A labeled case
        # whose expected procedure is NOT loaded is a coverage gap, not a
        # ranking failure -- scoring it as a miss would blame the matcher for a
        # procedure that simply is not present. Such cases are reported as
        # 'not_loaded' and excluded from precision/recall.
        loaded_ids = {p.procedure_id for p in service._procedures}

        rows = []
        signal_counts: dict[str, int] = {}
        for case in LABELED_SET:
            expected = case["expected"]
            not_loaded = expected is not None and expected not in loaded_ids

            res = service.recommend_candidates(case["ticket"], top=5)
            recommended = res["decision"] == "recommend"
            top = res["candidates"][0] if res["candidates"] else None
            top_id = top["procedure_id"] if (recommended and top) else None

            for c in res["candidates"]:
                for sig in c["evidence"]["signals"]:
                    signal_counts[sig] = signal_counts.get(sig, 0) + 1

            if not_loaded:
                correct = None  # not scored
            elif expected is None:
                correct = not recommended  # should have abstained
            else:
                correct = recommended and top_id == expected

            rows.append(
                {
                    "name": case["name"],
                    "expected": expected,
                    "recommended": recommended,
                    "top_id": top_id,
                    "top_confidence": top["confidence"] if top else None,
                    "not_loaded": not_loaded,
                    "correct": correct,
                }
            )
    finally:
        service.shutdown()

    scored = [r for r in rows if not r["not_loaded"]]
    not_loaded_rows = [r for r in rows if r["not_loaded"]]
    in_scope = [r for r in scored if r["expected"] is not None]
    out_scope = [r for r in scored if r["expected"] is None]
    recommended_in = [r for r in in_scope if r["recommended"]]

    precision_at_1 = (
        sum(r["correct"] for r in recommended_in) / len(recommended_in)
        if recommended_in
        else 0.0
    )
    recall = (
        sum(r["correct"] for r in in_scope) / len(in_scope) if in_scope else 0.0
    )
    abstain_rate = (
        sum(not r["recommended"] for r in scored) / len(scored) if scored else 0.0
    )
    abstain_correctness = (
        sum(r["correct"] for r in out_scope) / len(out_scope) if out_scope else 0.0
    )

    return {
        "cases": rows,
        "metrics": {
            "n_cases": len(rows),
            "n_scored": len(scored),
            "n_not_loaded": len(not_loaded_rows),
            "n_in_scope": len(in_scope),
            "n_out_scope": len(out_scope),
            "precision_at_1": round(precision_at_1, 3),
            "recall_at_1": round(recall, 3),
            "abstain_rate": round(abstain_rate, 3),
            "abstain_correctness": round(abstain_correctness, 3),
        },
        "signal_coverage": dict(sorted(signal_counts.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibrate recommend_candidates.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    report = run()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    m = report["metrics"]
    print("Recommendation calibration")
    print("=" * 52)
    for r in report["cases"]:
        mark = "-- " if r["not_loaded"] else ("OK " if r["correct"] else "XX ")
        exp = r["expected"] or "(abstain)"
        if r["not_loaded"]:
            got = "(procedure not loaded)"
        elif r["recommended"]:
            got = r["top_id"]
        else:
            got = "(abstained)"
        conf = f"{r['top_confidence']:.2f}" if r["top_confidence"] is not None else "-"
        print(f"  {mark} {r['name']:<34} expect={exp:<40} got={got:<40} conf={conf}")
    print("-" * 52)
    if m["n_not_loaded"]:
        print(f"  note: {m['n_not_loaded']} case(s) excluded (expected procedure not loaded)")
    print(f"  precision@1          : {m['precision_at_1']:.3f}  (of recommendations)")
    print(f"  recall@1             : {m['recall_at_1']:.3f}  (in-scope found at rank 1)")
    print(f"  abstain rate         : {m['abstain_rate']:.3f}")
    print(f"  abstain correctness  : {m['abstain_correctness']:.3f}  (noise correctly declined)")
    print(f"  signal coverage      : {report['signal_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
