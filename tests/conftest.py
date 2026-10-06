"""Shared fixtures. Every test runs against isolated temporary state and never
touches real ServiceNow, Active Directory, AWS, or the developer's database.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Operational one-off scripts live under scripts/ (migration, demo, doc builders).
# A couple of them are imported directly by tests (e.g. migrate_sqlite_to_postgres),
# so make that directory importable for the whole test session.
_SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture
def sqlite_path(tmp_path) -> str:
    """A throwaway SQLite database path unique to each test."""

    return str(tmp_path / "overlay_test.db")


@pytest.fixture
def store(sqlite_path):
    """A fresh SQLite Store, closed automatically after the test."""

    from overlay.store import Store

    instance = Store(sqlite_path)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def service(sqlite_path, monkeypatch):
    """An OverlayService bound to an isolated SQLite database in simulation mode."""

    from overlay.service import OverlayService

    monkeypatch.setenv("OVERLAY_DB", sqlite_path)
    monkeypatch.setenv("LOKI_STORE_BACKEND", "sqlite")
    monkeypatch.setenv("LOKI_EXECUTION_MODE", "simulation")
    instance = OverlayService()
    try:
        yield instance
    finally:
        instance.shutdown()


@pytest.fixture
def restart_ticket() -> dict:
    """A ticket that matches the fully agent-runnable service-restart procedure."""

    return {
        "number": "INC0012345",
        "short_description": "Netprobe service stopped on ldn2ws9696",
        "description": "agent down, service not running",
        "assignment_group": "Global Wintel Server Support",
        "cmdb_ci": "ldn2ws9696",
        "service_name": "NetprobeNT_64",
    }


@pytest.fixture
def postgres_dsn():
    """PostgreSQL DSN for repository tests, or None to skip cleanly."""

    return os.getenv("LOKI_TEST_POSTGRES_DSN")
