"""Bounded test extract.

Pulls a single small page of incidents (most recent first) using the real
incident field set and date filter, then writes data/incidents_test.csv.
This validates auth, query, pagination and CSV schema without pulling the
full ~600k-row window.
"""

import os

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

import config

TEST_LIMIT = int(os.getenv("SN_TEST_LIMIT", "200"))
OUTPUT_FILE = "incidents_test.csv"


def main():
    print("=" * 50)
    print("BOUNDED TEST EXTRACT: incident")
    print("=" * 50)
    print(f"Instance : {config.INSTANCE}")
    print(f"User     : {config.USERNAME}")
    print(f"Limit    : {TEST_LIMIT}")

    query = config.query_for("incident")
    # Most recent first so the sample is meaningful.
    sysparm_query = f"{query}^ORDERBYDESCsys_created_on"

    url = f"https://{config.INSTANCE}/api/now/table/incident"
    params = {
        "sysparm_limit": TEST_LIMIT,
        "sysparm_offset": 0,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join(config.fields_for("incident")),
        "sysparm_query": sysparm_query,
    }

    resp = requests.get(
        url,
        params=params,
        auth=HTTPBasicAuth(config.USERNAME, config.PASSWORD),
        headers={"Accept": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )

    print(f"Status   : {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:500])
        return

    records = resp.json().get("result", [])
    print(f"Retrieved: {len(records)} records")

    df = pd.DataFrame(records)
    print(f"Columns  : {len(df.columns)}")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    out = os.path.join(config.OUTPUT_DIR, OUTPUT_FILE)
    df.to_csv(out, index=False)
    print(f"Saved    : {out}")

    # Quick schema sanity check against what the analyzer expects.
    for col in ("short_description", "number"):
        print(f"  has '{col}': {col in df.columns}")

    print("TEST EXTRACT COMPLETE")


if __name__ == "__main__":
    main()
