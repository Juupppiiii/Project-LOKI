"""Service-level tests for the triage use case.

Guarantees:
  * the feature is gated: disabled by default -> refused;
  * enabled -> a triage run is persisted to the dedicated triage_runs table,
    carries a classification, and is audited against the ticket;
  * an unknown live ticket is refused;
  * the triage run executes nothing (simulated=True on the offline path).
"""

from __future__ import annotations

import pytest

from overlay.domain import DomainError, LiveTicket, LiveTicketStatus

pytestmark = pytest.mark.unit


def _seed_host_down_ticket(store) -> LiveTicket:
    lt = LiveTicket(
        live_id="lt_test0001",
        ticket_number="INC73400001",
        sys_id="sys0001",
        source="servicenow",
        table="incident",
        payload={
            "number": "INC73400001",
            "short_description": "njc1ws2471.corp.ad.tullib.com UP -> DOWN No IP packet received for 60.9 s",
            "description": "",
            "category": "Events-CMK",
            "assignment_group": "Global Wintel Server Support",
        },
        status=LiveTicketStatus.NEW,
    )
    store.upsert_live_ticket(lt)
    return lt


def test_triage_disabled_by_default_is_refused(service, monkeypatch):
    monkeypatch.delenv("BUCKY_TRIAGE_AGENT_ENABLED", raising=False)
    _seed_host_down_ticket(service.store)
    with pytest.raises(DomainError):
        service.run_triage("lt_test0001", actor="alice")


def test_triage_enabled_persists_and_audits(service, monkeypatch):
    monkeypatch.setenv("BUCKY_TRIAGE_AGENT_ENABLED", "1")
    _seed_host_down_ticket(service.store)

    record = service.run_triage("lt_test0001", actor="alice")

    assert record["triage_id"].startswith("trg_")
    assert record["ticket_ref"] == "INC73400001"
    assert record["host"] == "njc1ws2471.corp.ad.tullib.com"
    assert record["classification"]  # a verdict was reached
    assert record["simulated"] is True  # offline path touches nothing

    # persisted in the dedicated table and retrievable
    fetched = service.get_triage_run(record["triage_id"])
    assert fetched is not None
    assert fetched["triage_id"] == record["triage_id"]
    listed = service.list_triage_runs(live_id="lt_test0001")
    assert any(r["triage_id"] == record["triage_id"] for r in listed)

    # audited against the ticket
    events = [e.event for e in service.store.list_audit("INC73400001")]
    assert "triage.completed" in events


def test_triage_unknown_ticket_refused(service, monkeypatch):
    monkeypatch.setenv("BUCKY_TRIAGE_AGENT_ENABLED", "1")
    with pytest.raises(DomainError):
        service.run_triage("lt_does_not_exist", actor="alice")
