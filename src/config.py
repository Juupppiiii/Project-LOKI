# src/config.py
"""Central configuration for the Phase 1 Discovery framework.

All modules read from here so there is a single source of truth for the
ServiceNow instance, credentials, tables, fields and tuning parameters.
"""

import os

from dotenv import load_dotenv

# Load variables from a local .env file if present. This is a no-op when the
# file does not exist, so it is safe in CI / scheduled environments where the
# values come from real environment variables instead.
load_dotenv()

# ------------------------------------------------------------------
# ServiceNow connection
# ------------------------------------------------------------------

# Accept several env var spellings so an existing .env "just works":
#   SERVICENOW_* (used in this project's .env), then SN_* fallbacks.
# The REST instance takes priority since that is the basic-auth endpoint.
_raw_instance = (
    os.getenv("SERVICENOW_REST_INSTANCE")
    or os.getenv("SERVICENOW_INSTANCE")
    or os.getenv("SN_INSTANCE")
    or "tpicapdev.service-now.com"
)

# The extractor builds "https://{INSTANCE}/..." itself, so strip any scheme
# and trailing slash from the configured value.
INSTANCE = _raw_instance.replace("https://", "").replace("http://", "").rstrip("/")

USERNAME = (
    os.getenv("SERVICENOW_USERNAME")
    or os.getenv("SN_USERNAME")
    or os.getenv("SN_USER")
    or "api_read_only"
)

PASSWORD = os.getenv("SERVICENOW_PASSWORD") or os.getenv("SN_PASSWORD")

# ------------------------------------------------------------------
# Extraction tuning
# ------------------------------------------------------------------

PAGE_SIZE = int(os.getenv("SN_PAGE_SIZE", "5000"))

MAX_RETRIES = int(os.getenv("SN_MAX_RETRIES", "3"))

RETRY_BACKOFF = float(os.getenv("SN_RETRY_BACKOFF", "2.0"))

REQUEST_TIMEOUT = int(os.getenv("SN_REQUEST_TIMEOUT", "120"))

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------

OUTPUT_DIR = os.getenv("SN_OUTPUT_DIR", "data")

ANALYSIS_DIR = os.getenv("SN_ANALYSIS_DIR", "output")

DUCKDB_PATH = os.getenv("SN_DUCKDB_PATH", "automation_discovery.duckdb")

# ------------------------------------------------------------------
# Tables to extract (ServiceNow table name -> output CSV file)
# ------------------------------------------------------------------

TABLES = {
    "incident": "incidents.csv",
    "change_request": "changes.csv",
    "change_task": "change_tasks.csv",
    "sc_request": "requests.csv",
    "sc_req_item": "request_items.csv",
    "sc_task": "catalog_tasks.csv",
    "problem": "problems.csv",
    "problem_task": "problem_tasks.csv",
}

# ------------------------------------------------------------------
# Rolling date window
# ------------------------------------------------------------------
# When a table's query below contains "{DATE_WINDOW}" it is replaced with a
# ServiceNow relative date filter on sys_created_on. Default matches the
# incident URL shared for the project ("Last 3 months").
DATE_WINDOW_MONTHS = int(os.getenv("SN_DATE_WINDOW_MONTHS", "3"))

DATE_WINDOW = (
    "sys_created_onONLast {n} months"
    "@javascript:gs.beginningOfLast{n}Months()"
    "@javascript:gs.endOfLast{n}Months()"
).format(n=DATE_WINDOW_MONTHS)

# ------------------------------------------------------------------
# Default fields (used by any table without its own entry in TABLE_FIELDS)
# ------------------------------------------------------------------

DEFAULT_FIELDS = [
    "number",
    "short_description",
    "description",
    "close_notes",
    "work_notes",
    "assignment_group",
    "assigned_to",
    "priority",
    "state",
    "category",
    "subcategory",
    "cmdb_ci",
    "resolved_by",
    "reopen_count",
    "reassignment_count",
    "business_duration",
    "calendar_duration",
    "sys_created_on",
    "sys_updated_on",
]

# ------------------------------------------------------------------
# Per-table field overrides. A table not listed here uses DEFAULT_FIELDS.
# The incident set mirrors the full field list from the project's real
# incident query (custom u_* fields included).
# ------------------------------------------------------------------

TABLE_FIELDS = {
    "incident": [
        "number",
        "short_description",
        "contact_type",
        "assignment_group",
        "assigned_to",
        "opened_by",
        "priority",
        "state",
        "problem_id",
        "rfc",
        "caused_by",
        "parent_incident",
        "source_incident",
        "u_related_ticket",
        "source_ci",
        "u_it_service",
        "u_impacted_application_software",
        "u_outage",
        "child_incidents",
        "cause",
        "u_cause_category",
        "u_cause_subcategory",
        # Resolution content: close_notes holds the fix summary, work_notes the
        # step-by-step log, comments the customer-facing notes. resolution_code
        # is empty on this instance but kept in case older records use it.
        "description",
        "close_notes",
        "work_notes",
        "comments",
        "close_code",
        "resolution_code",
        "resolved_at",
        "cmdb_ci",
        "reopen_count",
        "reopened_by",
        "reopened_time",
        "open_count",
        "close_count",
        "sys_mod_count",
        "u_duplicate_count",
        "resolved_by",
        "reassignment_count",
        "business_stc",
        "calendar_stc",
        "sys_created_on",
        "sys_updated_on",
        "u_assignment_time",
        "actual_disruption_time",
        "estd_disruption_time",
        "business_impact",
        "bs_impact",
        "affected_users",
        "u_impacted_volume",
        "u_client",
        "u_major_incident",
        "major_incident_state",
        "location",
        "u_trader",
        "u_isin",
        "u_invoice_value",
        "u_company_impacted",
    ],
}

# ------------------------------------------------------------------
# Per-table encoded query (sysparm_query). Optional. "{DATE_WINDOW}" is
# substituted with DATE_WINDOW above. A table not listed here is extracted
# unfiltered (ordered by sys_created_on for stable pagination).
# ------------------------------------------------------------------

# Set to True (or env SN_MANUAL_ONLY=1) to restrict incidents to
# human-originated ("manual") tickets. On this instance the bulk of incidents
# are machine-created with contact_type=Automated (service account
# svcgbsnsoap / GATE Import Account); manual tickets are Email / Phone /
# Instant Message / Walk-in / Self-service / Virtual Agent.
MANUAL_ONLY = os.getenv("SN_MANUAL_ONLY", "1") not in ("0", "false", "False", "")

_MANUAL_FILTER = "^contact_type!=Automated" if MANUAL_ONLY else ""

TABLE_QUERIES = {
    "incident": "{DATE_WINDOW}^u_record_type=incident" + _MANUAL_FILTER,
    # Scope every table to the same rolling window so no full-table (millions
    # of rows) pull happens. All these tables have sys_created_on.
    "change_request": "{DATE_WINDOW}",
    "change_task": "{DATE_WINDOW}",
    "sc_request": "{DATE_WINDOW}",
    "sc_req_item": "{DATE_WINDOW}",
    "sc_task": "{DATE_WINDOW}",
    "problem": "{DATE_WINDOW}",
    "problem_task": "{DATE_WINDOW}",
}


def fields_for(table_name):
    """Return the field list to request for a given table."""

    return TABLE_FIELDS.get(table_name, DEFAULT_FIELDS)


def query_for(table_name):
    """Return the encoded query for a table, or None if unfiltered."""

    template = TABLE_QUERIES.get(table_name)

    if not template:
        return None

    return template.replace("{DATE_WINDOW}", DATE_WINDOW)
