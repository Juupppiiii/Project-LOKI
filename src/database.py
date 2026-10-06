"""DuckDB integration for the Phase 1 Discovery framework.

Loads the extracted CSVs into a single DuckDB file so analysis and the
dashboard can query one store instead of loose files. Table names are derived
from the CSV file stems (e.g. incidents.csv -> incidents).
"""

from pathlib import Path

import duckdb

import config


def get_connection(path=None):
    """Open (and create if needed) the DuckDB database."""

    return duckdb.connect(path or config.DUCKDB_PATH)


def load_csvs(data_dir=None, db_path=None):
    """Load every CSV in the data directory into a DuckDB table.

    Existing tables are replaced so repeated runs stay idempotent. Returns a
    dict of table_name -> row_count.
    """

    data_dir = Path(data_dir or config.OUTPUT_DIR)
    csv_files = sorted(data_dir.glob("*.csv"))

    if not csv_files:
        print(f"No CSVs found in '{data_dir}'. Run extraction first.")
        return {}

    loaded = {}

    with get_connection(db_path) as con:

        for csv_file in csv_files:

            table = csv_file.stem

            # read_csv_auto handles type inference and quoting. Parameterize
            # the path; the table identifier is a safe file stem.
            con.execute(
                f'CREATE OR REPLACE TABLE "{table}" AS '
                "SELECT * FROM read_csv_auto(?, header=true)",
                [str(csv_file)],
            )

            count = con.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]

            loaded[table] = count
            print(f"Loaded {csv_file.name} -> table '{table}': {count:,} rows")

    print(f"\nDuckDB ready at {db_path or config.DUCKDB_PATH}")
    return loaded


if __name__ == "__main__":
    load_csvs()
