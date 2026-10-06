"""Group-by distributions for the manual-vs-automated signal fields.

Uses the aggregate API (sysparm_group_by) so we see the full breakdown across
the entire 3-month incident window without pulling rows.
"""

import requests
from requests.auth import HTTPBasicAuth

import config

BASE_QUERY = config.query_for("incident")


def group_count(field):
    url = f"https://{config.INSTANCE}/api/now/stats/incident"
    params = {
        "sysparm_count": "true",
        "sysparm_group_by": field,
        "sysparm_query": BASE_QUERY,
        "sysparm_display_value": "true",
    }
    r = requests.get(
        url, params=params,
        auth=HTTPBasicAuth(config.USERNAME, config.PASSWORD),
        headers={"Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json().get("result", []), None


def show(field):
    print(f"\n===== {field} =====")
    result, err = group_count(field)
    if err:
        print(" ", err)
        return
    rows = []
    for item in result:
        grouped = item.get("groupby_fields", [])
        label = grouped[0].get("value") if grouped else "?"
        count = int(item["stats"]["count"])
        rows.append((count, label or "(empty)"))
    for count, label in sorted(rows, reverse=True)[:25]:
        print(f"  {count:>8,}  {label}")


def main():
    print("3-month incident window. Field value distributions:")
    for field in ("contact_type", "sys_created_by", "opened_by", "state", "incident_state"):
        show(field)


if __name__ == "__main__":
    main()
