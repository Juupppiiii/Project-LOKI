"""Diagnostic: verify auth and whether the incident date filter is applied.

Makes a few tiny stats/aggregate calls (no bulk data) so we can confirm the
connection works and that "last 3 months" actually filters instead of silently
returning the whole table.
"""

import requests
from requests.auth import HTTPBasicAuth

import config


def _count(table, query=None):
    """Return the row count for a table/query using the aggregate API."""

    url = f"https://{config.INSTANCE}/api/now/stats/{table}"
    params = {"sysparm_count": "true"}
    if query:
        params["sysparm_query"] = query

    resp = requests.get(
        url,
        params=params,
        auth=HTTPBasicAuth(config.USERNAME, config.PASSWORD),
        headers={"Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )

    if resp.status_code != 200:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"

    return resp.json()["result"]["stats"]["count"]


def main():
    print("Instance:", config.INSTANCE)
    print("User:", config.USERNAME)
    print("Password loaded:", bool(config.PASSWORD))
    print("-" * 50)

    print("incident query used by extractor:")
    print(" ", config.query_for("incident"))
    print("-" * 50)

    print("incident total (no filter)      :", _count("incident"))
    print("incident with date window       :",
          _count("incident", config.DATE_WINDOW))
    print("incident with full extractor qry:",
          _count("incident", config.query_for("incident")))


if __name__ == "__main__":
    main()
