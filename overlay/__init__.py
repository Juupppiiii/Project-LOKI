"""Task overlay: human-in-the-loop orchestration on top of System 1 + System 2.

Layering (important for keeping the UI swappable):

    domain.py     pure model    - no framework, no DB, no transport
    risk.py       policy        - how a step is classified
    planner.py    use case      - ticket + procedure -> an approvable plan
    store.py      persistence   - SQLite behind a repository seam
    simulator.py  execution     - SIMULATION ONLY, no connectors
    service.py    application   - the use cases the API exposes
    api.py        transport     - FastAPI; the contract every client speaks
    web/          one client    - a thin browser client, holds no logic

Any future desktop app replaces only `web/`. Everything above it is reused
unchanged, because all decisions happen server-side.
"""

__all__ = ["domain", "risk", "planner", "store", "simulator", "service"]
