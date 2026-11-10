"""Shared configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# AWS / Bedrock
AWS_REGION = os.getenv("AWS_REGION", "eu-west-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-opus-5")

# ServiceNow (read-only)
SN_INSTANCE = os.getenv("SN_INSTANCE", "")
SN_USERNAME = os.getenv("SN_USERNAME", "")
SN_PASSWORD = os.getenv("SN_PASSWORD", "")

# Vector store
CHROMA_PERSIST_DIR = os.getenv(
    "CHROMA_PERSIST_DIR",
    str(PROJECT_ROOT / "data" / "index" / "chroma"),
)

# Server
HOST = os.getenv("BUCKY_HOST", "127.0.0.1")
PORT = int(os.getenv("BUCKY_PORT", "8800"))

# RAG tuning
TOP_K = int(os.getenv("BUCKY_TOP_K", "8"))
SIMILARITY_THRESHOLD = float(os.getenv("BUCKY_SIMILARITY_THRESHOLD", "0.3"))
