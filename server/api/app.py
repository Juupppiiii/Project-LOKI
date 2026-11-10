"""FastAPI application factory, middleware, and lifespan."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.rag.functions.index import get_index

from .routes import router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up the RAG index on startup."""

    try:
        get_index()
    except Exception:  # noqa: BLE001
        logger.info("Vector index not loaded on startup (run ingest first)")
    yield


app = FastAPI(
    title="Bucky",
    version="2.0.0",
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

app.include_router(router, prefix="/api")

# ---------------------------------------------------------------------------
# Static dashboard (serve the built React app)
# ---------------------------------------------------------------------------

_DASHBOARD_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"

if _DASHBOARD_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_DASHBOARD_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def serve_dashboard(path: str):
        """Serve the React SPA — any non-API path returns index.html."""

        file_path = _DASHBOARD_DIR / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_DASHBOARD_DIR / "index.html")
