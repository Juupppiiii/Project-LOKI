"""Print row counts per table for the scoped queries (fast stats API)."""

import requests
from requests.auth import HTTPBasicAuth

import config


def count(table, query):
    url = f"https://{config.INSTANCE}/api/now/stats/{table}"
    params = {"sysparm_count": "true"}
    if query:
        params["sysparm_query"] = query
    r = requests.get(
        url, params=params,
        auth=HTTPBasicAuth(config.USERNAME, config.PASSWORD),
        headers={"Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    return int(r.json()["result"]["stats"]["count"])


def main():
    total = 0
    for table in config.TABLES:
        c = count(table, config.query_for(table))
        if isinstance(c, int):
            total += c
        print(f"{table:16} {c:>12,}" if isinstance(c, int) else f"{table:16} {c}")
    print("-" * 30)
    print(f"{'TOTAL':16} {total:>12,}")


if __name__ == "__main__":
    main()
