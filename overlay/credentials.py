"""Access credentials centre.

The point of this module is to be the one place that answers "what is the agent
allowed to reach, as whom, and with what rights" -- without becoming a password
database.

Why it is built this way
------------------------
The obvious design is a form that saves passwords into the application
database. That would be a bad trade here. This app currently has no real
authentication (`X-Actor` is a header the client asserts), so a table of domain
and service-account passwords would be the most attractive target in the estate,
reachable by anyone who can reach the port.

So a credential record stores:

  * a POINTER to the secret (`secret_ref`), never the secret itself
  * the account it authenticates as
  * the scope it may act on (hosts, groups) -- an allowlist, not a hint
  * the permissions it is *expected* to have, for review
  * who registered it and when it was last proven to work

Where the secret actually lives, in order of preference:

  ``delinea:<path>``   the enterprise vault. Correct target for production;
                       resolution needs a Delinea connector, which does not
                       exist yet, so these read as "not resolvable here".
  ``env:<VAR>``        process environment. How the ServiceNow credentials
                       already work. Nothing is persisted by this app.
  ``keyring:<svc>/<a>``  the OS credential store (Windows Credential Manager via
                       `keyring`). Used when someone pastes a value into the UI:
                       it goes to the OS vault, and only the pointer is saved.

A secret value is never returned by the API, never logged, and never written to
the database. `resolve()` exists so a future connector can fetch a value at the
moment of use; nothing calls it today because execution is still simulated.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .domain import DomainError, new_id, now_iso

try:  # OS credential store; optional so the module still imports without it
    import keyring

    _KEYRING = True
except Exception:  # pragma: no cover
    _KEYRING = False


KEYRING_SERVICE = "overlay-credentials"

# Connectors the agent could plausibly speak to, with the read/write split that
# matters when reviewing what a credential is for.
CONNECTORS: dict[str, dict[str, Any]] = {
    "servicenow": {
        "label": "ServiceNow",
        "reads": "incidents, requests, tasks",
        "writes": "work notes, state changes",
        "scope_hint": "instance host, e.g. tpicapdev.service-now.com",
    },
    "winrm": {
        "label": "Windows (WinRM / PowerShell remoting)",
        "reads": "services, disks, processes, event logs",
        "writes": "service restarts, file removal, reboots",
        "scope_hint": "hostnames, e.g. ldn2ws9696",
    },
    "ssh": {
        "label": "Linux (SSH)",
        "reads": "systemctl, df, du, journald",
        "writes": "service starts, file removal",
        "scope_hint": "hostnames, e.g. ldn1lx3594",
    },
    "activedirectory": {
        "label": "Active Directory",
        "reads": "user and account state",
        "writes": "unlock, password reset, group membership",
        "scope_hint": "OU or domain, e.g. corp.ad.tullib.com",
    },
    "vcenter": {
        "label": "VMware vCenter",
        "reads": "host and VM health",
        "writes": "power operations",
        "scope_hint": "vCenter host",
    },
    "http": {
        "label": "Generic HTTP API",
        "reads": "whatever the endpoint exposes",
        "writes": "whatever the endpoint exposes",
        "scope_hint": "base URL or hostname",
    },
}

_REF_RE = re.compile(r"^(env|keyring|delinea):(.+)$", re.I)


@dataclass
class Credential:
    credential_id: str
    name: str
    connector: str
    account: str
    secret_ref: str
    scope: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    enabled: bool = True
    note: str = ""
    created_by: str = "unknown"
    created_at: str = field(default_factory=now_iso)
    last_tested_at: str | None = None
    last_test_ok: bool | None = None
    last_test_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- validation


def validate(
    name: str,
    connector: str,
    account: str,
    secret_ref: str,
    scope: list[str],
) -> None:
    if not name.strip():
        raise DomainError("give the credential a name you will recognise later")

    if connector not in CONNECTORS:
        raise DomainError(
            f"unknown connector '{connector}'. "
            f"Choose one of: {', '.join(sorted(CONNECTORS))}"
        )

    if not account.strip():
        raise DomainError("account is required -- who does the agent act as?")

    m = _REF_RE.match(secret_ref.strip())
    if not m:
        raise DomainError(
            "secret_ref must point at a secret store, not contain the secret. "
            "Use env:VARNAME, keyring:service/account, or delinea:path."
        )

    # Catch the most likely mistake: pasting the password into the pointer.
    kind, target = m.group(1).lower(), m.group(2).strip()
    if kind == "env" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
        raise DomainError(
            f"'{target}' is not a valid environment variable name. "
            "Did you paste the secret itself?"
        )

    if not scope:
        raise DomainError(
            "scope cannot be empty. Name the hosts or groups this credential may "
            "act on -- an unscoped credential is an unbounded one."
        )


# ------------------------------------------------------------------ storing


def store_secret_in_os_vault(service_account: str, value: str) -> str:
    """Put a pasted secret in the OS credential store, return its pointer.

    This is the only path that handles a raw secret, and it does not keep it:
    the value goes to the OS vault and a `keyring:` pointer comes back.
    """

    if not _KEYRING:
        raise DomainError(
            "the OS credential store is unavailable (keyring not installed), so "
            "a pasted secret cannot be stored safely. Put the value in an "
            "environment variable and reference it as env:VARNAME instead."
        )
    if not value:
        raise DomainError("no value supplied")

    try:
        keyring.set_password(KEYRING_SERVICE, service_account, value)
    except Exception as exc:  # pragma: no cover - platform dependent
        raise DomainError(f"could not write to the OS credential store: {exc}") from None

    return f"keyring:{KEYRING_SERVICE}/{service_account}"


# ----------------------------------------------------------------- resolving


def describe_ref(secret_ref: str) -> dict[str, Any]:
    """Explain a pointer without resolving it. Safe to show in the UI."""

    m = _REF_RE.match(secret_ref or "")
    if not m:
        return {"kind": "invalid", "target": "", "resolvable": False,
                "detail": "not a recognised reference"}

    kind, target = m.group(1).lower(), m.group(2).strip()

    if kind == "env":
        present = target in os.environ
        return {
            "kind": "env",
            "target": target,
            "resolvable": present,
            "detail": (
                f"environment variable {target} is set"
                if present
                else f"environment variable {target} is not set in this process"
            ),
        }

    if kind == "keyring":
        if not _KEYRING:
            return {"kind": "keyring", "target": target, "resolvable": False,
                    "detail": "keyring is not installed"}
        service, _, account = target.partition("/")
        try:
            present = keyring.get_password(service or KEYRING_SERVICE, account) is not None
        except Exception as exc:  # pragma: no cover
            return {"kind": "keyring", "target": target, "resolvable": False,
                    "detail": f"keyring error: {exc}"}
        return {
            "kind": "keyring",
            "target": target,
            "resolvable": present,
            "detail": (
                "stored in the OS credential store"
                if present
                else "nothing stored under that name"
            ),
        }

    return {
        "kind": "delinea",
        "target": target,
        "resolvable": False,
        "detail": (
            "Delinea is the right home for this, but no Delinea connector exists "
            "yet, so it cannot be resolved from here."
        ),
    }


def resolve(secret_ref: str) -> str:
    """Fetch the actual secret. For connectors only; never for a response.

    Nothing calls this today -- execution is simulated. It exists so the shape of
    the seam is settled before connectors arrive.
    """

    m = _REF_RE.match(secret_ref or "")
    if not m:
        raise DomainError("not a recognised secret reference")

    kind, target = m.group(1).lower(), m.group(2).strip()

    if kind == "env":
        value = os.environ.get(target)
        if not value:
            raise DomainError(f"environment variable {target} is not set")
        return value

    if kind == "keyring":
        if not _KEYRING:
            raise DomainError("keyring is not installed")
        service, _, account = target.partition("/")
        value = keyring.get_password(service or KEYRING_SERVICE, account)
        if not value:
            raise DomainError("nothing stored under that keyring name")
        return value

    raise DomainError(
        "Delinea references cannot be resolved yet; no connector exists."
    )


# -------------------------------------------------------------------- tests


def test_credential(cred: Credential) -> dict[str, Any]:
    """Check a credential as far as is possible without connectors.

    Today this verifies the pointer resolves and reports what the connector
    *would* be allowed to do. It deliberately does not attempt a live login for
    anything except ServiceNow, which the overlay already talks to read-only.
    """

    ref = describe_ref(cred.secret_ref)

    if not ref["resolvable"]:
        return {
            "ok": False,
            "stage": "secret",
            "detail": ref["detail"],
            "checked_at": now_iso(),
        }

    if cred.connector == "servicenow":
        # The only connector that exists, and only for reading.
        try:
            from .live import ServiceNowReader, load_credentials

            creds = load_credentials()
            if not creds["present"]:
                return {
                    "ok": False,
                    "stage": "connect",
                    "detail": "ServiceNow credentials are not fully configured",
                    "checked_at": now_iso(),
                }
            result = ServiceNowReader().probe(
                {"table": "incident", "query": "", "page_size": 1}, creds
            )
            return {
                "ok": True,
                "stage": "connect",
                "detail": (
                    f"reached {result['instance']}, read {result['sample_returned']} "
                    f"record from {result['table']}"
                ),
                "checked_at": now_iso(),
            }
        except Exception as exc:
            return {
                "ok": False,
                "stage": "connect",
                "detail": str(exc)[:200],
                "checked_at": now_iso(),
            }

    return {
        "ok": True,
        "stage": "secret",
        "detail": (
            f"secret resolves ({ref['detail']}). No {cred.connector} connector "
            "exists yet, so the login itself is unverified."
        ),
        "checked_at": now_iso(),
    }


def connector_catalogue() -> list[dict[str, Any]]:
    """What the UI offers, described rather than hardcoded client-side."""

    return [
        {
            "id": key,
            "label": meta["label"],
            "reads": meta["reads"],
            "writes": meta["writes"],
            "scope_hint": meta["scope_hint"],
            "implemented": key == "servicenow",
        }
        for key, meta in sorted(CONNECTORS.items(), key=lambda kv: kv[1]["label"])
    ]


def new_credential(
    name: str,
    connector: str,
    account: str,
    secret_ref: str,
    scope: list[str],
    permissions: list[str],
    note: str,
    actor: str,
) -> Credential:
    validate(name, connector, account, secret_ref, scope)
    return Credential(
        credential_id=new_id("cred"),
        name=name.strip(),
        connector=connector,
        account=account.strip(),
        secret_ref=secret_ref.strip(),
        scope=[s.strip() for s in scope if s.strip()],
        permissions=[p.strip() for p in permissions if p.strip()],
        note=note.strip(),
        created_by=actor,
    )
