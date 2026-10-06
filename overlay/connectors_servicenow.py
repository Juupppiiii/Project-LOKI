"""The first real connector: post a work note to a ServiceNow ticket.

This is the narrowest, safest possible first step across the "simulation ->
real" boundary. Posting a work note:

* cannot alter infrastructure (it writes a comment on a ticket),
* is trivially verifiable (you look at the ticket), and
* reuses the credentials and Table API the read-only live feed already uses.

Two independent switches must BOTH be on before this connector writes anything:

1. the gateway must run in ``live`` mode (``LOKI_EXECUTION_MODE=live``), and
2. ``LOKI_SERVICENOW_WRITE_ENABLED=1`` must be set.

With neither, this module is never even registered. With live mode but the
write switch off, the connector runs in **dry-run**: it validates everything and
returns a result describing exactly what it *would* post, without making any
write call. That lets the whole path be exercised end to end against a real
instance before a single real write happens.

Credentials come from the environment via the live feed's existing resolver;
they never touch the database and are never returned to a client.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from . import live
from .connectors import ActionEnvelope, ActionResult, ConnectorFailure

# A transport is any callable that performs one HTTP request and returns a
# simple response object with ``.status_code``, ``.text`` and ``.json()``. The
# default uses ``requests``; tests inject a fake so no network is touched.
Transport = Callable[..., Any]

WRITE_ENV = "LOKI_SERVICENOW_WRITE_ENABLED"


def write_enabled() -> bool:
    return os.getenv(WRITE_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def _requests_transport(method: str, url: str, *, headers, params=None, json=None, timeout=30):
    import requests  # lazy: only needed on the real write path

    return requests.request(
        method, url, headers=headers, params=params, json=json, timeout=timeout
    )


class ServiceNowConnector:
    """Posts a work note to a ServiceNow ticket via the Table API.

    ``transport`` and ``credentials_provider`` are injectable so the connector
    is fully testable offline. In normal use they default to the ``requests``
    based transport and the live feed's environment credential resolver.
    """

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        credentials_provider: Callable[[], dict[str, Any]] | None = None,
        timeout: int = 30,
    ) -> None:
        self._transport = transport or _requests_transport
        self._credentials = credentials_provider or live.load_credentials
        self._timeout = timeout

    # -- public contract -------------------------------------------------

    def execute(self, envelope: ActionEnvelope) -> ActionResult:
        note = str(envelope.parameters.get("note") or "").strip()
        if not note:
            # Should already be caught by the gateway's required-parameter check;
            # defend anyway so a direct call can never post an empty note.
            raise ConnectorFailure("work note text is required")

        ticket_number = self._ticket_number(envelope)
        creds = self._credentials()
        if not creds.get("present"):
            raise ConnectorFailure(
                "ServiceNow credentials are not configured; cannot post a work note"
            )

        if not write_enabled():
            # Dry-run: validated, but deliberately not writing. Report what would
            # happen so the path can be proven before enabling real writes.
            return ActionResult(
                summary=f"[DRY-RUN] would post work note to {ticket_number}",
                connector=envelope.connector,
                operation=envelope.operation,
                simulated=True,
                evidence={
                    "ticket": ticket_number,
                    "note_preview": note[:120],
                    "write_enabled": False,
                    "instance": creds.get("instance", ""),
                },
            )

        sys_id = self._resolve_sys_id(envelope, creds, ticket_number)
        self._patch_work_note(creds, sys_id, note)
        return ActionResult(
            summary=f"work note posted to {ticket_number}",
            connector=envelope.connector,
            operation=envelope.operation,
            simulated=False,
            evidence={
                "ticket": ticket_number,
                "sys_id": sys_id,
                "note_preview": note[:120],
                "write_enabled": True,
                "instance": creds.get("instance", ""),
            },
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _ticket_number(envelope: ActionEnvelope) -> str:
        # target_ref is "servicenow:ticket/{number}"; fall back to the param.
        ref = envelope.target_ref or ""
        if "/" in ref:
            candidate = ref.rsplit("/", 1)[-1].strip()
            if candidate:
                return candidate
        return str(envelope.parameters.get("ticket_id") or "").strip() or "(unknown ticket)"

    @property
    def _table(self) -> str:
        return os.getenv("LOKI_SERVICENOW_TABLE", "incident").strip() or "incident"

    def _resolve_sys_id(self, envelope: ActionEnvelope, creds: dict[str, Any], number: str) -> str:
        # Prefer an explicitly supplied sys_id; otherwise look it up by number.
        supplied = str(envelope.parameters.get("sys_id") or "").strip()
        if supplied:
            return supplied

        url = f"https://{creds['instance']}/api/now/table/{self._table}"
        resp = self._transport(
            "GET",
            url,
            headers=self._headers(creds),
            params={
                "sysparm_query": f"number={number}",
                "sysparm_fields": "sys_id,number",
                "sysparm_limit": 1,
            },
            timeout=self._timeout,
        )
        self._raise_for_status(resp, context="looking up the ticket")
        rows = self._result(resp)
        if not rows:
            raise ConnectorFailure(f"ServiceNow has no ticket matching number {number}")
        sys_id = str(rows[0].get("sys_id") or "").strip()
        if not sys_id:
            raise ConnectorFailure(f"ServiceNow returned no sys_id for {number}")
        return sys_id

    def _patch_work_note(self, creds: dict[str, Any], sys_id: str, note: str) -> None:
        url = f"https://{creds['instance']}/api/now/table/{self._table}/{sys_id}"
        resp = self._transport(
            "PATCH",
            url,
            headers={**self._headers(creds), "Content-Type": "application/json"},
            json={"work_notes": note},
            timeout=self._timeout,
        )
        self._raise_for_status(resp, context="posting the work note")

    @staticmethod
    def _headers(creds: dict[str, Any]) -> dict[str, str]:
        import base64

        token = base64.b64encode(
            f"{creds['username']}:{creds['password']}".encode()
        ).decode()
        return {"Authorization": f"Basic {token}", "Accept": "application/json"}

    @staticmethod
    def _raise_for_status(resp: Any, *, context: str) -> None:
        status = getattr(resp, "status_code", 0)
        if status == 401:
            raise ConnectorFailure(f"ServiceNow rejected the credentials (401) while {context}")
        if status == 403:
            raise ConnectorFailure(
                f"ServiceNow returned 403 while {context}; the service account "
                "lacks write access to work notes"
            )
        if status and status >= 400:
            text = getattr(resp, "text", "")
            raise ConnectorFailure(f"ServiceNow returned {status} while {context}: {text[:200]}")

    @staticmethod
    def _result(resp: Any) -> list[dict[str, Any]]:
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorFailure("ServiceNow returned a non-JSON response") from exc
        return payload.get("result", []) if isinstance(payload, dict) else []
