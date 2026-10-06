"""End-to-end demo / smoke test: prove the agent executes an assigned chunk.

Drives OverlayService the same way the API does -- propose -> approve -> execute --
and prints the plan, the per-step risk/owner split, the outcomes, and the audit
trail. Execution is SIMULATED (simulator.CONNECTORS_ENABLED is False), so this is
safe to run anywhere.

    python scripts/demo_agent_run.py
"""

import os
import sys
from pathlib import Path

# This script lives in scripts/; the importable packages are one level up at the
# project root. Put the root on sys.path so `overlay` resolves regardless of the
# current working directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# use a throwaway DB so the demo never touches real state (kept at the project
# root so it is covered by .gitignore and the demo is independent of cwd)
os.environ["OVERLAY_DB"] = str(ROOT / "demo_agent_test.db")
for f in (ROOT / "demo_agent_test.db",):
    try:
        os.remove(f)
    except OSError:
        pass

from overlay.service import OverlayService  # noqa: E402

svc = OverlayService()

# A ticket that matches the "restart a Windows service" procedure, which is the
# one procedure whose every step is agent-runnable (R0-R2).
ticket = {
    "number": "INC0012345",
    "short_description": "Netprobe service stopped on ldn2ws9696",
    "description": "agent down, service not running",
    "assignment_group": "Global Wintel Server Support",
    "cmdb_ci": "ldn2ws9696",
    "service_name": "NetprobeNT_64",
}

run = svc.propose(ticket, actor="alice")
print(f"RUN {run.run_id} | procedure: {run.procedure_name} | state: {run.state.value}")

print("\nPLAN (per-step risk and owner):")
for s in run.steps:
    print(f"  {s.sequence}. {s.action:16} risk={s.risk.value:3} owner={s.owner.value:5} :: {s.rendered}")

print("\nCHUNKS (what a human approves):")
for c in run.chunks:
    print(f"  [{c.owner.value}] risk={c.risk.value} :: {c.label}")

print("\nAPPROVE + EXECUTE the agent chunks:")
for c in run.chunks:
    if c.owner.value.lower().startswith("a"):  # agent-owned
        try:
            svc.decide_chunk(run.run_id, c.chunk_id, decision="approved", actor="bob_approver")
            svc.execute_chunk(run.run_id, c.chunk_id, actor="agent")
            print(f"  approved by bob_approver, ran as agent :: {c.label}")
        except Exception as e:
            print(f"  STOPPED at :: {c.label} -> {e}")

run = svc.get_run(run.run_id)
print(f"\nFINAL run state: {run.state.value}")

print("\nSTEP OUTCOMES:")
for s in run.steps:
    print(f"  {s.sequence}. {s.action:16} state={s.state.value:10} outcome={s.outcome or ''}")

print("\nAUDIT TRAIL (who did what):")
for a in svc.audit(run.run_id):
    at = a.get("created_at", "")
    event = a.get("event", "")
    actor = a.get("actor", "")
    print(f"  {at} | {event:22} | actor={actor}")
