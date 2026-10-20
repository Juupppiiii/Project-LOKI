"""Live ticket intake from ServiceNow -- READ ONLY.

Three boundaries make this safe to switch on:

1. **Read-only by construction.** `_get()` is the only network call in this
   module and it only ever issues HTTP GET against the Table API. There is no
   code path that POSTs, PATCHes or DELETEs, so the overlay cannot write to
   ServiceNow even by mistake.
2. **Reading live data does not enable acting on it.** Ingest creates tickets and
   (optionally) proposed runs. Every run still needs human approval, and step
   execution is still simulated: `simulator.CONNECTORS_ENABLED` is False and
   `system2.executor.live_execute` still raises.
3. **Credentials never touch the database.** They are read from the environment
   via the project's existing config, and the API never returns them.

Off by default. A human has to enable it.
"""

from __future__ import annotations

import base64
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .coordination import Lease, new_lease_owner

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"

# Reuse the project's credential resolution rather than inventing a second one.
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

DEFAULT_FIELDS = [
    "number",
    "sys_id",
    "short_description",
    "description",
    "category",
    "subcategory",
    "assignment_group",
    "cmdb_ci",
    "priority",
    "state",
    "contact_type",
    "sys_created_on",
    "sys_updated_on",
]

SETTINGS_KEY = "live_feed"

# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------
# The Table API wants a ServiceNow "encoded query" -- a terse string like
# `active=true^u_record_type=incident^priorityIN1,2`. That is fine for an
# engineer and hostile to everyone else. So the UI offers plain dropdowns, and
# these definitions turn the chosen options into that string.
#
# Each filter names the encoded clause it produces. A `None` value means "no
# constraint", so leaving a dropdown on "Any" simply omits that clause.

# Ticket types live in DIFFERENT ServiceNow tables, so choosing a type changes
# the table the feed reads, not just a query clause. Each entry names:
#   table         the ServiceNow table to read
#   clause        an extra filter within that table (incidents share their table
#                 with other record types, so they still need u_record_type)
#   active_field  how "open" is expressed on that table (problems use a state
#                 code, not the `active` flag), or None if it has no such notion
WORK_TYPES: dict[str, dict[str, Any]] = {
    "incident": {
        "label": "Incidents â€” things that broke",
        "table": "incident",
        "clause": "u_record_type=incident",
        "active_field": "active=true",
    },
    "change": {
        "label": "Changes â€” planned work",
        "table": "change_request",
        "clause": None,
        "active_field": "active=true",
    },
    "request": {
        "label": "Requests â€” someone asked for something",
        "table": "sc_request",
        "clause": None,
        "active_field": "active=true",
    },
    "problem": {
        "label": "Problems â€” root-cause investigations",
        "table": "problem",
        "clause": None,
        "active_field": "active=true",
    },
}

QUERY_FILTERS: dict[str, dict[str, Any]] = {
    "work_type": {
        "label": "Type of work",
        "help": "Which kind of ticket to pull. Each type is a separate queue.",
        "default": "incident",
        # Options are derived from WORK_TYPES so the two never drift apart.
        "options": [
            {"value": key, "clause": None, "label": meta["label"]}
            for key, meta in WORK_TYPES.items()
        ],
    },
    "status": {
        "label": "Status",
        "help": "Open tickets are the ones still needing work.",
        "default": "open",
        # The actual "open" clause depends on the table, resolved in build_query.
        "options": [
            {"value": "open", "clause": "__active__", "label": "Open only"},
            {"value": "any", "clause": None, "label": "Open and closed"},
        ],
    },
    "raised_by": {
        "label": "Raised by",
        "help": "Only meaningful for incidents; ignored for other types.",
        "default": "people",
        "options": [
            {"value": "people", "clause": "contact_type!=Automated",
             "label": "People (exclude the machine feed)"},
            {"value": "any", "clause": None, "label": "Anyone, including automation"},
        ],
    },
    "priority": {
        "label": "Priority",
        "help": "Limit to the most urgent, or take everything.",
        "default": "any",
        "options": [
            {"value": "any", "clause": None, "label": "Any priority"},
            {"value": "p1", "clause": "priority=1", "label": "P1 only"},
            {"value": "p12", "clause": "priorityIN1,2", "label": "P1 and P2"},
            {"value": "p123", "clause": "priorityIN1,2,3", "label": "P1 to P3"},
        ],
    },
}

# Priority and status apply across the tables. work_type picks the table;
# raised_by only means anything for incidents.
_FILTER_ORDER = ["work_type", "status", "raised_by", "priority"]


def table_for(filters: dict[str, str]) -> str:
    """The ServiceNow table the chosen ticket type lives in."""

    wt = filters.get("work_type", "incident")
    return WORK_TYPES.get(wt, WORK_TYPES["incident"])["table"]


def build_query(filters: dict[str, str], group: str = "") -> str:
    """Turn dropdown choices into a ServiceNow encoded query for the right table.

    `work_type` selects the table (handled by `table_for`) and may add a
    record-type clause. `raised_by` only applies to incidents, since only
    incidents carry `contact_type`. `group` scopes to one assignment group.
    """

    wt_key = filters.get("work_type", "incident")
    wt = WORK_TYPES.get(wt_key, WORK_TYPES["incident"])
    clauses: list[str] = []

    # record-type clause (incidents share their table with other types)
    if wt["clause"]:
        clauses.append(wt["clause"])

    # status -> the table's own "open" expression
    if filters.get("status", "open") == "open" and wt["active_field"]:
        clauses.append(wt["active_field"])

    # raised_by -> incidents only
    if wt_key == "incident" and filters.get("raised_by", "people") == "people":
        clauses.append("contact_type!=Automated")

    # priority
    prio = filters.get("priority", "any")
    prio_clause = {
        "p1": "priority=1",
        "p12": "priorityIN1,2",
        "p123": "priorityIN1,2,3",
    }.get(prio)
    if prio_clause:
        clauses.append(prio_clause)

    if group.strip():
        clauses.append(f"assignment_group.name={group.strip()}")

    return "^".join(clauses)


def describe_query(filters: dict[str, str], group: str = "") -> str:
    """A one-line plain-English summary of the chosen filters, for the UI."""

    wt_key = filters.get("work_type", "incident")

    # Lead with the ticket type as a plural noun.
    noun = {
        "incident": "incidents",
        "change": "changes",
        "request": "requests",
        "problem": "problems",
    }.get(wt_key, "tickets")

    parts = ["open" if filters.get("status", "open") == "open" else "open and closed"]
    parts.append(noun)

    if wt_key == "incident" and filters.get("raised_by", "people") == "people":
        parts.append("raised by people (not the machine feed)")

    prio = filters.get("priority", "any")
    prio_word = {
        "p1": "P1 only",
        "p12": "P1 and P2",
        "p123": "P1 to P3",
    }.get(prio)
    if prio_word:
        parts.append(prio_word)

    summary = ", ".join(parts)
    if group.strip():
        summary += f", assigned to {group.strip()}"
    return summary


def query_builder_options() -> dict[str, Any]:
    """The dropdowns the UI should render, defined here rather than client-side."""

    return {
        "filters": [
            {
                "key": key,
                "label": spec["label"],
                "help": spec["help"],
                "default": spec["default"],
                "options": [
                    {"value": o["value"], "label": o["label"]}
                    for o in spec["options"]
                ],
            }
            for key, spec in ((k, QUERY_FILTERS[k]) for k in _FILTER_ORDER)
        ]
    }


def default_filters() -> dict[str, str]:
    return {k: QUERY_FILTERS[k]["default"] for k in _FILTER_ORDER}


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "source": "servicenow",
    "table": "incident",
    # Structured filters are the source of truth; `query` is derived from them.
    # The default set reproduces the original hand-written query:
    #   active=true^u_record_type=incident^contact_type!=Automated
    "filters": default_filters(),
    "group": "",
    # Kept for the advanced override and for anyone reading the effective query.
    # Recomputed from `filters` on every save unless an advanced raw query is set.
    "query": "active=true^u_record_type=incident^contact_type!=Automated",
    # When set, this raw query wins over the dropdowns (advanced escape hatch).
    "raw_query": "",
    "interval_seconds": 120,
    "page_size": 50,
    # When True a matching ticket becomes a proposed run automatically. It is
    # still only *proposed* -- approval and execution are untouched.
    "auto_propose": False,
    "only_groups": [],
}


class LiveFeedError(Exception):
    """A problem talking to the source. Surfaced to the UI, never silent."""


def load_credentials() -> dict[str, Any]:
    """Resolve instance/user/password from the project config or environment."""

    instance = username = password = None
    try:
        import config as sn_config  # src/config.py

        instance = sn_config.INSTANCE
        username = sn_config.USERNAME
        password = sn_config.PASSWORD
    except Exception:
        import os

        raw = (
            os.getenv("SERVICENOW_REST_INSTANCE")
            or os.getenv("SERVICENOW_INSTANCE")
            or os.getenv("SN_INSTANCE")
            or ""
        )
        instance = raw.replace("https://", "").replace("http://", "").rstrip("/")
        username = (
            os.getenv("SERVICENOW_USERNAME")
            or os.getenv("SN_USERNAME")
            or os.getenv("SN_USER")
        )
        password = os.getenv("SERVICENOW_PASSWORD") or os.getenv("SN_PASSWORD")

    return {
        "instance": instance or "",
        "username": username or "",
        "password": password or "",
        "present": bool(instance and username and password),
    }


class ServiceNowReader:
    """Minimal read-only Table API client."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def _get(self, url: str, creds: dict[str, Any], params: dict[str, Any]) -> Any:
        """The only network call in this module. GET only, by design."""

        token = base64.b64encode(
            f"{creds['username']}:{creds['password']}".encode()
        ).decode()
        try:
            resp = requests.get(
                url,
                headers={
                    "Authorization": f"Basic {token}",
                    "Accept": "application/json",
                },
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LiveFeedError(f"cannot reach ServiceNow: {exc}") from exc

        if resp.status_code == 401:
            raise LiveFeedError(
                "ServiceNow rejected the credentials (401). Check "
                "SERVICENOW_USERNAME / SERVICENOW_PASSWORD."
            )
        if resp.status_code == 403:
            raise LiveFeedError(
                "ServiceNow returned 403. The account lacks read access to this "
                "table, or REST access is restricted."
            )
        if resp.status_code >= 400:
            raise LiveFeedError(
                f"ServiceNow returned {resp.status_code}: {resp.text[:200]}"
            )

        try:
            return resp.json().get("result", [])
        except ValueError as exc:
            raise LiveFeedError("ServiceNow returned a non-JSON response") from exc

    def fetch(self, cfg: dict[str, Any], creds: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch the most recently updated tickets matching the query."""

        if not creds["present"]:
            raise LiveFeedError(
                "ServiceNow credentials are not configured. Set "
                "SERVICENOW_INSTANCE, SERVICENOW_USERNAME and "
                "SERVICENOW_PASSWORD (or the SN_* equivalents)."
            )

        table = cfg.get("table") or "incident"
        query = cfg.get("query") or ""
        # Newest first so a small page still surfaces current work.
        ordered = f"{query}^ORDERBYDESCsys_updated_on" if query else "ORDERBYDESCsys_updated_on"

        url = f"https://{creds['instance']}/api/now/table/{table}"
        params = {
            "sysparm_query": ordered,
            "sysparm_limit": int(cfg.get("page_size") or 50),
            # Display values sidestep reference objects entirely.
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": ",".join(DEFAULT_FIELDS),
        }
        return self._get(url, creds, params)

    def probe(self, cfg: dict[str, Any], creds: dict[str, Any]) -> dict[str, Any]:
        """Verify connectivity and permissions without ingesting anything."""

        records = self.fetch({**cfg, "page_size": 1}, creds)
        return {
            "reachable": True,
            "instance": creds["instance"],
            "table": cfg.get("table"),
            "sample_returned": len(records),
            "sample_number": (records[0].get("number") if records else None),
        }


class LiveFeed:
    """Owns the poller thread and the ingest loop."""

    def __init__(self, service) -> None:
        self.service = service
        self.store = service.store
        self.reader = ServiceNowReader()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._lease_name = "live-feed"
        self._owner_id = new_lease_owner("live-feed")
        self._lease: Lease | None = None
        self._lease_ttl = max(60, int(os.getenv("BUCKY_LIVE_LEASE_TTL_SECONDS", "120")))
        self._renew_interval = max(10, min(30, self._lease_ttl // 3))
        self.last_error: str | None = None
        self.last_poll_at: str | None = None
        self.last_fetched: int = 0
        self.last_new: int = 0
        self.total_ingested: int = 0

    # -- configuration ---------------------------------------------------

    def config(self) -> dict[str, Any]:
        stored = self.store.get_setting(SETTINGS_KEY) or {}
        cfg = {**DEFAULT_CONFIG, **stored}
        # Both `query` and `table` are DERIVED from the dropdowns, never trusted
        # from storage, so the chosen ticket type and the table actually read
        # cannot drift apart. The ticket type is the table -- incidents, changes,
        # requests and problems each live in their own ServiceNow table.
        cfg["query"] = self._effective_query(cfg)
        cfg["table"] = self._effective_table(cfg)
        return cfg

    def _effective_query(self, cfg: dict[str, Any]) -> str:
        """The query actually sent to ServiceNow.

        An advanced raw query, if set, wins over the dropdowns -- the escape
        hatch for the rare filter the builder does not cover. Otherwise the
        structured filters are assembled into the encoded query.
        """

        raw = str(cfg.get("raw_query") or "").strip()
        if raw:
            return raw
        filters = {**default_filters(), **(cfg.get("filters") or {})}
        return build_query(filters, cfg.get("group", ""))

    def _effective_table(self, cfg: dict[str, Any]) -> str:
        """The table actually read.

        On the advanced path the operator may have typed a table explicitly, so
        that wins. Otherwise the ticket type decides it.
        """

        if str(cfg.get("raw_query") or "").strip():
            return cfg.get("table") or "incident"
        filters = {**default_filters(), **(cfg.get("filters") or {})}
        return table_for(filters)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config()

        allowed = set(DEFAULT_CONFIG)
        unknown = set(updates) - allowed
        if unknown:
            raise LiveFeedError(
                f"unknown setting(s): {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(allowed))}"
            )

        # Validate structured filters against the known options.
        if "filters" in updates:
            incoming = updates["filters"] or {}
            if not isinstance(incoming, dict):
                raise LiveFeedError("filters must be an object")
            clean: dict[str, str] = {}
            for key, spec in QUERY_FILTERS.items():
                value = incoming.get(key, spec["default"])
                valid = {o["value"] for o in spec["options"]}
                if value not in valid:
                    raise LiveFeedError(
                        f"{spec['label']}: '{value}' is not an option "
                        f"({', '.join(sorted(valid))})"
                    )
                clean[key] = value
            updates["filters"] = clean

        if "interval_seconds" in updates:
            try:
                interval = int(updates["interval_seconds"])
            except (TypeError, ValueError):
                raise LiveFeedError("interval_seconds must be a number") from None
            # A floor protects the instance from being hammered.
            if interval < 30:
                raise LiveFeedError("interval_seconds must be at least 30")
            updates["interval_seconds"] = interval

        if "page_size" in updates:
            try:
                page = int(updates["page_size"])
            except (TypeError, ValueError):
                raise LiveFeedError("page_size must be a number") from None
            if not 1 <= page <= 200:
                raise LiveFeedError("page_size must be between 1 and 200")
            updates["page_size"] = page

        if "table" in updates and not str(updates["table"]).strip():
            raise LiveFeedError("table cannot be empty")

        cfg.update(updates)
        # Recompute the derived query and table so storage always reflects the
        # dropdown choices (the ticket type is the table).
        cfg["query"] = self._effective_query(cfg)
        cfg["table"] = self._effective_table(cfg)
        self.store.set_setting(SETTINGS_KEY, cfg)

        # Apply changes immediately. The supervisor remains alive while disabled
        # so every process can contend for leadership after a remote enable.
        self.start()
        if not cfg.get("enabled"):
            self._release_lease()

        return cfg

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        creds = load_credentials()
        active_lease = self.store.get_named_lease(self._lease_name)
        leader_here = bool(
            active_lease
            and self._lease
            and active_lease.owner_id == self._owner_id
            and active_lease.fencing_token == self._lease.fencing_token
        )
        supervising = bool(self._thread and self._thread.is_alive())
        return {
            "enabled": bool(cfg.get("enabled")),
            "running": bool(supervising and leader_here and cfg.get("enabled")),
            "supervising": supervising,
            "leader_active": active_lease is not None,
            "leader_here": leader_here,
            "fencing_token": self._lease.fencing_token if leader_here and self._lease else None,
            "source": cfg.get("source"),
            "table": cfg.get("table"),
            "query": cfg.get("query"),
            "filters": {**default_filters(), **(cfg.get("filters") or {})},
            "group": cfg.get("group", ""),
            "raw_query": cfg.get("raw_query", ""),
            "using_advanced_query": bool(str(cfg.get("raw_query") or "").strip()),
            "query_summary": describe_query(
                {**default_filters(), **(cfg.get("filters") or {})},
                cfg.get("group", ""),
            ),
            "interval_seconds": cfg.get("interval_seconds"),
            "page_size": cfg.get("page_size"),
            "auto_propose": bool(cfg.get("auto_propose")),
            "only_groups": cfg.get("only_groups") or [],
            # Host only. The password is never returned.
            "instance": creds["instance"],
            "credentials_present": creds["present"],
            "last_poll_at": self.last_poll_at,
            "last_error": self.last_error,
            "last_fetched": self.last_fetched,
            "last_new": self.last_new,
            "total_ingested": self.total_ingested,
            "counts": self.store.live_counts(),
            "access": "read-only (GET only; the overlay never writes to ServiceNow)",
            "execution": "simulated (reading live data does not enable live execution)",
        }

    def probe(self) -> dict[str, Any]:
        return self.reader.probe(self.config(), load_credentials())

    # -- ingest ----------------------------------------------------------

    def poll_once(self, actor: str = "live-feed") -> dict[str, Any]:
        """Fetch and ingest once, but only while this process owns the lease."""

        from .domain import LiveTicket, LiveTicketStatus, new_id
        from .service import normalize_ticket

        if not self._ensure_leader():
            raise LiveFeedError(
                "live-feed polling is currently owned by another application process"
            )

        with self._lock:
            cfg = self.config()
            creds = load_credentials()

            try:
                records = self.reader.fetch(cfg, creds)
                self.last_error = None
            except LiveFeedError as exc:
                self.last_error = str(exc)
                self.last_poll_at = _now()
                raise

            known = self.store.known_sys_ids(cfg.get("source") or "servicenow")
            only_groups = {
                g.strip().lower() for g in (cfg.get("only_groups") or []) if g.strip()
            }

            new_tickets: list[dict[str, Any]] = []
            skipped_group = 0

            for rec in records:
                sys_id = str(rec.get("sys_id") or "").strip()
                if not sys_id or sys_id in known:
                    continue

                ticket = normalize_ticket(rec)

                group = str(ticket.get("assignment_group") or "").strip().lower()
                if only_groups and group not in only_groups:
                    skipped_group += 1
                    continue

                match, _ranked, clean, applied = self.service._match(ticket)

                live = LiveTicket(
                    live_id=new_id("lt"),
                    ticket_number=str(ticket.get("number") or "(no number)"),
                    sys_id=sys_id,
                    source=cfg.get("source") or "servicenow",
                    table=cfg.get("table") or "incident",
                    payload=clean,
                    status=(
                        LiveTicketStatus.NEW if match else LiveTicketStatus.NO_MATCH
                    ),
                    match={
                        "matched": match is not None,
                        "procedure_id": (
                            match.procedure.procedure_id if match else None
                        ),
                        "procedure_name": match.procedure.name if match else None,
                        "score": float(match.score) if match else 0.0,
                        "missing_parameters": list(match.missing) if match else [],
                        "dictionary_terms_applied": applied,
                    },
                    sys_updated_on=str(ticket.get("sys_updated_on") or ""),
                )

                if not self.store.upsert_live_ticket(live):
                    continue

                known.add(sys_id)
                self.total_ingested += 1
                new_tickets.append(live.to_dict())

                # Optional: create the run immediately. Still only *proposed*.
                if match is not None and cfg.get("auto_propose"):
                    try:
                        run = self.service.propose(clean, actor=actor)
                        live.status = LiveTicketStatus.PROPOSED
                        live.run_id = run.run_id
                        self.store.update_live_ticket(live)
                    except Exception as exc:  # never let one bad ticket stop the poll
                        live.match["auto_propose_error"] = str(exc)
                        self.store.update_live_ticket(live)

            self.last_poll_at = _now()
            self.last_fetched = len(records)
            self.last_new = len(new_tickets)

            return {
                "fetched": len(records),
                "new": len(new_tickets),
                "skipped_not_in_group_filter": skipped_group,
                "tickets": new_tickets,
                "polled_at": self.last_poll_at,
            }

    # -- thread management and database-backed leadership ----------------

    def _ensure_leader(self) -> bool:
        if self._lease is not None:
            renewed = self.store.renew_named_lease(
                self._lease_name,
                self._owner_id,
                self._lease.fencing_token,
                self._lease_ttl,
            )
            if renewed is not None:
                self._lease = renewed
                return True
            self._lease = None

        acquired = self.store.try_acquire_named_lease(
            self._lease_name, self._owner_id, self._lease_ttl
        )
        self._lease = acquired
        return acquired is not None

    def _release_lease(self) -> bool:
        lease = self._lease
        if lease is None:
            return False
        released = self.store.release_named_lease(
            lease.name, self._owner_id, lease.fencing_token
        )
        self._lease = None
        return released

    def start(self) -> bool:
        """Start a lightweight supervisor; only the DB lease holder polls."""

        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._thread = None
            self._stop = threading.Event()
            thread = threading.Thread(
                target=self._loop, name="overlay-live-feed-supervisor", daemon=True
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if not (thread and thread.is_alive()):
                self._thread = None
                self._release_lease()
                return False
            self._stop.set()

        # The reader timeout is 30 seconds; never clear/reuse the event while an
        # old poll thread may still be alive.
        thread.join(timeout=35)
        with self._lifecycle_lock:
            if thread.is_alive():
                return False
            if self._thread is thread:
                self._thread = None
            self._release_lease()
            return True

    def _loop(self) -> None:
        next_poll = 0.0
        try:
            while not self._stop.is_set():
                cfg = self.config()
                if not cfg.get("enabled"):
                    self._release_lease()
                    next_poll = 0.0
                    self._stop.wait(self._renew_interval)
                    continue

                if not self._ensure_leader():
                    self._stop.wait(self._renew_interval)
                    continue

                now = time.monotonic()
                if now >= next_poll:
                    try:
                        self.poll_once()
                    except LiveFeedError:
                        pass
                    except Exception as exc:
                        self.last_error = f"unexpected poll failure: {exc}"
                    next_poll = time.monotonic() + int(
                        cfg.get("interval_seconds") or 120
                    )

                wait = min(
                    self._renew_interval,
                    max(1.0, next_poll - time.monotonic()),
                )
                self._stop.wait(wait)
        finally:
            self._release_lease()


def _now() -> str:
    from .domain import now_iso

    return now_iso()
