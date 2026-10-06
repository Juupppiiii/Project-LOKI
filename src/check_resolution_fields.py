"""Measure how often resolution-related fields are populated on manual tickets.

Samples the manual incident set and reports, for each candidate field, the
percent non-empty and a short example, so we only add fields that carry real
'how it was resolved' content.
"""

import requests
from requests.auth import HTTPBasicAuth

import config

SAMPLE = 1000

CANDIDATES = [
    "close_notes",
    "work_notes",
    "comments",
    "comments_and_work_notes",
    "description",
    "cause",
    "actions_taken",
    "lessons_learned",
    "u_instructions",
    "resolution_code",
    "close_code",
    "resolved_at",
    "resolved_by",
    "u_email_notes",
    "knowledge",
]


def _val(v):
    if isinstance(v, dict):
        return v.get("display_value") or v.get("value") or ""
    return v if v is not None else ""


def main():
    url = f"https://{config.INSTANCE}/api/now/table/incident"
    params = {
        "sysparm_limit": SAMPLE,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join(CANDIDATES),
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
        print(resp.text[:400])
        return

    rows = resp.json().get("result", [])
    n = len(rows)
    print(f"Sampled {n} manual incidents\n")
    if not n:
        return

    print(f"{'field':<26}{'%filled':>8}   example")
    print("-" * 90)
    for field in CANDIDATES:
        filled = 0
        example = ""
        for r in rows:
            v = str(_val(r.get(field, ""))).strip()
            if v:
                filled += 1
                if not example:
                    example = v.replace("\n", " ")[:60]
        pct = 100.0 * filled / n
        print(f"{field:<26}{pct:>7.1f}%   {example}")


if __name__ == "__main__":
    main()
