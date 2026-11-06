"""Bucky API — the HTTP interface for the RAG system.

Simple and focused: submit a ticket, get back similar resolved tickets and an
AI-synthesized resolution recommendation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, rag
from .models import QueryResponse, TicketQuery

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up the RAG index on startup."""

    try:
        rag.get_index()
    except Exception:  # noqa: BLE001
        logger.info("Vector index not loaded on startup (run ingest first)")
    yield


app = FastAPI(
    title="Bucky",
    version="1.0.0",
    description=(
        "IT ticket resolution RAG. Submit a ticket, get back similar resolved "
        "tickets and an AI-synthesized recommendation."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": config.BEDROCK_MODEL_ID}


@app.post("/api/query", response_model=QueryResponse)
def query_ticket(ticket: TicketQuery) -> QueryResponse:
    """Submit a ticket and get similar resolutions + AI recommendation."""

    try:
        return rag.query(ticket)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/index/status")
def index_status() -> dict:
    """Check whether the vector index is loaded and its size."""

    try:
        index = rag.get_index()
        # ChromaDB collection count
        vs = index._vector_store
        count = vs._collection.count() if hasattr(vs, "_collection") else "unknown"
        return {"indexed": True, "document_count": count}
    except Exception as exc:  # noqa: BLE001
        return {"indexed": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Static UI (serve the built React app)
# ---------------------------------------------------------------------------

_UI_DIR = Path(__file__).resolve().parent.parent / "ui" / "dist"

if _UI_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_UI_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def serve_ui(path: str):
        """Serve the React SPA — any non-API path returns index.html."""

        file_path = _UI_DIR / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_UI_DIR / "index.html")
