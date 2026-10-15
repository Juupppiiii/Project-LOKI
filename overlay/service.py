"""Application service: every use case the overlay supports.

This is the authority. All policy decisions, approval checks and state
transitions happen here -- never in a client. A browser, a desktop app, a CLI
and a chat bot all call these same methods through the API, so none of them can
grant itself permission the others do not have.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import (
    _system2,
    action_catalog,
    knowledge,
    planner,
    risk as risk_policy,
    simulator,
    verification,
)
from .action_gateway import ActionGateway
from .connectors import ConnectorRefused, build_envelope
from .domain import (
    Approval,
    DomainError,
    DraftStatus,
    DuplicateRunError,
    ExecutionPolicy,
    KnowledgeSource,
    Owner,
    RunState,
    SourceKind,
    StepState,
    TaskRun,
    new_id,
    now_iso,
)
from .registry import ProcedureRegistry
from .store_contract import StoreProtocol
from .store_factory import create_store_from_env

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# -- Governance controls -----------------------------------------------------
# Separation of duties: the person who triggers execution must not be the same
# person who approved the chunk. Default ON. It can be relaxed for a single-user
# dev box via LOKI_ENFORCE_SOD=0, but production should leave it on.
def _sod_enforced() -> bool:
    return os.getenv("LOKI_ENFORCE_SOD", "1").strip().lower() not in ("0", "false", "no", "")


# Approval expiry: an approval older than this many seconds no longer authorises
# execution and must be renewed. 0 disables expiry. Default 1 hour.
def _approval_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("LOKI_APPROVAL_TTL_SECONDS", "3600")))
    except ValueError:
        return 3600


def _age_seconds(iso_timestamp: str) -> float | None:
    """Seconds between an ISO-8601 timestamp and now, or None if unparseable."""

    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


# -- Evidence-based recommendation scoring -----------------------------------
# The recommend_candidates path FUSES the keyword matcher and the semantic
# matcher into ranked candidates, each carrying the concrete evidence that
# justifies it and a 0..1 confidence. It is strictly advisory: it never
# proposes, approves, classifies risk, or executes. Its only job is to help a
# human decide, and -- importantly -- to ABSTAIN when the evidence is weak
# rather than guessing.

# Minimum confidence for the top candidate before we are willing to say
# "recommend" rather than "insufficient_evidence". Tunable; deliberately
# conservative so a weak signal abstains instead of asserting a fix.
def _recommend_floor() -> float:
    try:
        return float(os.getenv("LOKI_RECOMMEND_MIN_CONFIDENCE", "0.45"))
    except ValueError:
        return 0.45


# The keyword score at which keyword confidence saturates to 1.0. The engine
# scores +2 per matched keyword (+1 for group), MIN_SCORE=3. Three solid
# keyword hits (score 6) is a strong signal, so we saturate there.
_KEYWORD_SCORE_SATURATION = 6.0


def _keyword_confidence(score: float) -> float:
    """Map a raw keyword score (0, 2, 3, 4, ...) to 0..1, saturating at 6."""

    if score <= 0:
        return 0.0
    return min(1.0, score / _KEYWORD_SCORE_SATURATION)


def _semantic_confidence(similarity: float, threshold: float) -> float:
    """Map cosine similarity to 0..1, anchored so `threshold` -> ~0.5.

    Below threshold the signal is weak (<0.5); a similarity of 2x threshold or
    more saturates to 1.0. This keeps the semantic contribution comparable to
    the keyword one without pretending sub-threshold hits are strong.
    """

    if similarity <= 0 or threshold <= 0:
        return 0.0
    # threshold -> 0.5, 2*threshold -> 1.0, linear in between, clamped.
    return max(0.0, min(1.0, 0.5 * (similarity / threshold)))


def _fuse_confidence(kw: float, sem: float) -> float:
    """Combine keyword and semantic confidence.

    Two independent signals agreeing should be more confident than either
    alone, but neither alone should be dismissed. We take the stronger signal
    and add a bounded agreement bonus from the weaker one.
    """

    strong, weak = (kw, sem) if kw >= sem else (sem, kw)
    return round(min(1.0, strong + 0.25 * weak), 4)


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


# Error-code-like tokens are high-precision match evidence: a Windows Event ID,
# an HTTP status, a hex code (0x8007...), a process exit code, or a vendor code
# like E1234 / ORA-00942. When the same code appears in both the ticket and a
# candidate procedure's trigger/name/notes, that is far stronger than a generic
# keyword overlap, so it earns a dedicated signal.
_ERROR_CODE_RES = (
    re.compile(r"\bevent\s*id\s*:?\s*(\d{2,6})\b", re.I),
    re.compile(r"\b0x[0-9a-f]{4,8}\b", re.I),
    re.compile(r"\b[A-Z]{2,5}-\d{3,6}\b"),          # ORA-00942, SQL-1234
    re.compile(r"\bexit(?:\s*code)?\s*:?\s*(\d{1,5})\b", re.I),
    re.compile(r"\bhttp\s*(\d{3})\b", re.I),
    re.compile(r"\b[45]\d{2}\b"),                   # bare 4xx/5xx
    re.compile(r"\bE\d{3,5}\b"),                     # E1234
)


def _extract_error_codes(text: str) -> set[str]:
    """Pull error-code-like tokens from free text, normalised to lowercase."""

    found: set[str] = set()
    low = text or ""
    for rx in _ERROR_CODE_RES:
        for m in rx.finditer(low):
            # Prefer a captured group (the numeric code) when present, else the
            # whole token, so "Event ID 1069" and "1069" match the same code.
            token = (m.group(1) if m.groups() else m.group(0)).strip().lower()
            if token:
                found.add(token)
    return found


def _error_code_signal(ticket_codes: set[str], proc: Any) -> set[str]:
    """Error codes shared between the ticket and a procedure's own text."""

    if not ticket_codes:
        return set()
    proc_text = " ".join(
        [proc.name or "", getattr(proc, "notes", "") or ""]
        + list(getattr(proc.trigger, "keywords", []) or [])
    ).lower()
    proc_codes = _extract_error_codes(proc_text)
    # Also treat a procedure keyword that literally contains the code as a hit,
    # so a keyword like "1069" or "0x80070005" counts even without regex shape.
    for code in ticket_codes:
        if code in proc_text:
            proc_codes.add(code)
    return ticket_codes & proc_codes


def _historical_signal(
    solved: list[Any], proc: Any
) -> tuple[float, list[dict[str, Any]]]:
    """How well a procedure is corroborated by similar solved tickets.

    For each retrieved solved ticket, if its resolution or short description
    overlaps the procedure's name/keywords, it counts as corroboration weighted
    by that ticket's retrieval similarity. Returns (0..1 strength, citations).
    This reuses the knowledge base's real resolution history -- it never treats
    a closure note as proof, only as corroborating evidence a human can inspect.
    """

    if not solved:
        return 0.0, []
    proc_terms = {
        t
        for t in (
            [w.lower() for w in (proc.name or "").split()]
            + list(getattr(proc.trigger, "keywords", []) or [])
        )
        if len(t) >= 4
    }
    if not proc_terms:
        return 0.0, []

    best = 0.0
    citations: list[dict[str, Any]] = []
    for st in solved:
        hay = f"{st.short_description} {st.resolution}".lower()
        hits = [t for t in proc_terms if t in hay]
        if not hits:
            continue
        # Overlap fraction x the solved ticket's own retrieval similarity.
        strength = (len(hits) / len(proc_terms)) * max(0.0, min(1.0, st.similarity))
        if strength > 0:
            citations.append(
                {
                    "number": st.number,
                    "similarity": round(float(st.similarity), 4),
                    "overlap_terms": hits[:8],
                }
            )
            best = max(best, strength)
    citations.sort(key=lambda c: c["similarity"], reverse=True)
    return round(min(1.0, best), 4), citations[:3]


def _normalise_resolution(text: str) -> str:
    """Canonicalise a resolution for near-duplicate comparison.

    Strips host names, ticket numbers, dates/times, and digits so that
    "restarted netprobe on ldn2ws9696" and "restarted netprobe on njc1ws040"
    collapse to the same shape. Lowercased, whitespace-squeezed.
    """

    t = (text or "").lower()
    t = re.sub(r"\b[a-z]{2,3}\d[a-z0-9]{3,}\b", " ", t)   # host tokens
    t = re.sub(r"\b(inc|req|chg|ritm)\d+\b", " ", t)       # ticket numbers
    t = re.sub(r"\d{1,4}[:/-]\d{1,2}[:/-]?\d{0,4}", " ", t)  # dates/times
    t = re.sub(r"\d+", " ", t)                              # bare digits
    t = re.sub(r"[^a-z ]+", " ", t)                        # punctuation
    return re.sub(r"\s+", " ", t).strip()


def _shingles(text: str, k: int = 3) -> set[str]:
    """Word k-shingles for Jaccard near-duplicate comparison."""

    words = text.split()
    if len(words) < k:
        return {text} if text else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def cluster_resolutions(
    solved: list[Any], threshold: float = 0.5
) -> list[dict[str, Any]]:
    """Group near-duplicate resolutions among retrieved solved tickets.

    Greedy single-pass clustering on Jaccard similarity of 3-shingles over the
    normalised resolution text. Clusters are returned largest-first; a resolution
    seen across many distinct incidents is stronger evidence than a one-off.
    This treats closure notes as evidence a human can inspect -- never as proof
    a procedure is correct -- and makes recurring resolution patterns visible.
    """

    reps: list[dict[str, Any]] = []
    for st in solved:
        norm = _normalise_resolution(getattr(st, "resolution", ""))
        if not norm:
            continue
        sh = _shingles(norm)
        placed = False
        for cluster in reps:
            inter = len(sh & cluster["_shingles"])
            union = len(sh | cluster["_shingles"]) or 1
            if inter / union >= threshold:
                cluster["count"] += 1
                cluster["tickets"].append(getattr(st, "number", ""))
                placed = True
                break
        if not placed:
            reps.append(
                {
                    "representative": getattr(st, "resolution", "")[:300],
                    "count": 1,
                    "tickets": [getattr(st, "number", "")],
                    "_shingles": sh,
                }
            )
    for c in reps:
        c.pop("_shingles", None)
        c["tickets"] = [t for t in c["tickets"] if t][:10]
    reps.sort(key=lambda c: c["count"], reverse=True)
    return reps


def _ci_signal(ticket: dict[str, Any], match: Any) -> str | None:
    """Configuration-item corroboration.

    When the ticket names a configuration item (``cmdb_ci``) and the candidate
    procedure actually resolved that same host into its parameters, the
    procedure is demonstrably targeting the thing the ticket is about -- a
    high-precision corroboration distinct from text/keyword overlap. Returns the
    matched CI token, or None.
    """

    if match is None:
        return None
    ci = str(ticket.get("cmdb_ci") or "").strip().lower()
    if not ci:
        return None
    for value in (match.parameters or {}).values():
        v = str(value or "").strip().lower()
        if not v:
            continue
        # Match on the host label (ignore domain suffix on either side).
        if v == ci or v.split(".")[0] == ci.split(".")[0]:
            return str(value)
    return None


def _raise_csv_field_limit() -> None:
    """Real ServiceNow exports blow past Python's 131072-char CSV field cap.

    `work_notes` on a long-running incident can be enormous. Without this the
    reader raises `_csv.Error: field larger than field limit`. Stepping down from
    sys.maxsize avoids the OverflowError that platform C longs can throw.
    """

    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


_raise_csv_field_limit()


# ServiceNow reference fields (assignment_group, cmdb_ci, ...) come back as
# objects. Depending on how they were extracted they land in CSV as a
# stringified record like:
#   @{display_value=Global Wintel Server Support; link=https://.../sys_user_group/19e...}
# Matching compares plain names, so an unflattened value silently never matches
# its procedure's assignment_group -- which suppresses the group score and, with
# MIN_SCORE=3, drops otherwise valid tickets.
_REFERENCE_RE = re.compile(r"display_value=(?P<value>.*?)(?:;\s*link=|\s*\}\s*$)", re.S)


def _flatten_reference(value: Any) -> Any:
    """Reduce a ServiceNow reference value to its display name."""

    if isinstance(value, dict):
        return str(value.get("display_value") or value.get("value") or "").strip()

    if isinstance(value, str) and "display_value=" in value:
        m = _REFERENCE_RE.search(value)
        if m:
            return m.group("value").strip().rstrip("}").strip()

    return value


def normalize_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    """Flatten reference fields so matching and display both see plain names."""

    return {k: _flatten_reference(v) for k, v in ticket.items()}


class OverlayService:
    def __init__(
        self,
        store: StoreProtocol | None = None,
        procedures_path: str | Path | None = None,
        tickets_csv: str | Path | None = None,
        gateway: ActionGateway | None = None,
    ) -> None:
        self.store = store or create_store_from_env()
        self.gateway = gateway or ActionGateway()
        self.procedures_path = procedures_path
        self.tickets_csv = Path(
            tickets_csv or _PROJECT_ROOT / "data" / "incidents.csv"
        )
        # The registry owns the effective rule set: built-in procedures plus
        # whatever a human has published from an upload.
        self.registry = ProcedureRegistry(self.store, procedures_path)

    @property
    def _procedures(self) -> list[Any]:
        return self.registry.procedures

    # -- reference data --------------------------------------------------

    def reload_procedures(self) -> int:
        return self.registry.reload()

    def list_procedures(self) -> list[dict[str, Any]]:
        snapshot = self.registry.snapshot()
        out = []
        for p in snapshot.procedures:
            classified = [risk_policy.classify(s) for s in p.steps]
            out.append(
                {
                    "procedure_id": p.procedure_id,
                    "name": p.name,
                    "version": _system2.procedure_version(p),
                    "origin": snapshot.origin.get(p.procedure_id, "unknown"),
                    "assignment_group": p.trigger.assignment_group,
                    "keywords": p.trigger.keywords,
                    "step_count": len(p.steps),
                    "ai_step_count": sum(
                        1
                        for risk, owner, policy, _ in classified
                        if owner is Owner.AI and policy is not ExecutionPolicy.MANUAL_ONLY
                    ),
                    "automatability": p.automatability,
                    "frequency": p.frequency,
                }
            )
        return out

    # -- matching (dictionary-aware) -------------------------------------

    def _match(self, ticket: dict[str, Any]):
        """Normalise, apply the dictionary, then match.

        Returns (match, ranked, augmented_ticket, applied_terms). Every entry
        point goes through here so uploaded dictionaries affect the whole system
        consistently.
        """

        clean = normalize_ticket(ticket)
        snapshot = self.registry.snapshot()
        procedures = list(snapshot.procedures)
        augmented, applied = self.registry.augment_ticket(clean, snapshot)
        match = _system2.match_ticket(augmented, procedures)
        ranked = _system2.rank_matches(augmented, procedures)
        return match, ranked, clean, applied

    def semantic_suggestions(
        self, ticket: dict[str, Any], top: int = 3
    ) -> dict[str, Any]:
        """Advisory, meaning-based procedure suggestions. Authorises NOTHING.

        The keyword matcher remains the authoritative matcher that drives
        proposals, approval and execution. This method exists only to help a
        human when the keyword rules found nothing or a weak match: it reports
        which procedures *look* semantically relevant, so a reviewer can decide
        to apply or publish one. It never proposes a run, never classifies risk,
        and never feeds the approval path.
        """

        if os.getenv("LOKI_SEMANTIC_SUGGESTIONS", "1").strip().lower() in (
            "0", "false", "no", "",
        ):
            return {"enabled": False, "suggestions": [], "backend": None}

        clean = normalize_ticket(ticket)
        snapshot = self.registry.snapshot()
        augmented, _applied = self.registry.augment_ticket(clean, snapshot)

        matcher = self._semantic_matcher(snapshot)
        if matcher is None:
            return {"enabled": True, "suggestions": [], "backend": None}

        ranked = matcher.rank(augmented, top=top)

        from . import efficiency
        eff_index = efficiency.shared_index() if efficiency.efficiency_enabled() else None

        suggestions = []
        for proc, sim in ranked:
            entry = {
                "procedure_id": proc.procedure_id,
                "procedure_name": proc.name,
                "similarity": round(float(sim), 4),
                # Scaled to the keyword score range so the UI can compare them.
                "advisory_score": round(float(sim) * 10.0, 2),
                "above_threshold": float(sim) >= matcher.threshold,
            }
            if eff_index is not None:
                entry["efficiency"] = efficiency.efficiency_profile(proc, eff_index)
            suggestions.append(entry)
        return {
            "enabled": True,
            "advisory": True,
            "note": "Suggestions are advisory only; a human must still approve any plan.",
            "backend": matcher.backend,
            "threshold": matcher.threshold,
            "suggestions": suggestions,
        }

    def recommend_candidates(
        self, ticket: dict[str, Any], top: int = 5
    ) -> dict[str, Any]:
        """Ranked, evidence-cited procedure candidates with an abstain path.

        ADVISORY ONLY. This fuses the two existing matchers -- the authoritative
        keyword engine and the semantic matcher -- into a single ranked list.
        Each candidate carries the concrete evidence that justifies it (which
        keywords hit, whether the assignment group matched, which parameters
        resolved and which are still missing, and which signals fired) plus a
        0..1 confidence and a band.

        Crucially it ABSTAINS: when the best candidate's confidence is below the
        floor, the decision is ``insufficient_evidence`` and no procedure is
        recommended. A missed suggestion is safe; a confident wrong one is not.

        This never proposes a run, classifies risk, or feeds the approval path.
        The keyword engine remains the authoritative matcher for propose/execute.
        """

        clean = normalize_ticket(ticket)
        snapshot = self.registry.snapshot()
        procedures = list(snapshot.procedures)
        augmented, applied = self.registry.augment_ticket(clean, snapshot)

        # Authoritative keyword signal (what actually drives proposals).
        kw_match = _system2.match_ticket(augmented, procedures)
        kw_ranked = _system2.rank_matches(augmented, procedures)
        kw_by_id = {m.procedure.procedure_id: m for m in kw_ranked}

        # Semantic signal (advisory meaning-based similarity), if enabled.
        sem_by_id: dict[str, float] = {}
        matcher = None
        semantic_enabled = os.getenv(
            "LOKI_SEMANTIC_SUGGESTIONS", "1"
        ).strip().lower() not in ("0", "false", "no", "")
        if semantic_enabled:
            matcher = self._semantic_matcher(snapshot)
            if matcher is not None:
                for proc, sim in matcher.rank(augmented, top=len(procedures) or 1):
                    sem_by_id[proc.procedure_id] = float(sim)
        sem_threshold = matcher.threshold if matcher is not None else None

        # Error-code signal: high-precision tokens in the ticket text.
        ticket_text = " ".join(
            str(augmented.get(f, ""))
            for f in ("short_description", "description", "category")
        )
        ticket_codes = _extract_error_codes(ticket_text)

        # Historical-resolution signal: similar *solved* tickets from the real
        # resolution history. Advisory corroboration only.
        solved = self._retrieve_solved(augmented)

        # Union of every procedure either signal surfaced.
        proc_ids = list(kw_by_id.keys()) + [
            pid for pid in sem_by_id if pid not in kw_by_id
        ]

        candidates: list[dict[str, Any]] = []
        for pid in proc_ids:
            m = kw_by_id.get(pid)
            proc = m.procedure if m is not None else self._procedure(pid)
            kw_score = float(m.score) if m is not None else 0.0
            sem_sim = sem_by_id.get(pid, 0.0)

            kw_conf = _keyword_confidence(kw_score)
            sem_conf = (
                _semantic_confidence(sem_sim, sem_threshold)
                if sem_threshold
                else 0.0
            )
            base_confidence = _fuse_confidence(kw_conf, sem_conf)

            # High-precision / corroboration signals.
            shared_codes = _error_code_signal(ticket_codes, proc)
            hist_strength, hist_cites = _historical_signal(solved, proc)
            group_match = bool(m is not None and m.group_match)
            ci_match = _ci_signal(augmented, m)

            # Fold the extra signals in as bounded boosts. Exact error-code and
            # CI matches are strong, precise evidence (+0.25 / +0.20); an
            # assignment-group match is weaker corroboration (+0.10); historical
            # resolution is softest (up to +0.15). All cap the total at 1.0 and
            # none can manufacture a candidate -- they only strengthen one a base
            # signal already surfaced.
            confidence = base_confidence
            if shared_codes:
                confidence = min(1.0, confidence + 0.25)
            if ci_match:
                confidence = min(1.0, confidence + 0.20)
            if group_match:
                confidence = min(1.0, confidence + 0.10)
            confidence = min(1.0, confidence + 0.15 * hist_strength)
            confidence = round(confidence, 4)

            signals = []
            if kw_score > 0:
                signals.append("keyword")
            if group_match:
                signals.append("assignment_group")
            if ci_match:
                signals.append("configuration_item")
            if sem_threshold and sem_sim >= sem_threshold:
                signals.append("semantic")
            if shared_codes:
                signals.append("error_code")
            if hist_strength > 0:
                signals.append("historical_resolution")

            candidates.append(
                {
                    "procedure_id": pid,
                    "procedure_name": proc.name,
                    "confidence": confidence,
                    "confidence_band": _confidence_band(confidence),
                    "keyword_score": round(kw_score, 2),
                    "semantic_similarity": round(sem_sim, 4),
                    "evidence": {
                        "matched_keywords": list(m.matched_keywords) if m else [],
                        "assignment_group_match": bool(m.group_match) if m else False,
                        "resolved_parameters": dict(m.parameters) if m else {},
                        "missing_parameters": list(m.missing) if m else [],
                        "error_codes": sorted(shared_codes),
                        "configuration_item": ci_match,
                        "historical_tickets": hist_cites,
                        "signals": signals,
                    },
                }
            )

        # Rank by fused confidence, then keyword score as a stable tiebreak.
        candidates.sort(
            key=lambda c: (c["confidence"], c["keyword_score"]), reverse=True
        )
        candidates = candidates[:top]

        floor = _recommend_floor()
        best = candidates[0] if candidates else None
        recommend = best is not None and best["confidence"] >= floor

        if recommend:
            reason = (
                f"Top candidate '{best['procedure_name']}' meets the confidence "
                f"floor ({best['confidence']:.2f} >= {floor:.2f}) with signals: "
                f"{', '.join(best['evidence']['signals']) or 'none'}."
            )
        elif best is not None:
            reason = (
                f"Best candidate '{best['procedure_name']}' is below the "
                f"confidence floor ({best['confidence']:.2f} < {floor:.2f}); "
                "abstaining and asking for more information."
            )
        else:
            reason = "No procedure matched this ticket on any signal; abstaining."

        return {
            "advisory": True,
            "decision": "recommend" if recommend else "insufficient_evidence",
            "abstained": not recommend,
            "reason": reason,
            "confidence_floor": floor,
            "authoritative_match": (
                {
                    "procedure_id": kw_match.procedure.procedure_id,
                    "procedure_name": kw_match.procedure.name,
                    "score": float(kw_match.score),
                }
                if kw_match is not None
                else None
            ),
            "semantic_backend": matcher.backend if matcher is not None else None,
            "dictionary_terms_applied": applied,
            "candidates": candidates,
            "note": (
                "Candidates are advisory and evidence-cited. A human must still "
                "approve any plan; the keyword engine remains authoritative for "
                "proposals and execution."
            ),
        }

    def recommend_steps(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """Advisory, LLM-assisted step recommendation grounded in real history.

        Retrieves the sanctioned matched procedure, candidate procedures, and the
        most similar *solved* incidents (their real resolutions), then asks the
        configured model to synthesise and critique a recommended approach --
        citing the evidence it was grounded in.

        STRICTLY ADVISORY. This method proposes NOTHING to the run store,
        approves NOTHING, and executes NOTHING. It returns text + citations for a
        human to read. The authoritative procedure pick is still the keyword
        matcher's; a human still approves every plan; and the action gateway
        still refuses anything not explicitly allowed. The model cannot widen any
        of that -- it only advises.
        """

        from . import knowledge_base as kb
        from . import knowledge_llm as kllm

        clean = normalize_ticket(ticket)
        match, ranked, _augmented, _applied = self._match(clean)

        # --- grounding (real, inspectable, no model involved yet) -----------
        grounding = kb.Grounding()
        if match is not None:
            grounding.matched_procedure = {
                "procedure_id": match.procedure.procedure_id,
                "procedure_name": match.procedure.name,
                "score": float(match.score),
                "steps": [
                    {"action": s.action, "description": s.description}
                    for s in match.procedure.steps
                ],
            }
        grounding.candidate_procedures = [
            {"procedure_id": m.procedure.procedure_id, "procedure_name": m.procedure.name,
             "score": float(m.score)}
            for m in ranked[:5]
        ]
        solved = kb.shared_solved_index().retrieve(clean, top=5)
        grounding.solved_tickets = [s.to_dict() for s in solved]
        grounding.source_counts = {
            "candidate_procedures": len(grounding.candidate_procedures),
            "solved_tickets": len(grounding.solved_tickets),
            "solved_index_size": kb.shared_solved_index().size,
            "solved_index_source": kb.shared_solved_index().source,
        }

        # --- model synthesis (advisory) ------------------------------------
        backend = kllm.make_backend()
        is_live_backend = not isinstance(backend, kllm.EchoBackend)
        system = (
            "You are an advisory assistant for an IT operations tool. You help a "
            "human engineer by RECOMMENDING and CRITIQUING resolution steps for a "
            "ticket. You never approve or execute anything; a human decides and a "
            "separate system enforces safety. Ground every recommendation in the "
            "provided procedures and solved-ticket history, and cite the ticket "
            "numbers you relied on. If the evidence is weak, say so plainly. Flag "
            "any step that is destructive or irreversible as human-only."
        )
        user = self._build_rag_prompt(clean, grounding)
        try:
            text = backend.complete(system, user)
            # Only a live backend counts as the model being available; the
            # offline echo returns text but is explicitly not a live model.
            available = is_live_backend
        except kllm.LLMUnavailable as exc:
            text = f"[advisory model unavailable: {exc}]"
            available = False

        return {
            "advisory": True,
            "note": (
                "LLM-assisted recommendation. Advisory only: it approves and "
                "executes nothing; a human must review and approve any plan."
            ),
            "backend": backend.name,
            "model_available": available,
            "authoritative_match": (
                grounding.matched_procedure["procedure_id"]
                if grounding.matched_procedure else None
            ),
            "grounding": grounding.to_dict(),
            "recommendation": text,
        }

    def _retrieve_solved(self, ticket: dict[str, Any], top: int = 5) -> list[Any]:
        """Similar solved tickets for the historical-resolution signal.

        Defensive: advisory retrieval must never break the recommendation path,
        so any failure (missing dataset, load error) yields an empty list.
        """

        try:
            from . import knowledge_base as kb

            index = kb.shared_solved_index()
            if not getattr(index, "loaded", False) or index.size == 0:
                return []
            return index.retrieve(ticket, top=top)
        except Exception as exc:  # never let advisory retrieval break a request
            self._kb_error = str(exc)
            return []

    def resolution_clusters(
        self, ticket: dict[str, Any], top: int = 25, threshold: float = 0.5
    ) -> dict[str, Any]:
        """Near-duplicate clusters among the resolutions of similar solved tickets.

        ADVISORY, read-only. Retrieves the solved tickets most similar to this
        one and groups their resolutions into near-duplicate clusters, so a
        human sees the recurring resolution patterns (and how many distinct
        incidents each represents) rather than a flat list. Treats closure notes
        as inspectable evidence, never as proof. Creates/approves/executes
        nothing.
        """

        solved = self._retrieve_solved(ticket, top=top)
        clusters = cluster_resolutions(solved, threshold=threshold)
        return {
            "advisory": True,
            "note": (
                "Resolution clusters are advisory evidence from solved-ticket "
                "history; a closed ticket's notes are not proof a fix was correct."
            ),
            "retrieved": len(solved),
            "clusters": clusters,
        }

    @staticmethod
    def _build_rag_prompt(ticket: dict[str, Any], grounding: "Any") -> str:
        """Assemble the retrieval-grounded prompt. Pure string building."""

        import json as _json

        lines = ["TICKET:", _json.dumps(
            {k: ticket.get(k) for k in ("number", "short_description", "description",
                                        "assignment_group", "cmdb_ci") if ticket.get(k)},
            indent=2,
        )]
        if grounding.matched_procedure:
            lines.append("\nSANCTIONED MATCHED PROCEDURE (authoritative):")
            lines.append(_json.dumps(grounding.matched_procedure, indent=2))
        if grounding.candidate_procedures:
            lines.append("\nOTHER CANDIDATE PROCEDURES:")
            lines.append(_json.dumps(grounding.candidate_procedures, indent=2))
        if grounding.solved_tickets:
            lines.append("\nMOST SIMILAR SOLVED TICKETS (real resolutions, cite these):")
            lines.append(_json.dumps(grounding.solved_tickets, indent=2))
        lines.append(
            "\nTASK: Recommend the best resolution steps for this ticket. Prefer "
            "the sanctioned procedure's steps where they apply. Cite solved-ticket "
            "numbers for any claim about how this is normally fixed. Mark any "
            "destructive/irreversible step as human-only. Be concise."
        )
        return "\n".join(lines)

    def _semantic_matcher(self, snapshot: Any):
        """Lazily build and cache a SemanticMatcher, rebuilding on registry change.

        Building fits a vectorizer over the procedure corpus, so we do it once
        per registry revision rather than per request.
        """

        procedures = list(snapshot.procedures)
        if not procedures:
            return None
        cached = getattr(self, "_sem_cache", None)
        if cached is not None and cached[0] == snapshot.revision:
            return cached[1]
        try:
            from . import semantic_engine

            matcher = semantic_engine.build_matcher(procedures)
        except Exception as exc:  # never let advisory tooling break a request
            self._sem_error = str(exc)
            return None
        self._sem_cache = (snapshot.revision, matcher)
        return matcher

    def risk_policy_table(self) -> list[dict[str, str]]:
        return risk_policy.describe_policy()

    def sample_tickets(self, limit: int = 25, scan: int = 2000) -> list[dict[str, Any]]:
        """Read real extracted tickets to drive the queue.

        Each ticket is annotated with whether a procedure would match it. Most
        tickets in the export belong to groups with no codified procedure yet, so
        without this the picker is mostly dead ends and the tool looks broken
        when it is in fact behaving correctly.
        """

        if not self.tickets_csv.exists():
            return []

        wanted = (
            "number",
            "short_description",
            "description",
            "category",
            "assignment_group",
            "cmdb_ci",
            "priority",
            "state",
        )

        matchable: list[dict[str, Any]] = []
        others: list[dict[str, Any]] = []

        with self.tickets_csv.open(newline="", encoding="utf-8", errors="replace") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                if i >= scan:
                    break

                ticket = normalize_ticket({k: (row.get(k) or "") for k in wanted})
                match, _ranked, _clean, _applied = self._match(ticket)

                ticket["_match"] = {
                    "matched": match is not None,
                    "procedure_id": match.procedure.procedure_id if match else None,
                    "procedure_name": match.procedure.name if match else None,
                    "missing_parameters": list(match.missing) if match else [],
                }

                (matchable if match is not None else others).append(ticket)

                # Enough matchable examples found; no need to read further.
                if len(matchable) >= limit:
                    break

        # Matchable first so the picker leads with tickets that actually work.
        return (matchable + others)[:limit]

    # -- proposing work --------------------------------------------------

    def preview_match(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """Match without persisting. Lets a client show 'what would happen'.

        The authoritative pick stays the keyword match. Efficiency data is
        attached as advice only: the match carries an efficiency profile, and an
        extra ``efficiency_ranked`` list offers a human a view sorted by
        efficiency -- it never changes which procedure ``procedure_id`` names.
        """

        match, ranked, _clean, applied = self._match(ticket)
        result = {
            "matched": match is not None,
            "procedure_id": match.procedure.procedure_id if match else None,
            "procedure_name": match.procedure.name if match else None,
            "score": float(match.score) if match else 0.0,
            "matched_keywords": list(match.matched_keywords) if match else [],
            "missing_parameters": list(match.missing) if match else [],
            "dictionary_terms_applied": applied,
            "alternatives": [
                {
                    "procedure_id": m.procedure.procedure_id,
                    "procedure_name": m.procedure.name,
                    "score": float(m.score),
                }
                for m in ranked[:5]
            ],
        }
        self._attach_efficiency(result, match, ranked)
        return result

    def _attach_efficiency(self, result: dict[str, Any], match, ranked) -> None:
        """Add advisory efficiency data to a match result. Never reorders the
        authoritative keyword match; only annotates and offers a separate rank."""

        from . import efficiency

        if not efficiency.efficiency_enabled():
            result["efficiency_enabled"] = False
            return
        index = efficiency.shared_index()

        if match is not None:
            result["efficiency"] = efficiency.efficiency_profile(match.procedure, index)

        # A separate, advisory ranking of the candidates by efficiency score.
        # The keyword `alternatives` above keep their keyword-confidence order;
        # this list is explicitly labelled as the efficiency view.
        scored = []
        for m in ranked[:5]:
            prof = efficiency.efficiency_profile(m.procedure, index)
            scored.append(
                {
                    "procedure_id": m.procedure.procedure_id,
                    "procedure_name": m.procedure.name,
                    "match_score": float(m.score),
                    "efficiency_score": prof["efficiency_score"],
                    "automatability": prof["automatability"],
                    "resolution_history": prof["resolution_history"],
                }
            )
        scored.sort(key=lambda x: x["efficiency_score"], reverse=True)
        result["efficiency_enabled"] = True
        result["efficiency_advisory"] = True
        result["efficiency_ranked"] = scored

    def plan_efficiency_advice(self, run_id: str) -> dict[str, Any]:
        """Advisory, read-only efficiency notes on an existing plan.

        This NEVER mutates the run, reorders steps, or removes any. It returns a
        separate annotation layer keyed by step_id so a client can show "where
        the agent can help" and "where a human must decide" without the advice
        ever being able to change what executes. Step order and the risk/owner
        split are the planner's and risk.py's decisions, not this method's.
        """

        run = self.get_run(run_id)
        steps = run.steps

        notes: list[dict[str, Any]] = []
        prev_action = None
        first_human_seq = None

        for s in steps:
            if s.owner is Owner.AI:
                category = "automatable"
                advice = "The agent can perform this bounded step once the chunk is approved."
            else:
                category = "human_decision"
                advice = "Human-owned: this needs a person's judgement or carries irreversible risk."
                if first_human_seq is None:
                    first_human_seq = s.sequence

            # The ONLY streamline hint, and it is a question, never an order:
            # two consecutive agent verification steps *might* be combinable.
            # We flag it for a human to consider; we never auto-remove a check.
            streamline_hint = None
            if (
                s.action == "check"
                and prev_action == "check"
                and s.owner is Owner.AI
            ):
                streamline_hint = (
                    "Follows another verification step. A human may consider whether "
                    "both checks are needed, but neither is removed automatically."
                )

            notes.append(
                {
                    "step_id": s.step_id,
                    "sequence": s.sequence,
                    "action": s.action,
                    "risk": s.risk.value,
                    "owner": s.owner.value,
                    "category": category,
                    "advice": advice,
                    "streamline_hint": streamline_hint,
                }
            )
            prev_action = s.action

        agent_steps = sum(1 for s in steps if s.owner is Owner.AI)
        total = len(steps)
        return {
            "advisory": True,
            "run_id": run_id,
            "mutates_plan": False,
            "note": (
                "Advice only. Step order, inclusion, and the agent/human split are "
                "fixed by policy and are not changed by this view."
            ),
            "agent_steps": agent_steps,
            "human_steps": total - agent_steps,
            "agent_share": round(agent_steps / total, 2) if total else 0.0,
            "first_human_handoff_sequence": first_human_seq,
            "steps": notes,
        }

    def propose(
        self,
        ticket: dict[str, Any],
        actor: str = "system",
        allow_duplicate: bool = False,
        mode: str = "simulation",
    ) -> TaskRun:
        """Match a ticket and persist a per-step plan awaiting approval.

        Refuses if the ticket already has a run in progress, unless the caller
        explicitly allows a duplicate. Two runs for one ticket can be
        legitimate, but they should be deliberate: silently forking a ticket
        splits its audit trail and lets two people approve the same work.
        """

        mode = (mode or "simulation").strip().lower()
        if mode not in ("simulation", "live"):
            raise DomainError("mode must be 'simulation' or 'live'")

        match, ranked, clean, applied = self._match(ticket)
        if match is None:
            raise DomainError(
                "No approved procedure matches this ticket with sufficient "
                "confidence. It stays with a human."
            )

        ticket_id = str(
            clean.get("number") or clean.get("ticket_id") or ""
        ).strip()

        existing = (
            self.store.find_active_run_by_ticket(ticket_id) if ticket_id else None
        )
        if existing is not None and not allow_duplicate:
            raise DuplicateRunError(
                ticket_id, existing.run_id, existing.state.value
            )

        # Store the clean ticket, not the dictionary-augmented text, so the UI
        # shows what the requester actually wrote.
        run = planner.build_plan(clean, match, alternatives=ranked, mode=mode)
        self.store.save_run(run)
        self.store.append_audit(
            run.run_id,
            "run.proposed",
            actor,
            {
                "ticket_id": run.ticket_id,
                "procedure_id": run.procedure_id,
                "procedure_version": run.procedure_version,
                "score": run.match_score,
                "matched_keywords": run.matched_keywords,
                "ai_steps": run.ai_step_count,
                "human_steps": run.human_step_count,
                "mode": run.mode,
                "missing_parameters": run.missing_parameters,
                "procedure_origin": self.registry.origin(run.procedure_id),
                "dictionary_terms_applied": applied,
                # A deliberate duplicate is recorded on both sides so either
                # audit trail leads to the other.
                "duplicate_of": existing.run_id if existing else None,
            },
        )

        if existing is not None:
            self.store.append_audit(
                existing.run_id,
                "run.duplicated",
                actor,
                {
                    "new_run_id": run.run_id,
                    "ticket_id": ticket_id,
                    "note": "a second run was created for this ticket deliberately",
                },
            )

        return run

    # -- reading ---------------------------------------------------------

    def get_run(self, run_id: str) -> TaskRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise DomainError(f"unknown run '{run_id}'")
        return run

    def list_runs(
        self, state: str | None = None, group: str | None = None, limit: int = 100
    ) -> list[TaskRun]:
        return self.store.list_runs(state=state, assignment_group=group, limit=limit)

    def list_runs_annotated(
        self, state: str | None = None, group: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Runs plus how many runs exist for the same ticket.

        Lets the queue flag a ticket that has been forked, so an existing
        duplicate is visible rather than looking like two unrelated rows.
        """

        runs = self.list_runs(state=state, group=group, limit=limit)

        tallies: dict[str, int] = {}
        for run in runs:
            tallies[run.ticket_id] = tallies.get(run.ticket_id, 0) + 1

        out = []
        for run in runs:
            d = run.to_dict()
            # Count across the whole store, not just this page.
            d["runs_for_ticket"] = self.store.count_runs_by_ticket(run.ticket_id)
            out.append(d)
        return out

    def readiness(self) -> dict[str, Any]:
        status = self.store.readiness()
        procedure_count = len(self._procedures)
        ready = bool(status.get("ready")) and procedure_count > 0
        return {
            "ready": ready,
            "backend": status.get("backend", "unknown"),
            "schema_version": status.get("schema_version", 0),
            "procedure_count": procedure_count,
            "registry_error_count": len(self.registry.load_errors),
            **({"detail": status.get("detail", "repository is not ready")} if not ready else {}),
        }

    def queue_summary(self) -> dict[str, Any]:
        counts = self.store.counts_by_state()
        published = self.store.list_published(only_enabled=True)
        return {
            "counts_by_state": counts,
            "total": sum(counts.values()),
            "procedures_loaded": len(self._procedures),
            "published_procedures": len(published),
            "dictionary_entries": len(self.registry.dictionary),
            "pending_drafts": len(self.store.list_drafts(status="draft")),
            "registry_errors": self.registry.load_errors,
            "live": self.live_status(),
            "mode": "simulation",
            "connectors_enabled": simulator.CONNECTORS_ENABLED,
        }

    def audit(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)  # existence check
        return [e.to_dict() for e in self.store.list_audit(run_id)]

    def approvals(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        return [a.to_dict() for a in self.store.list_approvals(run_id)]

    # -- parameter correction -------------------------------------------

    def set_parameters(
        self, run_id: str, updates: dict[str, Any], actor: str
    ) -> TaskRun:
        """Human corrects extracted parameters. Invalidates prior approvals."""

        run = self.get_run(run_id)
        if run.state in (RunState.COMPLETED, RunState.CANCELLED):
            raise DomainError(f"run is {run.state.value}; parameters are frozen")

        clean = {k: v for k, v in updates.items() if str(v).strip() != ""}
        before = dict(run.parameters)
        run.parameters.update(clean)
        run.missing_parameters = [
            m for m in run.missing_parameters if m not in clean
        ]

        procedure = self._procedure(run.procedure_id)
        planner.rerender(run, procedure)
        planner.recompute_state(run)
        self.store.save_run(run)

        self.store.append_audit(
            run.run_id,
            "parameters.updated",
            actor,
            {
                "before": before,
                "after": run.parameters,
                "note": "rendered steps changed; existing approvals no longer bind",
            },
        )
        return run

    # -- approval --------------------------------------------------------

    def decide_chunk(
        self,
        run_id: str,
        chunk_id: str,
        decision: str,
        actor: str,
        reason: str = "",
    ) -> TaskRun:
        """Record an approve/reject decision bound to exactly what was shown."""

        if decision not in ("approved", "rejected"):
            raise DomainError("decision must be 'approved' or 'rejected'")

        run = self.get_run(run_id)
        chunk = run.chunk(chunk_id)
        if chunk is None:
            raise DomainError(f"unknown chunk '{chunk_id}'")

        if run.missing_parameters:
            raise DomainError(
                "resolve missing parameters before approving: "
                + ", ".join(run.missing_parameters)
            )

        if chunk.owner is not Owner.AI or chunk.policy is ExecutionPolicy.MANUAL_ONLY:
            raise DomainError(
                "this chunk is human-owned; it is performed manually, not approved "
                "for agent execution"
            )

        # Retire any earlier decision for this chunk first, so there is exactly
        # one active approval and a superseded one can never authorise a run.
        superseded = self.store.supersede_approvals(run_id, chunk_id, now_iso())

        digest = planner.binding_digest(run, chunk)
        approval = Approval(
            approval_id=new_id("apr"),
            run_id=run.run_id,
            chunk_id=chunk_id,
            step_ids=list(chunk.step_ids),
            binding_digest=digest,
            decision=decision,
            actor=actor,
            reason=reason,
        )
        self.store.save_approval(approval)

        for sid in chunk.step_ids:
            step = run.step(sid)
            if step is None or step.state in (
                StepState.SUCCEEDED,
                StepState.SKIPPED,
            ):
                continue
            step.state = StepState.READY if decision == "approved" else StepState.BLOCKED

        if decision == "rejected":
            run.state = RunState.BLOCKED
        else:
            planner.recompute_state(run)

        self.store.save_run(run)
        self.store.append_audit(
            run.run_id,
            f"chunk.{decision}",
            actor,
            {
                "chunk_id": chunk_id,
                "chunk_label": chunk.label,
                "step_ids": chunk.step_ids,
                "risk": chunk.risk.value,
                "binding_digest": digest,
                "reason": reason,
                "procedure_version": run.procedure_version,
                "superseded_prior_approvals": superseded,
            },
        )
        return run

    # -- execution (simulated) ------------------------------------------

    def execute_chunk(self, run_id: str, chunk_id: str, actor: str = "agent") -> TaskRun:
        """Execute an approved chunk, then stop.

        Stops at the end of the chunk, on the first failure, or the moment a
        human-owned step is reached. The agent never continues past its remit.
        """

        run = self.get_run(run_id)
        chunk = run.chunk(chunk_id)
        if chunk is None:
            raise DomainError(f"unknown chunk '{chunk_id}'")

        # The agent performs the work; `actor` is the human who triggered it.
        # Both are recorded so accountability and agency stay distinguishable.
        triggered_by = actor
        agent = "agent"

        approval = self.store.active_approval(run_id, chunk_id)
        if approval is None:
            # A refused attempt is itself auditable evidence.
            self.store.append_audit(
                run.run_id,
                "execution.refused",
                triggered_by,
                {"chunk_id": chunk_id, "reason": "no active approval"},
            )
            raise DomainError(
                "no active approval for this chunk; a human must approve it first"
            )

        expected = planner.binding_digest(run, chunk)
        if approval.binding_digest != expected:
            self.store.append_audit(
                run.run_id,
                "execution.refused",
                triggered_by,
                {
                    "chunk_id": chunk_id,
                    "reason": "binding digest mismatch",
                    "approved_digest": approval.binding_digest,
                    "current_digest": expected,
                },
            )
            raise DomainError(
                "the plan changed since it was approved; re-approval is required"
            )

        # Order matters: refuse to jump ahead of unfinished earlier steps.
        nxt = run.next_actionable()
        if nxt is not None and nxt.chunk_id != chunk_id:
            self.store.append_audit(
                run.run_id,
                "execution.refused",
                triggered_by,
                {
                    "chunk_id": chunk_id,
                    "reason": "out of order",
                    "outstanding_step": nxt.sequence,
                },
            )
            raise DomainError(
                f"step {nxt.sequence} is still outstanding; chunks run in order"
            )

        # Separation of duties: the triggering actor must differ from the
        # approver, so no single person can both authorise and run their own
        # work. The audit already records both; this enforces it.
        if _sod_enforced() and approval.actor and approval.actor == triggered_by:
            self.store.append_audit(
                run.run_id,
                "execution.refused",
                triggered_by,
                {
                    "chunk_id": chunk_id,
                    "reason": "separation of duties",
                    "approved_by": approval.actor,
                },
            )
            raise DomainError(
                "separation of duties: the person who approved this chunk cannot "
                "also trigger its execution"
            )

        # Approval expiry: an old approval no longer authorises execution. This
        # bounds the window between review and action, so a plan cannot be
        # approved and then run much later against a changed world.
        ttl = _approval_ttl_seconds()
        if ttl > 0:
            age = _age_seconds(approval.created_at)
            if age is not None and age > ttl:
                self.store.append_audit(
                    run.run_id,
                    "execution.refused",
                    triggered_by,
                    {
                        "chunk_id": chunk_id,
                        "reason": "approval expired",
                        "age_seconds": int(age),
                        "ttl_seconds": ttl,
                    },
                )
                raise DomainError(
                    "this approval has expired; a human must approve the chunk again"
                )

        run.state = RunState.RUNNING
        self.store.save_run(run)

        for sid in chunk.step_ids:
            step = run.step(sid)
            if step is None or step.state in (StepState.SUCCEEDED, StepState.SKIPPED):
                continue

            if step.owner is not Owner.AI:
                step.state = StepState.NEEDS_HUMAN
                break

            if step.state is not StepState.READY:
                step.state = StepState.BLOCKED
                self.store.append_audit(
                    run.run_id,
                    "step.blocked",
                    agent,
                    {
                        "reason": "step was not approved",
                        "sequence": step.sequence,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            if simulator.unresolved_tokens(step):
                step.state = StepState.NEEDS_HUMAN
                step.outcome = "unresolved parameter in rendered step"
                self.store.append_audit(
                    run.run_id,
                    "step.needs_human",
                    agent,
                    {
                        "reason": "unresolved parameter",
                        "rendered": step.rendered,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            try:
                definition = action_catalog.resolve(step.action)
            except action_catalog.UnknownAction as exc:
                step.state = StepState.NEEDS_HUMAN
                step.outcome = str(exc)
                self.store.append_audit(
                    run.run_id,
                    "step.refused",
                    agent,
                    {
                        "reason": str(exc),
                        "sequence": step.sequence,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            envelope = build_envelope(
                run, chunk, step, approval, definition, triggered_by
            )
            claimed, execution = self.store.claim_action_execution(
                envelope.to_dict()
            )
            if not claimed:
                if execution["status"] == "succeeded":
                    # A prior request completed after this caller loaded its run.
                    # Reuse the durable result; never dispatch the connector twice.
                    step.state = StepState.SUCCEEDED
                    step.outcome = execution.get("outcome") or ""
                    step.completed_by = agent
                    step.completed_at = execution.get("updated_at") or now_iso()
                    self.store.append_audit(
                        run.run_id,
                        "step.execution_replayed",
                        agent,
                        {
                            "sequence": step.sequence,
                            "action": definition.reference,
                            "idempotency_key": envelope.idempotency_key,
                            "triggered_by": triggered_by,
                        },
                        step_id=step.step_id,
                    )
                    continue

                if execution["status"] == "claimed":
                    step.state = StepState.READY
                    planner.recompute_state(run)
                    self.store.save_run(run)
                    self.store.append_audit(
                        run.run_id,
                        "execution.refused",
                        triggered_by,
                        {
                            "chunk_id": chunk_id,
                            "step_id": step.step_id,
                            "reason": "execution already in progress",
                            "idempotency_key": envelope.idempotency_key,
                        },
                        step_id=step.step_id,
                    )
                    raise DomainError(
                        f"step {step.sequence} execution is already in progress"
                    )

                step.state = StepState.NEEDS_HUMAN
                step.outcome = execution.get("error") or "earlier execution failed"
                self.store.append_audit(
                    run.run_id,
                    "step.needs_human",
                    agent,
                    {
                        "reason": "earlier execution attempt failed",
                        "idempotency_key": envelope.idempotency_key,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            step.state = StepState.RUNNING
            self.store.save_run(run)
            try:
                result = self.gateway.dispatch(envelope, step)
            except ConnectorRefused as exc:
                self.store.fail_action_execution(envelope.idempotency_key, str(exc))
                step.state = StepState.NEEDS_HUMAN
                step.outcome = str(exc)
                self.store.append_audit(
                    run.run_id,
                    "step.refused",
                    agent,
                    {
                        "reason": str(exc),
                        "sequence": step.sequence,
                        "action": definition.reference,
                        "connector": definition.connector,
                        "operation": definition.operation,
                        "idempotency_key": envelope.idempotency_key,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break
            except Exception as exc:
                self.store.fail_action_execution(envelope.idempotency_key, str(exc))
                step.state = StepState.FAILED
                step.outcome = f"connector failure: {exc}"
                self.store.append_audit(
                    run.run_id,
                    "step.failed",
                    agent,
                    {
                        "reason": str(exc),
                        "sequence": step.sequence,
                        "action": definition.reference,
                        "connector": definition.connector,
                        "operation": definition.operation,
                        "idempotency_key": envelope.idempotency_key,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            outcome = result.summary
            self.store.complete_action_execution(envelope.idempotency_key, outcome)

            # Postcondition verification: the connector reported success, but a
            # claim is not a confirmed state change. Confirm the action's effect
            # before marking the step done. If it cannot be confirmed, the step
            # stops for a human -- the plan never continues on an unverified
            # state change.
            verdict = verification.verify(definition, envelope, result)
            if verdict.blocks:
                step.state = StepState.NEEDS_HUMAN
                step.outcome = f"{outcome} (unverified: {verdict.detail})"
                self.store.append_audit(
                    run.run_id,
                    "step.unverified",
                    agent,
                    {
                        "sequence": step.sequence,
                        "action": step.action,
                        "action_reference": definition.reference,
                        "outcome": outcome,
                        "verification": verdict.to_dict(),
                        "idempotency_key": envelope.idempotency_key,
                        "triggered_by": triggered_by,
                    },
                    step_id=step.step_id,
                )
                break

            step.state = StepState.SUCCEEDED
            step.outcome = outcome
            step.completed_by = agent
            step.completed_at = now_iso()
            self.store.append_audit(
                run.run_id,
                "step.executed",
                agent,
                {
                    "sequence": step.sequence,
                    "action": step.action,
                    "action_reference": definition.reference,
                    "catalog_version": envelope.catalog_version,
                    "risk": step.risk.value,
                    "rendered": step.rendered,
                    "outcome": outcome,
                    "simulated": result.simulated,
                    "connector": result.connector,
                    "operation": result.operation,
                    "idempotency_key": envelope.idempotency_key,
                    "evidence": result.evidence,
                    "verification": verdict.to_dict(),
                    "triggered_by": triggered_by,
                    "approved_by": approval.actor,
                },
                step_id=step.step_id,
            )

        approval.consumed_at = now_iso()
        self.store.save_approval(approval)

        planner.recompute_state(run)
        self.store.save_run(run)

        nxt = run.next_actionable()
        self.store.append_audit(
            run.run_id,
            "chunk.finished",
            agent,
            {
                "chunk_id": chunk_id,
                "run_state": run.state.value,
                "triggered_by": triggered_by,
                "handoff": (
                    None
                    if nxt is None
                    else {
                        "sequence": nxt.sequence,
                        "owner": nxt.owner.value,
                        "description": nxt.description,
                        "risk": nxt.risk.value,
                    }
                ),
            },
        )
        return run

    def dispatch_chunk_async(self, run_id: str, chunk_id: str, producer: Any) -> dict[str, Any]:
        """Enqueue an approved chunk for asynchronous worker execution.

        The producer side of the SQS + Step Functions path: instead of running
        execute_chunk in the request, this validates the chunk is ready and
        enqueues one dispatch message (the Step Functions message shape) for the
        worker to consume. The worker then runs the SAME execute_chunk path, so
        every safety guard still applies -- this only moves *where* it runs, not
        *whether* it is checked.

        Validates before enqueueing (fail fast, and never enqueue work that
        would just be refused): an active approval whose binding digest matches
        the current plan and has not expired, and a next actionable step that is
        agent-owned. The FIFO group is the run (serialising its steps) and the
        de-dup id is the step's idempotency key, so a double-dispatch is
        collapsed by the queue. Enqueueing authorises nothing new; the approval
        already did, and execution is still simulated.
        """

        run = self.get_run(run_id)
        chunk = run.chunk(chunk_id)
        if chunk is None:
            raise DomainError(f"unknown chunk '{chunk_id}'")

        approval = self.store.active_approval(run_id, chunk_id)
        if approval is None:
            raise DomainError("no active approval for this chunk; approve it first")
        if approval.binding_digest != planner.binding_digest(run, chunk):
            raise DomainError("the plan changed since it was approved; re-approval is required")
        ttl = _approval_ttl_seconds()
        if ttl > 0:
            age = _age_seconds(approval.created_at)
            if age is not None and age > ttl:
                raise DomainError("this approval has expired; a human must approve it again")

        nxt = run.next_actionable()
        if nxt is None or nxt.chunk_id != chunk_id:
            raise DomainError("this chunk has no actionable step ready to dispatch")
        if nxt.owner is not Owner.AI:
            raise DomainError(
                f"step {nxt.sequence} is human-owned and cannot be dispatched to the agent"
            )

        # The worker will run execute_chunk(actor=triggered_by). Separation of
        # duties forbids the approver from also triggering, so an async dispatch
        # is triggered by a distinct system actor, never the approver.
        triggered_by = "worker-dispatch"
        if _sod_enforced() and approval.actor == triggered_by:
            triggered_by = "worker-dispatch-2"
        definition = action_catalog.resolve(nxt.action)
        envelope = build_envelope(run, chunk, nxt, approval, definition, triggered_by)
        body = {
            "taskToken": "",  # set by Step Functions in production; empty for a bare enqueue
            "runId": run_id,
            "stepId": nxt.step_id,
            "envelope": envelope.to_dict(),
        }
        producer.send(body, group_id=run_id, dedup_id=envelope.idempotency_key)

        self.store.append_audit(
            run_id,
            "chunk.dispatched_async",
            "system",
            {
                "chunk_id": chunk_id,
                "step_id": nxt.step_id,
                "sequence": nxt.sequence,
                "idempotency_key": envelope.idempotency_key,
            },
            step_id=nxt.step_id,
        )
        return {
            "dispatched": True,
            "run_id": run_id,
            "chunk_id": chunk_id,
            "step_id": nxt.step_id,
            "idempotency_key": envelope.idempotency_key,
        }

    def recover_stale_executions(
        self, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """Recover action executions orphaned by a crashed worker.

        The idempotency ledger dispatches an action at most once, but a worker
        that dies between claiming an execution and recording its outcome leaves
        the record stuck in 'claimed' -- which would wedge that step forever.
        This fails such claims (older than the timeout) rather than retrying
        them, so the step surfaces as a failed attempt a human resolves. Safe to
        call on startup and periodically. Returns the reclaimed records.
        """

        ttl = (
            timeout_seconds
            if timeout_seconds is not None
            else int(os.getenv("LOKI_EXECUTION_CLAIM_TIMEOUT_SECONDS", "900"))
        )
        reclaimed = self.store.reclaim_stale_executions(ttl)
        for rec in reclaimed:
            self.store.append_audit(
                rec.get("run_id") or "unknown",
                "execution.reclaimed",
                "system",
                {
                    "idempotency_key": rec.get("idempotency_key"),
                    "step_id": rec.get("step_id"),
                    "claimed_since": rec.get("updated_at"),
                    "timeout_seconds": ttl,
                    "reason": "worker did not finish before timeout",
                },
                step_id=rec.get("step_id"),
            )
        return {"reclaimed": len(reclaimed), "timeout_seconds": ttl, "records": reclaimed}

    # -- human steps -----------------------------------------------------

    def complete_step_manually(
        self, run_id: str, step_id: str, actor: str, evidence: str = ""
    ) -> TaskRun:
        """A human records that they did the step, with evidence."""

        run = self.get_run(run_id)
        step = run.step(step_id)
        if step is None:
            raise DomainError(f"unknown step '{step_id}'")
        if step.state in (StepState.SUCCEEDED, StepState.SKIPPED):
            raise DomainError(f"step {step.sequence} is already {step.state.value}")

        if not evidence.strip():
            raise DomainError(
                "evidence is required when completing a step manually"
            )

        step.state = StepState.SUCCEEDED
        step.outcome = "completed by human"
        step.evidence = evidence.strip()
        step.completed_by = actor
        step.completed_at = now_iso()

        planner.recompute_state(run)
        self.store.save_run(run)
        self.store.append_audit(
            run.run_id,
            "step.completed_by_human",
            actor,
            {
                "sequence": step.sequence,
                "action": step.action,
                "risk": step.risk.value,
                "evidence": step.evidence,
            },
            step_id=step.step_id,
        )
        return run

    def skip_step(
        self, run_id: str, step_id: str, actor: str, reason: str
    ) -> TaskRun:
        run = self.get_run(run_id)
        step = run.step(step_id)
        if step is None:
            raise DomainError(f"unknown step '{step_id}'")
        if not reason.strip():
            raise DomainError("a reason is required to skip a step")

        step.state = StepState.SKIPPED
        step.outcome = f"skipped: {reason.strip()}"
        step.completed_by = actor
        step.completed_at = now_iso()

        planner.recompute_state(run)
        self.store.save_run(run)
        self.store.append_audit(
            run.run_id,
            "step.skipped",
            actor,
            {"sequence": step.sequence, "reason": reason.strip()},
            step_id=step.step_id,
        )
        return run

    def cancel_run(self, run_id: str, actor: str, reason: str = "") -> TaskRun:
        run = self.get_run(run_id)
        run.state = RunState.CANCELLED
        self.store.save_run(run)
        self.store.append_audit(
            run.run_id, "run.cancelled", actor, {"reason": reason}
        )
        return run

    # -- internals -------------------------------------------------------

    def _procedure(self, procedure_id: str):
        proc = next(
            (p for p in self._procedures if p.procedure_id == procedure_id), None
        )
        if proc is None:
            raise DomainError(
                f"procedure '{procedure_id}' is no longer loaded; it may have been "
                "removed or renamed since this run was created"
            )
        return proc

    # ===================================================================
    # Customisation: uploaded handbooks and dictionaries
    # ===================================================================

    MAX_UPLOAD_BYTES = int(os.getenv("OVERLAY_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))

    def upload_knowledge(
        self, raw: bytes, filename: str, kind: str, actor: str
    ) -> dict[str, Any]:
        """Parse an upload into drafts (handbook) or entries (dictionary).

        Parsing never changes behaviour. A handbook becomes drafts that a human
        must publish; a dictionary takes effect immediately but can only widen
        matching, never authorise execution.
        """

        import hashlib

        try:
            source_kind = SourceKind(kind)
        except ValueError:
            raise DomainError(
                f"kind must be 'handbook' or 'dictionary', got '{kind}'"
            ) from None

        if not raw:
            raise DomainError("the uploaded file is empty")
        if len(raw) > self.MAX_UPLOAD_BYTES:
            raise DomainError(
                f"file is {len(raw):,} bytes; the limit is "
                f"{self.MAX_UPLOAD_BYTES:,}"
            )

        digest = hashlib.sha256(raw).hexdigest()
        duplicate = self.store.find_source_by_hash(digest)

        source = KnowledgeSource(
            source_id=new_id("src"),
            filename=filename,
            kind=source_kind,
            file_format=knowledge.detect_format(filename),
            size_bytes=len(raw),
            sha256=digest,
            uploaded_by=actor,
        )

        warnings: list[str] = []
        if duplicate:
            warnings.append(
                f"identical content was already uploaded as "
                f"'{duplicate['filename']}' on {duplicate['uploaded_at']}"
            )

        try:
            if source_kind is SourceKind.HANDBOOK:
                procedures, parse_warnings = knowledge.parse_handbook(raw, filename)
                warnings.extend(parse_warnings)
                drafts = knowledge.build_drafts(source.source_id, procedures)
                for d in drafts:
                    self.store.save_draft(d)
                source.draft_count = len(drafts)
                result_detail = {
                    "drafts": [d.to_dict() for d in drafts],
                    "entries": [],
                }
            else:
                records, parse_warnings = knowledge.parse_dictionary(raw, filename)
                warnings.extend(parse_warnings)
                entries = knowledge.build_dictionary_entries(source.source_id, records)
                for e in entries:
                    self.store.save_dictionary_entry(e)
                source.entry_count = len(entries)
                result_detail = {
                    "drafts": [],
                    "entries": [e.to_dict() for e in entries],
                }
        except knowledge.ParseError as exc:
            source.status = "failed"
            source.errors = [str(exc)]
            source.warnings = warnings
            # Keep the text even on failure so the author can see what was read.
            self.store.save_source(source, content=knowledge.decode(raw))
            raise DomainError(f"could not parse '{filename}': {exc}") from None

        source.warnings = warnings
        self.store.save_source(source, content=knowledge.decode(raw))

        if source_kind is SourceKind.DICTIONARY:
            # Matching changes immediately, so refresh the cached dictionary.
            self.registry.reload()

        return {
            "source": source.to_dict(),
            **result_detail,
            "note": (
                "Drafts are not active. Publish a draft to add it to the live "
                "rule set."
                if source_kind is SourceKind.HANDBOOK
                else "Dictionary entries are active now and affect matching only."
            ),
        }

    def list_sources(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Uploaded files, each annotated with the procedures it produced."""

        sources = self.store.list_sources(kind)
        drafts = self.store.list_drafts()

        by_source: dict[str, list[dict[str, Any]]] = {}
        for d in drafts:
            by_source.setdefault(d.source_id, []).append(
                {
                    "draft_id": d.draft_id,
                    "procedure_id": d.procedure_id,
                    "name": d.name,
                    "status": d.status.value,
                    "publishable": d.publishable,
                    "validation_errors": d.validation_errors,
                    "risk_warnings": d.risk_warnings,
                }
            )

        for s in sources:
            s["procedures"] = by_source.get(s["source_id"], [])
            s["published_count"] = sum(
                1 for p in s["procedures"] if p["status"] == "published"
            )
            s["pending_count"] = sum(
                1 for p in s["procedures"] if p["status"] == "draft"
            )
        return sources

    def source_content(self, source_id: str) -> dict[str, Any]:
        """The raw uploaded text, so a handbook can be read in the UI."""

        source = self.store.get_source(source_id)
        if source is None:
            raise DomainError(f"unknown source '{source_id}'")

        content = self.store.get_source_content(source_id)
        return {
            "source_id": source_id,
            "filename": source["filename"],
            "file_format": source["file_format"],
            "kind": source["kind"],
            "uploaded_by": source["uploaded_by"],
            "uploaded_at": source["uploaded_at"],
            "size_bytes": source["size_bytes"],
            "content": content,
            "available": content is not None,
            "note": (
                None
                if content is not None
                else "This file was uploaded before contents were retained. "
                "Re-upload it to make it readable here."
            ),
        }

    def delete_source(self, source_id: str, actor: str) -> dict[str, Any]:
        """Remove an uploaded file and everything it produced.

        Published procedures from this source are withdrawn from the live rule
        set, because leaving them active with no visible origin would be worse
        than removing them.
        """

        source = self.store.get_source(source_id)
        if source is None:
            raise DomainError(f"unknown source '{source_id}'")

        removed = self.store.delete_source_cascade(source_id)
        count = self.registry.reload()
        return {
            "source_id": source_id,
            "filename": source["filename"],
            "removed": removed,
            "procedures_loaded": count,
            "removed_by": actor,
        }

    def list_drafts(self, status: str | None = None) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self.store.list_drafts(status=status)]

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            raise DomainError(f"unknown draft '{draft_id}'")
        out = draft.to_dict()
        # Show exactly what publishing would mean, step by step.
        out["effect"] = knowledge.summarise_effect(draft.payload)
        out["conflicts_with_existing"] = (
            self.registry.get(draft.procedure_id) is not None
        )
        return out

    def publish_draft(
        self, draft_id: str, actor: str, note: str = ""
    ) -> dict[str, Any]:
        """The human review gate. Only this puts an upload into the rule set."""

        draft = self.store.get_draft(draft_id)
        if draft is None:
            raise DomainError(f"unknown draft '{draft_id}'")
        if draft.status is not DraftStatus.DRAFT:
            raise DomainError(f"draft is already {draft.status.value}")

        errors = knowledge.validate_procedure(draft.payload)
        if errors:
            raise DomainError(
                "this draft cannot be published until these are fixed: "
                + "; ".join(errors)
            )

        replaced = self.registry.get(draft.procedure_id) is not None

        self.store.publish_procedure(
            procedure_id=draft.procedure_id,
            name=draft.name,
            payload=draft.payload,
            published_by=actor,
            source_id=draft.source_id,
            draft_id=draft.draft_id,
        )

        draft.status = DraftStatus.PUBLISHED
        draft.reviewed_by = actor
        draft.reviewed_at = now_iso()
        draft.review_note = note
        self.store.save_draft(draft)

        count = self.registry.reload()
        effect = knowledge.summarise_effect(draft.payload)

        return {
            "draft": draft.to_dict(),
            "procedures_loaded": count,
            "replaced_existing": replaced,
            "effect": effect,
            "registry_errors": self.registry.load_errors,
        }

    def reject_draft(self, draft_id: str, actor: str, note: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            raise DomainError(f"unknown draft '{draft_id}'")
        if not note.strip():
            raise DomainError("a reason is required when rejecting a draft")

        draft.status = DraftStatus.REJECTED
        draft.reviewed_by = actor
        draft.reviewed_at = now_iso()
        draft.review_note = note.strip()
        self.store.save_draft(draft)
        return draft.to_dict()

    def list_published(self) -> list[dict[str, Any]]:
        return self.store.list_published(only_enabled=False)

    def set_published_enabled(
        self, procedure_id: str, enabled: bool
    ) -> dict[str, Any]:
        """Disable a published procedure without deleting its history."""

        if not self.store.set_published_enabled(procedure_id, enabled):
            raise DomainError(f"'{procedure_id}' is not a published procedure")
        count = self.registry.reload()
        return {
            "procedure_id": procedure_id,
            "enabled": enabled,
            "procedures_loaded": count,
        }

    def list_dictionary(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.store.list_dictionary(only_enabled=False)]

    def set_dictionary_enabled(self, entry_id: str, enabled: bool) -> dict[str, Any]:
        if not self.store.set_dictionary_enabled(entry_id, enabled):
            raise DomainError(f"unknown dictionary entry '{entry_id}'")
        self.registry.reload()
        return {"entry_id": entry_id, "enabled": enabled}

    def test_dictionary(
        self, text: str, assignment_group: str = ""
    ) -> dict[str, Any]:
        """Show how the dictionary rewrites a phrase and whether it matches.

        Reports the near misses too. A single keyword scores 2.0 against a
        threshold of 3.0, so a dictionary can genuinely fire and the ticket still
        not match. Saying only "no match" would look like the dictionary failed.
        """

        import engine as _engine  # already on sys.path via _system2

        ticket = {
            "short_description": text,
            "description": "",
            "category": "",
            "assignment_group": assignment_group,
        }
        augmented, applied = self.registry.augment_ticket(ticket)
        match = _system2.match_ticket(augmented, self._procedures)
        ranked = _system2.rank_matches(augmented, self._procedures)

        threshold = float(_engine.MIN_SCORE)
        best = ranked[0] if ranked else None

        explanation = ""
        if match is None and best is not None:
            shortfall = threshold - float(best.score)
            explanation = (
                f"Closest is '{best.procedure.name}' at {best.score:.1f}, "
                f"{shortfall:.1f} short of the {threshold:.1f} needed. "
            )
            if not best.group_match and best.procedure.trigger.assignment_group:
                explanation += (
                    f"Matching the assignment group "
                    f"'{best.procedure.trigger.assignment_group}' would add 1.0, "
                    f"and each extra keyword adds 2.0."
                )
            else:
                explanation += "Each additional matching keyword adds 2.0."
        elif match is None:
            explanation = "No procedure shares any keyword with this text."

        return {
            "input": text,
            "terms_applied": applied,
            "matched": match is not None,
            "procedure_id": match.procedure.procedure_id if match else None,
            "procedure_name": match.procedure.name if match else None,
            "score": float(match.score) if match else 0.0,
            "threshold": threshold,
            "explanation": explanation,
            "near_misses": [
                {
                    "procedure_id": m.procedure.procedure_id,
                    "procedure_name": m.procedure.name,
                    "score": float(m.score),
                    "matched_keywords": list(m.matched_keywords),
                    "group_match": bool(m.group_match),
                }
                for m in ranked[:3]
            ],
        }

    # ===================================================================
    # Live ticket intake (read-only)
    # ===================================================================

    @property
    def live(self):
        """Lazily built so importing the service never opens a connection."""

        if getattr(self, "_live", None) is None:
            from .live import LiveFeed

            self._live = LiveFeed(self)
        return self._live

    def live_status(self) -> dict[str, Any]:
        try:
            return self.live.status()
        except Exception as exc:  # status must never break the summary endpoint
            return {"enabled": False, "running": False, "error": str(exc)}

    def live_config(self) -> dict[str, Any]:
        return self.live.config()

    def live_query_options(self) -> dict[str, Any]:
        """The dropdowns the Live tab renders instead of a raw query box."""

        from . import live as live_mod

        return live_mod.query_builder_options()

    def update_live_config(
        self, updates: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        from .live import LiveFeedError

        try:
            cfg = self.live.update_config(updates)
        except LiveFeedError as exc:
            raise DomainError(str(exc)) from None

        self.store.set_setting(
            "live_feed_last_change",
            {"by": actor, "at": now_iso(), "changes": updates},
        )
        return cfg

    def live_probe(self) -> dict[str, Any]:
        """Test the connection without ingesting anything."""

        from .live import LiveFeedError

        try:
            return self.live.probe()
        except LiveFeedError as exc:
            raise DomainError(str(exc)) from None

    def live_poll_now(self, actor: str) -> dict[str, Any]:
        """Manual poll. Works whether or not the background thread is running."""

        from .live import LiveFeedError

        try:
            return self.live.poll_once(actor=actor)
        except LiveFeedError as exc:
            raise DomainError(str(exc)) from None

    def list_live_tickets(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.store.list_live_tickets(status, limit)]

    def propose_from_live(
        self, live_id: str, actor: str, allow_duplicate: bool = False,
        mode: str = "simulation",
    ) -> dict[str, Any]:
        """Turn an ingested live ticket into a proposed run."""

        from .domain import LiveTicketStatus

        live = self.store.get_live_ticket(live_id)
        if live is None:
            raise DomainError(f"unknown live ticket '{live_id}'")
        if live.run_id:
            raise DomainError(
                f"{live.ticket_number} already has run {live.run_id}"
            )

        run = self.propose(
            live.payload, actor=actor, allow_duplicate=allow_duplicate, mode=mode
        )
        live.status = LiveTicketStatus.PROPOSED
        live.run_id = run.run_id
        self.store.update_live_ticket(live)
        return {"live_ticket": live.to_dict(), "run": run.to_dict()}

    def ignore_live_ticket(
        self, live_id: str, actor: str, reason: str = ""
    ) -> dict[str, Any]:
        from .domain import LiveTicketStatus

        live = self.store.get_live_ticket(live_id)
        if live is None:
            raise DomainError(f"unknown live ticket '{live_id}'")

        live.status = LiveTicketStatus.IGNORED
        live.match["ignored_by"] = actor
        live.match["ignored_reason"] = reason
        live.match["ignored_at"] = now_iso()
        self.store.update_live_ticket(live)
        return live.to_dict()

    def start_live_feed_if_enabled(self) -> bool:
        """Start the supervisor in every process; the DB lease selects one poller."""

        return self.live.start()

    def shutdown(self) -> None:
        if getattr(self, "_live", None) is not None:
            self._live.stop()
        self.store.close()

    # ===================================================================
    # Access credentials centre
    # ===================================================================
    # Records point at secrets; they never contain them. See
    # overlay/credentials.py for the reasoning and the storage options.

    def connector_catalogue(self) -> list[dict[str, Any]]:
        from . import credentials as cred_mod

        return cred_mod.connector_catalogue()

    def list_credentials(self) -> list[dict[str, Any]]:
        """Credentials with their pointer resolvability, never their values."""

        from . import credentials as cred_mod

        out = []
        for row in self.store.list_credentials():
            row = dict(row)
            row["secret"] = cred_mod.describe_ref(row.get("secret_ref", ""))
            out.append(row)
        return out

    def add_credential(
        self,
        name: str,
        connector: str,
        account: str,
        scope: list[str],
        permissions: list[str],
        note: str,
        actor: str,
        secret_ref: str = "",
        secret_value: str = "",
    ) -> dict[str, Any]:
        """Register a credential.

        Either supply a pointer (`secret_ref`), or paste a value which is put in
        the OS credential store and reduced to a pointer. The value is not kept
        by this application either way.
        """

        from . import credentials as cred_mod

        if secret_value and secret_ref:
            raise DomainError(
                "supply either a reference or a value to store, not both"
            )

        if secret_value:
            handle = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{connector}-{account}").strip("-")
            secret_ref = cred_mod.store_secret_in_os_vault(handle, secret_value)

        cred = cred_mod.new_credential(
            name=name,
            connector=connector,
            account=account,
            secret_ref=secret_ref,
            scope=scope,
            permissions=permissions,
            note=note,
            actor=actor,
        )
        self.store.save_credential(cred.to_dict())

        # Registering access is itself an auditable act.
        self.store.append_audit(
            f"credential:{cred.credential_id}",
            "credential.registered",
            actor,
            {
                "name": cred.name,
                "connector": cred.connector,
                "account": cred.account,
                # The pointer is recorded; the value is not, and never was.
                "secret_ref": cred.secret_ref,
                "scope": cred.scope,
                "permissions": cred.permissions,
            },
        )

        row = cred.to_dict()
        row["secret"] = cred_mod.describe_ref(cred.secret_ref)
        return row

    def test_credential(self, credential_id: str, actor: str) -> dict[str, Any]:
        from . import credentials as cred_mod

        raw = self.store.get_credential(credential_id)
        if raw is None:
            raise DomainError(f"unknown credential '{credential_id}'")

        cred = cred_mod.Credential(**{
            k: v for k, v in raw.items()
            if k in cred_mod.Credential.__dataclass_fields__
        })
        result = cred_mod.test_credential(cred)

        raw["last_tested_at"] = result["checked_at"]
        raw["last_test_ok"] = result["ok"]
        raw["last_test_detail"] = result["detail"]
        self.store.save_credential(raw)

        self.store.append_audit(
            f"credential:{credential_id}",
            "credential.tested",
            actor,
            {"ok": result["ok"], "stage": result["stage"], "detail": result["detail"]},
        )
        return result

    def set_credential_enabled(
        self, credential_id: str, enabled: bool, actor: str
    ) -> dict[str, Any]:
        raw = self.store.get_credential(credential_id)
        if raw is None:
            raise DomainError(f"unknown credential '{credential_id}'")

        raw["enabled"] = bool(enabled)
        self.store.save_credential(raw)
        self.store.append_audit(
            f"credential:{credential_id}",
            "credential.enabled" if enabled else "credential.disabled",
            actor,
            {"name": raw.get("name")},
        )
        return {"credential_id": credential_id, "enabled": raw["enabled"]}

    def delete_credential(self, credential_id: str, actor: str) -> dict[str, Any]:
        raw = self.store.get_credential(credential_id)
        if raw is None:
            raise DomainError(f"unknown credential '{credential_id}'")

        self.store.delete_credential(credential_id)
        self.store.append_audit(
            f"credential:{credential_id}",
            "credential.removed",
            actor,
            {"name": raw.get("name"), "connector": raw.get("connector")},
        )
        return {
            "credential_id": credential_id,
            "removed": True,
            "note": (
                "The pointer was removed. If a value was stored in the OS "
                "credential store it is still there -- remove it from Windows "
                "Credential Manager if it is no longer wanted."
            ),
        }
