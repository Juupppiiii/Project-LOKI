"""Pipeline orchestrator for the Phase 1 Discovery framework.

Runs the three stages end to end:

    1. extract   - pull ServiceNow tables to CSV
    2. analyze   - score automation opportunities from the CSVs
    3. load      - load the CSVs into DuckDB for querying / dashboards

By default all stages run. Use flags to run a subset, e.g.:

    python src/main.py --skip-extract        # re-analyze existing CSVs
    python src/main.py --only extract
"""

import argparse

from requests.auth import HTTPBasicAuth

import config
from extractor import extract_table, _get_password


def run_extract():

    print("=" * 60)
    print("STAGE 1: SERVICENOW MULTI-TABLE EXTRACTION")
    print("=" * 60)

    auth = HTTPBasicAuth(config.USERNAME, _get_password())

    for table_name, file_name in config.TABLES.items():

        print(f"\nProcessing: {table_name}")

        try:
            extract_table(table_name, file_name, auth=auth)
        except Exception as exc:
            print(f"FAILED: {table_name}")
            print(f"  {exc}")

    print("\nExtraction complete")


def run_analyze():

    print("\n" + "=" * 60)
    print("STAGE 2: ANALYSIS")
    print("=" * 60)

    # Imported lazily so an extract-only run does not require pandas-heavy deps
    # to be importable first.
    from analyzer import run_analysis

    run_analysis()


def run_load():

    print("\n" + "=" * 60)
    print("STAGE 3: LOAD TO DUCKDB")
    print("=" * 60)

    from database import load_csvs

    load_csvs()


def main():

    parser = argparse.ArgumentParser(description="Phase 1 Discovery pipeline")
    parser.add_argument(
        "--skip-extract", action="store_true", help="skip ServiceNow extraction"
    )
    parser.add_argument(
        "--skip-analyze", action="store_true", help="skip analysis"
    )
    parser.add_argument(
        "--skip-load", action="store_true", help="skip DuckDB load"
    )
    parser.add_argument(
        "--only",
        choices=["extract", "analyze", "load"],
        help="run only one stage",
    )
    args = parser.parse_args()

    if args.only:
        stages = {args.only}
    else:
        stages = set()
        if not args.skip_extract:
            stages.add("extract")
        if not args.skip_analyze:
            stages.add("analyze")
        if not args.skip_load:
            stages.add("load")

    if "extract" in stages:
        run_extract()
    if "analyze" in stages:
        run_analyze()
    if "load" in stages:
        run_load()

    print("\nPipeline finished")


if __name__ == "__main__":
    main()
