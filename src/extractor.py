"""ServiceNow table extractor.

Config-driven, paginated and resilient. All tuning lives in ``config.py``;
credentials come from the environment (.env) with an interactive prompt as a
last-resort fallback so the module can still be run by hand.
"""

import os
import sys
import time
from getpass import getpass

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

import config

# Hard safety cap so a misbehaving endpoint can never loop forever.
MAX_PAGES = int(os.getenv("SN_MAX_PAGES", "1000"))


def _get_password():
    """Return the ServiceNow password from config/env.

    Prompts only when a real terminal is attached; in a scheduled / CI run it
    fails loudly instead of blocking forever on stdin.
    """

    if config.PASSWORD:
        return config.PASSWORD

    if sys.stdin is not None and sys.stdin.isatty():
        # getpass keeps the entry off the screen.
        return getpass("Enter ServiceNow Password: ")

    raise RuntimeError(
        "SN_PASSWORD is not set and no interactive terminal is available. "
        "Set SN_PASSWORD in the environment or .env file."
    )


def _expected_count(table_name, query, auth):
    """Return the row count the aggregate API reports for this query.

    Used as the authoritative stopping target so pagination does not quit
    early when ServiceNow returns a truncated (short but non-empty) page.
    Returns None if the count cannot be determined.
    """

    url = f"https://{config.INSTANCE}/api/now/stats/{table_name}"
    params = {"sysparm_count": "true"}
    if query:
        params["sysparm_query"] = query

    try:
        resp = requests.get(
            url,
            params=params,
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return int(resp.json()["result"]["stats"]["count"])
    except (requests.RequestException, KeyError, ValueError):
        pass

    return None


def _request_page(url, params, auth):
    """Perform a single GET with retry + exponential backoff.

    Returns the parsed ``result`` list. Raises on persistent failure so the
    caller can mark the whole table as failed.
    """

    last_error = None

    for attempt in range(1, config.MAX_RETRIES + 1):

        retry_after = None

        try:

            response = requests.get(
                url,
                params=params,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=config.REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                return response.json().get("result", [])

            # Retry on transient server / throttling errors only.
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                # Honor server-directed backoff when rate limited.
                retry_after = response.headers.get("Retry-After")
            else:
                # Client errors (401, 403, 404, ...) will not fix themselves.
                raise RuntimeError(
                    f"HTTP {response.status_code}: {response.text[:500]}"
                )

        except requests.RequestException as exc:
            last_error = str(exc)
            retry_after = None

        if attempt < config.MAX_RETRIES:
            wait = config.RETRY_BACKOFF ** attempt
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            print(
                f"  Attempt {attempt} failed ({last_error}); "
                f"retrying in {wait:.1f}s"
            )
            time.sleep(wait)

    raise RuntimeError(
        f"Giving up after {config.MAX_RETRIES} attempts: {last_error}"
    )


def extract_table(table_name, output_file, auth=None):
    """Extract a full ServiceNow table to CSV using offset pagination."""

    print("\n" + "=" * 50)
    print(f"Extracting: {table_name}")
    print("=" * 50)

    if auth is None:
        auth = HTTPBasicAuth(config.USERNAME, _get_password())

    url = f"https://{config.INSTANCE}/api/now/table/{table_name}"

    # A stable sort makes offset pagination deterministic. Without it,
    # ServiceNow does not guarantee row order across pages, so offsets can
    # overlap or skip records. Append the ordering to any per-table filter.
    table_query = config.query_for(table_name)
    if table_query:
        sysparm_query = f"{table_query}^ORDERBYsys_created_on"
    else:
        sysparm_query = "ORDERBYsys_created_on"

    base_params = {
        "sysparm_limit": config.PAGE_SIZE,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join(config.fields_for(table_name)),
        "sysparm_query": sysparm_query,
    }

    # Authoritative target so we don't stop on a truncated page. On this
    # instance ORDERBY + sysparm_offset can return a short-but-non-empty page
    # before the real end, so "short page == done" is unreliable.
    expected = _expected_count(table_name, table_query, auth)
    if expected is not None:
        print(f"  expected rows: {expected:,}")

    all_records = []
    offset = 0

    for page_num in range(MAX_PAGES):

        params = dict(base_params, sysparm_offset=offset)

        page = _request_page(url, params, auth)

        all_records.extend(page)

        print(f"  offset {offset:>7,}: +{len(page):,} (total {len(all_records):,})")

        # Stop only on a genuinely empty page, or once we've collected the
        # expected total. Advance by the actual page length so a truncated
        # page doesn't create an offset gap.
        if not page:
            break
        if expected is not None and len(all_records) >= expected:
            break

        offset += len(page)

    else:
        print(
            f"  WARNING: hit MAX_PAGES ({MAX_PAGES}) for {table_name}; "
            "output may be incomplete."
        )

    if expected is not None and len(all_records) < expected:
        print(
            f"  WARNING: collected {len(all_records):,} of {expected:,} "
            f"expected rows for {table_name}."
        )

    df = pd.DataFrame(all_records)

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(config.OUTPUT_DIR, output_file)
    df.to_csv(output_path, index=False)

    print(f"Saved {len(df):,} records -> {output_path}")

    return len(df)


def main():

    print("=" * 60)
    print("SERVICENOW MULTI-TABLE EXTRACTOR")
    print("=" * 60)

    # Resolve credentials once and reuse for every table.
    auth = HTTPBasicAuth(config.USERNAME, _get_password())

    for table_name, file_name in config.TABLES.items():

        try:
            extract_table(table_name, file_name, auth=auth)
        except Exception as exc:
            print(f"FAILED: {table_name}")
            print(f"  {exc}")

    print("\nEXTRACTION COMPLETE")


if __name__ == "__main__":
    main()
