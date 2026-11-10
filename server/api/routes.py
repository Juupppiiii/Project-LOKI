"""API route definitions. Each endpoint delegates to a vertical's functions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server import config
from server.models import QueryResponse, TicketQuery
from server.rag.functions.index import get_index
from server.rag.functions.search import search_similar
from server.recommend.functions.synthesize import synthesize

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "model": config.BEDROCK_MODEL_ID}


@router.post("/query", response_model=QueryResponse)
def query_ticket(ticket: TicketQuery) -> QueryResponse:
    """Submit a ticket and get similar resolutions + AI recommendation."""

    try:
        similar = search_similar(ticket)
        recommendation = synthesize(ticket, similar)
        return QueryResponse(
            query=ticket,
            similar_tickets=similar,
            recommendation=recommendation,
            model=config.BEDROCK_MODEL_ID,
            retrieval_count=len(similar),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/index/status")
def index_status() -> dict:
    """Check whether the vector index is loaded and its size."""

    try:
        index = get_index()
        vs = index._vector_store
        count = vs._collection.count() if hasattr(vs, "_collection") else "unknown"
        return {"indexed": True, "document_count": count}
    except Exception as exc:  # noqa: BLE001
        return {"indexed": False, "error": str(exc)}
