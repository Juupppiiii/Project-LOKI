"""CLI entry point: python -m server.ingest

Usage:
    python -m server.ingest --knowledge path/to/knowledge/
    python -m server.ingest --json path/to/resolved_tickets.json
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .functions.indexer import build_index
from .functions.loader import load_json_file, load_knowledge_dir

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

    documents = []
    start = time.time()

    if args.knowledge:
        logger.info("Loading from knowledge directory: %s", args.knowledge)
        documents.extend(load_knowledge_dir(args.knowledge))

    if args.json:
        logger.info("Loading from JSON file: %s", args.json)
        documents.extend(load_json_file(args.json))

    count = build_index(documents)
    elapsed = round(time.time() - start, 1)
    logger.info("Done. %d documents indexed in %ss.", count, elapsed)

    if count == 0:
        logger.warning("No documents were indexed. Check your data paths.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
