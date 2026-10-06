"""Semantic (meaning-based) ticket -> procedure matching.  ADVISORY ONLY.

This is the semantic counterpart to system2/engine.py's keyword matcher. Instead
of literal substring hits, it compares the *meaning* of the ticket text against
each procedure's trigger using vector similarity, so a ticket that says
"can't sign in, account disabled" can still find the "account locked" procedure
even with zero shared keywords.

WHAT THIS MODULE DOES AND DOES NOT DO
-------------------------------------
* It recommends a procedure and resolves parameters -- exactly the same output
  shape as engine.Match. Nothing more.
* It NEVER classifies risk, approves a chunk, or executes anything. Semantics
  widen what gets *proposed*; they never widen what is *permitted*. A human still
  approves the concrete plan and risk.py still refuses anything unproven.
* It is safe to run in "shadow" mode next to the keyword matcher: see
  scripts/compare_matchers.py. Turning it on for real is a deliberate, separate step.

EMBEDDING BACKEND (pluggable, degrades gracefully)
--------------------------------------------------
1. If `sentence-transformers` is installed, use it (a small local model, runs
   offline on CPU). Best quality.
2. Otherwise fall back to a dependency-light vectorizer built on numpy only:
   TF-IDF over word tokens plus character 3/4-grams. The char-grams give it
   fuzzy, sub-word matching ("login" ~ "log in" ~ "logon") without any model
   download. Good enough to prove the mechanism and compare against keywords.

Both backends expose the same `embed(texts) -> matrix of unit vectors`, so the
matcher code does not care which one is active. `backend_name()` reports it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

# Reuse System 2's own primitives so there is one definition of a Match, the
# problem fields, the threshold, and the parameter resolver. The semantic
# matcher only replaces *how the score is computed*, not what a Match is.
from . import _system2  # noqa: F401  (ensures system2 is importable)

import engine as _kw_engine  # noqa: E402  system2/engine.py (on path via _system2)

Match = _kw_engine.Match
PROBLEM_FIELDS = _kw_engine.PROBLEM_FIELDS
_resolve_parameters = _kw_engine._resolve_parameters


# Cosine-similarity threshold below which we return no match. Tunable; this is
# the semantic analogue of MIN_SCORE. Calibrate against real tickets before ever
# trusting it in production -- too low invites false matches, too high loses
# recall. Human approval is the safety net while tuning.
DEFAULT_THRESHOLD = float(0.28)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _ticket_text(ticket: dict[str, Any]) -> str:
    """Same fields the keyword engine matches on: the problem statement only."""

    return " ".join(str(ticket.get(f, "")) for f in PROBLEM_FIELDS).lower()


def _procedure_text(proc: Any) -> str:
    """A procedure's 'meaning' = its name plus its trigger keywords.

    This is what the ticket is compared against. Keywords are still useful here
    -- not as literal matches, but as words that describe the intent.
    """

    kws = " ".join(proc.trigger.keywords)
    return f"{proc.name} {kws}".lower()


# ===========================================================================
# Embedding backends
# ===========================================================================
class _NumpyVectorizer:
    """Dependency-light embeddings: TF-IDF over words + character n-grams.

    Fit on the corpus (the procedure texts + the tickets being scored) so the
    vocabulary and IDF weights are shared. Produces L2-normalised vectors, so a
    dot product is cosine similarity.
    """

    def __init__(self, char_ngrams: Sequence[int] = (3, 4)) -> None:
        self.char_ngrams = tuple(char_ngrams)
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None

    def _features(self, text: str) -> list[str]:
        text = text.lower()
        feats = _TOKEN_RE.findall(text)
        squished = re.sub(r"\s+", " ", text)
        for n in self.char_ngrams:
            for i in range(len(squished) - n + 1):
                gram = squished[i : i + n]
                if gram.strip():
                    feats.append(f"#{gram}")  # '#' namespaces char-grams
        return feats

    def fit(self, corpus: Sequence[str]) -> "_NumpyVectorizer":
        df: dict[str, int] = {}
        docs_feats = []
        for doc in corpus:
            feats = set(self._features(doc))
            docs_feats.append(feats)
            for f in feats:
                df[f] = df.get(f, 0) + 1
        self.vocab = {f: i for i, f in enumerate(sorted(df))}
        n_docs = max(len(corpus), 1)
        idf = np.zeros(len(self.vocab), dtype=np.float32)
        for f, i in self.vocab.items():
            idf[i] = math.log((1 + n_docs) / (1 + df[f])) + 1.0
        self.idf = idf
        return self

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        assert self.idf is not None, "vectorizer not fitted"
        mat = np.zeros((len(texts), len(self.vocab)), dtype=np.float32)
        for r, text in enumerate(texts):
            counts: dict[int, float] = {}
            for f in self._features(text):
                j = self.vocab.get(f)
                if j is not None:
                    counts[j] = counts.get(j, 0.0) + 1.0
            for j, c in counts.items():
                mat[r, j] = (1.0 + math.log(c)) * self.idf[j]
        # L2 normalise so dot product == cosine similarity
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms


class _SbertVectorizer:
    """Wraps sentence-transformers if it is installed. Optional, better quality."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # heavy, optional

        self.model = SentenceTransformer(model_name)

    def fit(self, corpus: Sequence[str]) -> "_SbertVectorizer":
        return self  # nothing to fit; the model is pre-trained

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        vecs = self.model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(vecs, dtype=np.float32)


def _make_backend():
    """Prefer sentence-transformers; fall back to the numpy vectorizer."""

    try:
        return _SbertVectorizer(), "sentence-transformers (all-MiniLM-L6-v2)"
    except Exception:
        return _NumpyVectorizer(), "numpy tf-idf + char-ngrams (fallback)"


# ===========================================================================
# The matcher
# ===========================================================================
@dataclass
class SemanticMatcher:
    """Holds fitted procedure vectors so tickets can be scored quickly."""

    procedures: list[Any]
    threshold: float = DEFAULT_THRESHOLD
    _backend: Any = field(default=None, repr=False)
    _proc_vecs: np.ndarray | None = field(default=None, repr=False)
    backend: str = ""

    def __post_init__(self) -> None:
        self._backend, self.backend = _make_backend()
        proc_texts = [_procedure_text(p) for p in self.procedures]
        # Fit on procedure texts so the vocabulary is anchored on the rulebook.
        self._backend.fit(proc_texts)
        self._proc_vecs = self._backend.embed(proc_texts)

    def match(self, ticket: dict[str, Any]) -> Match | None:
        """Best-matching procedure by meaning, or None if below threshold.

        Returns the same Match object the keyword engine returns, so every
        downstream consumer (planner, risk, approval, audit) is unchanged.
        """

        if not self.procedures or self._proc_vecs is None:
            return None

        text = _ticket_text(ticket).strip()
        if not text:
            return None

        tvec = self._backend.embed([text])[0]
        sims = self._proc_vecs @ tvec  # cosine, both are unit vectors
        best_i = int(np.argmax(sims))
        best_sim = float(sims[best_i])

        if best_sim < self.threshold:
            return None

        proc = self.procedures[best_i]
        params, missing = _resolve_parameters(proc, ticket)

        # Report which trigger words actually appear, for the review card -- the
        # human still sees concrete evidence, not just a similarity number.
        low = text
        matched = [kw for kw in proc.trigger.keywords if kw in low]

        # Keep the Match shape identical; expose similarity as the score so the
        # UI/threshold logic is comparable, but scaled to the keyword range so
        # dashboards do not misread it. (0..1 similarity -> 0..10-ish.)
        return Match(
            procedure=proc,
            score=round(best_sim * 10.0, 2),
            matched_keywords=matched,
            group_match=False,
            parameters=params,
            missing=missing,
        )

    def rank(self, ticket: dict[str, Any], top: int = 5) -> list[tuple[Any, float]]:
        """(procedure, similarity) pairs, best first -- for review/debugging."""

        text = _ticket_text(ticket).strip()
        if not text or self._proc_vecs is None:
            return []
        tvec = self._backend.embed([text])[0]
        sims = self._proc_vecs @ tvec
        order = np.argsort(sims)[::-1][:top]
        return [(self.procedures[i], float(sims[i])) for i in order]


def build_matcher(procedures: list[Any], threshold: float = DEFAULT_THRESHOLD) -> SemanticMatcher:
    return SemanticMatcher(procedures=procedures, threshold=threshold)
