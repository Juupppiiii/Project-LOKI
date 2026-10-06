"""Discover which fields/values encode 'manual vs automated' on incidents.

Pulls a recent sample of incidents with the full field set (no field
projection) and reports:
  - all field names present
  - distinct value frequencies for the likely 'manual' signal fields
  - a scan of any field whose name or value hints at automation

Read-only sampling, no bulk pull.
"""

import collections

import requests
from requests.auth import HTTPBasicAuth

import config

SAMPLE = 500

# Fields most likely to distinguish manual vs automated handling.
SIGNAL_FIELDS = [
    "sys_created_by",
    "sys_updated_by",
    "closed_by",
    "resolved_by",
    "opened_by",
    "close_code",
    "resolution_code",
    "correlation_id",
    "correlation_display",
    "contact_type",
    "u_cause_category",
    "u_cause_subcategory",
]


def _val(field_value):
    """Normalise a ServiceNow field (may be dict with display_value or str)."""

    if isinstance(field_value, dict):
        return field_value.get("display_value") or field_value.get("value") or ""
    return field_value


def main():
    url = f"https://{config.INSTANCE}/api/now/table/incident"
    params = {
        "sysparm_limit": SAMPLE,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        # No sysparm_fields -> return everything so we can see what exists.
        "sysparm_query": f"{config.query_for('incident')}^ORDERBYDESCsys_created_on",
    }

    resp = requests.get(
        url, params=params,
        auth=HTTPBasicAuth(config.USERNAME, config.PASSWORD),
        headers={"Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )

    print("Status:", resp.status_code)
    if resp.status_code != 200:
        print(resp.text[:500])
        return

    rows = resp.json().get("result", [])
    print(f"Sampled {len(rows)} incidents\n")

    if not rows:
        return

    # All field names present in the sample.
    all_fields = sorted(rows[0].keys())
    print("FIELDS PRESENT (%d):" % len(all_fields))
    print(", ".join(all_fields))
    print()

    # Highlight any field whose NAME hints at automation.
    hint_fields = [
        f for f in all_fields
        if any(k in f.lower() for k in
               ("auto", "workflow", "bot", "integration", "orchestr",
                "manual", "flow", "resolution_type"))
    ]
    print("FIELDS whose name hints at automation:", hint_fields or "(none)")
    print()

    # Distinct value frequencies for the signal fields that exist.
    for field in SIGNAL_FIELDS:
        if field not in all_fields:
            continue
        counter = collections.Counter(_val(r.get(field, "")) for r in rows)
        print(f"--- {field} (top 15) ---")
        for value, n in counter.most_common(15):
            shown = value if value != "" else "(empty)"
            print(f"  {n:>4}  {shown}")
        print()


if __name__ == "__main__":
    main()
