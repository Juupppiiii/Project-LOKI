"""Retrieval for the advisory knowledge base (the grounding behind the LLM).

"Backed by actual process gathered from solved tickets" means: before the model
says anything, we retrieve the real evidence it must be grounded in --

  1. the published procedures that match the ticket (the sanctioned rulebook),
  2. the most similar *solved* incidents and how they were actually resolved
     (close_notes / work_notes), filtered to substantive resolutions.

This module only RETRIEVES and RANKS real data; it makes no network calls and
runs no model. The LLM layer consumes what this returns. Keeping retrieval
separate means the grounding is inspectable on its own (you can see exactly
which tickets a recommendation was based on) and the model can never fabricate
its evidence.

In the cloud this retriever's source becomes the shared Aurora store (and,
optionally, a Bedrock Knowledge Base vector index); the interface here stays the
same so that swap is a backend change, not an API change.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOLVED = _PROJECT_ROOT / "data" / "closed_incidents.csv"
# Prefer the richer structured JSON knowledge (built by build_knowledge_json.py)
# when present â€” it carries 16k+ pre-cleaned substantive resolutions vs ~500 in
# the raw CSV. Fall back to the CSV if the JSON hasn't been built yet.
DEFAULT_KNOWLEDGE_JSON = _PROJECT_ROOT / "knowledge" / "tickets" / "resolved_tickets.json"

_WS = re.compile(r"\s+")
_RESOLVED_BY = re.compile(r"resolved by:\s*\S+", re.I)
_BOILERPLATE = re.compile(
    r"resolved from sled|this is ok|already raised|midnight reset|^ok$|^test$|"
    r"^raised$|no action required|^resolved$",
    re.I,
)


def _raise_csv_field_limit() -> None:
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def _norm(s: object) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def _resolution_core(close_notes: str, work_notes: str) -> str:
    cn = _BOILERPLATE.sub("", _RESOLVED_BY.sub("", close_notes)).strip(" .-")
    wn = _BOILERPLATE.sub("", work_notes).strip(" .-")
    return _norm(f"{cn} {wn}")


def _is_substantive(core: str) -> bool:
    return len(core) >= 40 and bool(re.search(r"[a-zA-Z]{4,}", core))


@dataclass
class SolvedTicket:
    number: str
    short_description: str
    assignment_group: str
    close_code: str
    resolution: str
    similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "short_description": self.short_description[:160],
            "assignment_group": self.assignment_group,
            "close_code": self.close_code,
            "resolution": self.resolution[:400],
            "similarity": round(self.similarity, 4),
        }


class SolvedTicketIndex:
    """Loads substantive solved resolutions once and ranks them by similarity.

    Uses the project's own numpy TF-IDF + char-ngram vectorizer (the same one
    the advisory semantic matcher uses), so retrieval stays offline and
    consistent with how the rest of the system judges 'similar'.
    """

    def __init__(self, dataset: Path | None = None) -> None:
        # If an explicit dataset is given, use it. Otherwise prefer the richer
        # structured JSON knowledge, falling back to the raw CSV.
        if dataset is not None:
            self.dataset = Path(dataset)
        elif DEFAULT_KNOWLEDGE_JSON.exists():
            self.dataset = DEFAULT_KNOWLEDGE_JSON
        else:
            self.dataset = DEFAULT_SOLVED
        self._rows: list[SolvedTicket] = []
        self._vec = None
        self._mat = None
        self.loaded = False
        self.source = ""

    def load(self) -> "SolvedTicketIndex":
        if not self.dataset.exists():
            self.loaded = False
            return self
        if self.dataset.suffix == ".json":
            rows, texts = self._load_json()
            self.source = "knowledge-json"
        else:
            rows, texts = self._load_csv()
            self.source = "closed-incidents-csv"

        if not rows:
            self.loaded = True
            return self

        from . import semantic_engine

        self._vec = semantic_engine._NumpyVectorizer().fit(texts)
        self._mat = self._vec.embed(texts)
        self._rows = rows
        self.loaded = True
        return self

    def _load_json(self) -> tuple[list[SolvedTicket], list[str]]:
        """Load from the pre-cleaned structured knowledge JSON (preferred).

        The numpy TF-IDF vectorizer's vocabulary (word + char n-grams) and dense
        matrix grow with the corpus, so we cap the index at the N most recent
        substantive resolutions (the file is already newest-first). This keeps
        retrieval fast and memory-bounded; recency is a good proxy for relevance.
        Raise BUCKY_KB_MAX_TICKETS to index more at the cost of load time/memory.
        """
        import json

        cap = int(os.getenv("BUCKY_KB_MAX_TICKETS", "4000"))
        rows: list[SolvedTicket] = []
        texts: list[str] = []
        data = json.loads(self.dataset.read_text(encoding="utf-8"))
        for r in data:
            if len(rows) >= cap:
                break
            if not r.get("resolution_substantive"):
                continue
            resolution = _norm(r.get("resolution_clean"))
            if not resolution:
                continue
            sd = _norm(r.get("short_description"))
            rows.append(
                SolvedTicket(
                    number=_norm(r.get("number")),
                    short_description=sd,
                    assignment_group=_norm(r.get("assignment_group")),
                    close_code=_norm(r.get("close_code")),
                    resolution=resolution,
                )
            )
            texts.append(f"{sd} {resolution}".lower())
        return rows, texts

    def _load_csv(self) -> tuple[list[SolvedTicket], list[str]]:
        """Fall back to the raw closed-incidents CSV."""
        _raise_csv_field_limit()
        rows: list[SolvedTicket] = []
        texts: list[str] = []
        with self.dataset.open(newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                core = _resolution_core(_norm(r.get("close_notes")), _norm(r.get("work_notes")))
                if not _is_substantive(core):
                    continue
                sd = _norm(r.get("short_description"))
                rows.append(
                    SolvedTicket(
                        number=_norm(r.get("number")),
                        short_description=sd,
                        assignment_group=_norm(r.get("assignment_group")),
                        close_code=_norm(r.get("close_code")),
                        resolution=core,
                    )
                )
                texts.append(f"{sd} {core}".lower())
        return rows, texts

    def retrieve(self, ticket: dict[str, Any], top: int = 5) -> list[SolvedTicket]:
        if not self._rows or self._mat is None:
            return []
        query = _norm(
            f"{ticket.get('short_description','')} {ticket.get('description','')}"
        ).lower()
        if not query:
            return []
        import numpy as np

        qv = self._vec.embed([query])[0]
        sims = self._mat @ qv
        order = np.argsort(sims)[::-1][:top]
        out: list[SolvedTicket] = []
        for i in order:
            st = self._rows[int(i)]
            st.similarity = float(sims[int(i)])
            out.append(st)
        return out

    @property
    def size(self) -> int:
        return len(self._rows)


@dataclass
class Grounding:
    """Everything retrieved for a ticket, before the model sees it."""

    matched_procedure: dict[str, Any] | None = None
    candidate_procedures: list[dict[str, Any]] = field(default_factory=list)
    solved_tickets: list[dict[str, Any]] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_procedure": self.matched_procedure,
            "candidate_procedures": self.candidate_procedures,
            "solved_tickets": self.solved_tickets,
            "source_counts": self.source_counts,
        }


_SOLVED_INDEX: SolvedTicketIndex | None = None


def shared_solved_index(*, force_reload: bool = False) -> SolvedTicketIndex:
    global _SOLVED_INDEX
    if _SOLVED_INDEX is None or force_reload:
        _SOLVED_INDEX = SolvedTicketIndex().load()
    return _SOLVED_INDEX
