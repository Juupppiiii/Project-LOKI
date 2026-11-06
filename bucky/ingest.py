"""CLI tool to ingest resolved ticket data into the vector store.

Usage:
    python -m bucky.ingest --knowledge path/to/knowledge/
    python -m bucky.ingest --json path/to/resolved_tickets.json

Run this once after pulling your ticket data, then again whenever the corpus
updates. The server will pick up the indexed data on next startup.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import rag

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest resolved tickets into the Bucky vector store."
    )
    parser.add_argument(
        "--knowledge",
        help="Path to the knowledge/ directory (reads tickets/by_group/*.json).",
    )
    parser.add_argument(
        "--json",
        help="Path to a flat resolved_tickets.json file.",
    )
    args = parser.parse_args()

    if not args.knowledge and not args.json:
        parser.error("Provide --knowledge or --json (or both).")

    total = 0
    start = time.time()

    if args.knowledge:
        logger.info("Ingesting from knowledge directory: %s", args.knowledge)
        count = rag.ingest_knowledge_json(args.knowledge)
        logger.info("Indexed %d documents from knowledge/", count)
        total += count

    if args.json:
        logger.info("Ingesting from JSON file: %s", args.json)
        count = rag.ingest_resolved_tickets_json(args.json)
        logger.info("Indexed %d documents from JSON", count)
        total += count

    elapsed = round(time.time() - start, 1)
    logger.info("Done. Total: %d documents indexed in %ss.", total, elapsed)

    if total == 0:
        logger.warning("No documents were indexed. Check your data paths.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
