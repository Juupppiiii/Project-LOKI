"""Pull closed/resolved incidents and show HOW they were resolved.

Read-only. Issues GETs only against the ServiceNow Table API, reusing the
project's own extractor (auth, retry/backoff, field list) from src/. It does
NOT touch the normal discovery pipeline config or outputs.

Goal: gather up to N closed incidents that actually carry resolution content
(close_notes / work_notes) so we can see the resolution steps and notes -- the
raw material for drafting new BUCKY procedures.

    python scripts/pull_closed_incidents.py                 # 5000, default
    python scripts/pull_closed_incidents.py --limit 2000
    python scripts/pull_closed_incidents.py --out data/closed_incidents.csv
    python scripts/pull_closed_incidents.py --show 5        # preview N on stdout

Output: a CSV under data/ plus a short on-screen summary (how many had
close_notes vs work_notes, the top close_codes, and a few examples).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# scripts/ lives one level under the project root; put the root AND src/ on the
# path so we can reuse the extractor and its config exactly.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for p in (str(ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402
from requests.auth import HTTPBasicAuth  # noqa: E402

import config  # noqa: E402  (src/config.py)
import extractor  # noqa: E402  (src/extractor.py)

# Fields we care about for "how was it resolved". Reuse the incident field list
# from config so we get close_notes/work_notes/comments/close_code/etc., which
# are already defined there.
FIELDS = config.fields_for("incident")

# Closed/resolved incidents that actually carry a resolution summary. active=false
# covers both Resolved and Closed states; requiring close_notes to be non-empty
# keeps rows that tell us something about the fix. u_record_type=incident keeps
# other record types out of the shared incident table.
CLOSED_QUERY = (
    "active=false"
    "^u_record_type=incident"
    "^close_notesISNOTEMPTY"
)


def pull(limit: int, auth: HTTPBasicAuth) -> pd.DataFrame:
    """Page through closed incidents newest-first until we have `limit` rows."""

    url = f"https://{config.INSTANCE}/api/now/table/incident"
    # Newest first so a partial pull still surfaces the most recent fixes.
    sysparm_query = f"{CLOSED_QUERY}^ORDERBYDESCsys_updated_on"
    page_size = min(config.PAGE_SIZE, 1000)

    base = {
        "sysparm_limit": page_size,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": ",".join(FIELDS),
        "sysparm_query": sysparm_query,
    }

    rows: list[dict] = []
    offset = 0
    while len(rows) < limit:
        params = dict(base, sysparm_offset=offset)
        page = extractor._request_page(url, params, auth)  # reuse retry/backoff
        if not page:
            break
        rows.extend(page)
        print(f"  offset {offset:>7,}: +{len(page):,} (total {len(rows):,})")
        offset += len(page)

    return pd.DataFrame(rows[:limit])


def summarise(df: pd.DataFrame, show: int) -> None:
    total = len(df)
    print("\n" + "=" * 60)
    print(f"PULLED {total:,} closed incidents with resolution content")
    print("=" * 60)
    if total == 0:
        print("No rows returned. Check credentials / query / instance.")
        return

    def _nonempty(col: str) -> int:
        if col not in df.columns:
            return 0
        return int(df[col].astype(str).str.strip().replace("nan", "").astype(bool).sum())

    print(f"  with close_notes : {_nonempty('close_notes'):,}")
    print(f"  with work_notes  : {_nonempty('work_notes'):,}")
    print(f"  with comments    : {_nonempty('comments'):,}")

    if "close_code" in df.columns:
        print("\n  Top close_code values:")
        for code, n in df["close_code"].fillna("(none)").value_counts().head(10).items():
            print(f"    {n:>5,}  {code}")

    if "assignment_group" in df.columns:
        print("\n  Top assignment groups:")
        for grp, n in df["assignment_group"].fillna("(none)").value_counts().head(10).items():
            print(f"    {n:>5,}  {grp}")

    if show > 0:
        print("\n" + "-" * 60)
        print(f"First {show} examples (how it was resolved):")
        print("-" * 60)
        for _, r in df.head(show).iterrows():
            print(f"\n[{r.get('number','?')}] {str(r.get('short_description',''))[:90]}")
            print(f"  group     : {r.get('assignment_group','')}")
            print(f"  close_code: {r.get('close_code','')}")
            notes = str(r.get("close_notes", "") or "").strip().replace("\n", " ")
            work = str(r.get("work_notes", "") or "").strip().replace("\n", " ")
            print(f"  close_notes: {notes[:300]}")
            if work:
                print(f"  work_notes : {work[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5000, help="max incidents to pull")
    ap.add_argument(
        "--out",
        default=str(ROOT / "data" / "closed_incidents.csv"),
        help="output CSV path",
    )
    ap.add_argument("--show", type=int, default=5, help="preview N examples on stdout")
    args = ap.parse_args()

    if not config.PASSWORD:
        print(
            "ServiceNow password is not configured. Set SERVICENOW_PASSWORD "
            "(or SN_PASSWORD) in the environment / .env.",
            file=sys.stderr,
        )
        return 2

    auth = HTTPBasicAuth(config.USERNAME, config.PASSWORD)
    print(f"Instance : {config.INSTANCE}")
    print(f"Query    : {CLOSED_QUERY}")
    print(f"Target   : up to {args.limit:,} rows (read-only GET)\n")

    df = pull(args.limit, auth)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df):,} rows -> {out}")

    summarise(df, args.show)
    return 0


if __name__ == "__main__":
    sys.exit(main())
