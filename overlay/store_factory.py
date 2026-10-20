"""Explicit persistence backend selection.

Local development remains SQLite by default. Production must opt into
PostgreSQL and provide its connection settings; it never silently falls back to
a local file when PostgreSQL is requested.
"""

from __future__ import annotations

import os
from pathlib import Path

from .store import Store
from .store_contract import StoreProtocol

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_store_from_env() -> StoreProtocol:
    backend = os.getenv("BUCKY_STORE_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        path = os.getenv("OVERLAY_DB", str(_PROJECT_ROOT / "overlay_tasks.db"))
        return Store(path)
    if backend == "postgres":
        from .postgres_store import PostgresStore

        return PostgresStore.from_env()
    raise RuntimeError(
        f"unsupported BUCKY_STORE_BACKEND '{backend}'; choose sqlite or postgres"
    )
