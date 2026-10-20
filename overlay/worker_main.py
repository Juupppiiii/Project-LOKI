"""Entry point for the simulation worker container.

    python -m overlay.worker_main

Wires the AWS-backed transport from the environment and runs the worker loop in
the foreground until signalled. This is the command the ECS worker task runs.

Required environment:
    BUCKY_SIMULATION_QUEUE_URL   SQS FIFO queue URL to consume
Optional:
    AWS_REGION / AWS_DEFAULT_REGION   region for the boto3 clients
    BUCKY_EXECUTION_MODE               must be "simulation" (default); refused otherwise
    BUCKY_STORE_BACKEND / OVERLAY_DB / BUCKY_POSTGRES_*   same as the API
    BUCKY_WORKER_LEASE_TTL_SECONDS / BUCKY_WORKER_WAIT_SECONDS / BUCKY_WORKER_BATCH

The worker shares the same store (and therefore the same idempotency ledger) as
the API by reading the same backend env, so execution can never happen twice.
"""

from __future__ import annotations

import os
import signal
import sys
import time

from .service import OverlayService
from .worker import SimulationWorker
from .worker_aws import SqsTaskQueue, StepFunctionsReporter


def _refuse_non_simulation() -> None:
    mode = os.getenv("BUCKY_EXECUTION_MODE", "simulation")
    if mode != "simulation":
        raise SystemExit(
            f"refusing to start: BUCKY_EXECUTION_MODE={mode!r}; this worker is "
            "simulation-only"
        )


def main() -> int:
    _refuse_non_simulation()

    queue_url = os.getenv("BUCKY_SIMULATION_QUEUE_URL", "").strip()
    if not queue_url:
        raise SystemExit("BUCKY_SIMULATION_QUEUE_URL is required")

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    # OverlayService construction also enforces simulation mode via ActionGateway.
    service = OverlayService()
    queue = SqsTaskQueue(queue_url, region_name=region)
    reporter = StepFunctionsReporter(region_name=region)
    worker = SimulationWorker(service, queue, reporter)

    stopping = {"flag": False}

    def _handle(signum, _frame):  # noqa: ANN001
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    print(f"simulation-worker: consuming {queue_url}", flush=True)
    worker.start()
    try:
        while not stopping["flag"] and worker._thread and worker._thread.is_alive():
            time.sleep(1.0)
    finally:
        worker.stop()
        print(
            f"simulation-worker: stopped (processed={worker.processed}, "
            f"succeeded={worker.succeeded}, failed={worker.failed})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
