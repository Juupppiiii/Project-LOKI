"""HTTP API -- the contract every client speaks.

This is the only way in. A browser page, a desktop app, a CLI or a Teams bot all
call these endpoints, so the rules cannot be bypassed by swapping the UI.

Two deliberate properties keep the client swappable:

* Every response is JSON. No server-rendered data, no template state.
* The API is stateless per request (no cookies, no session). A client
  identifies itself with the `X-Actor` header, which a real deployment replaces
  with SSO-issued claims.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import DomainError, DuplicateRunError
from .service import OverlayService

_WEB_DIR = Path(__file__).resolve().parent / "web"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Resume an enabled live feed on startup; stop it cleanly on shutdown."""

    try:
        service.start_live_feed_if_enabled()
    except Exception:
        pass  # a broken feed must never stop the server from booting
    yield
    service.shutdown()


app = FastAPI(
    title="LOKI API",
    version="0.2.0",
    description=(
        "LOKI — human-in-the-loop ticket automation.\n\n"
        "Ticket data may be read live from ServiceNow, but execution is "
        "simulation only: no connectors, no writes to any target system."
    ),
    lifespan=lifespan,
)

# A desktop app runs on a different origin than the API, so CORS has to allow
# it. Credentials (the session cookie) must be allowed, which means the origin
# cannot be the "*" wildcard when a specific origin is set.
_origins = os.getenv("OVERLAY_ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = OverlayService()

# --- authentication ---------------------------------------------------------

from fastapi import Cookie, Depends, Request, Response  # noqa: E402

from . import auth  # noqa: E402

_sessions = auth.SessionManager()

# When True, write endpoints require a signed-in session and the header actor is
# ignored. Off by default so existing local use keeps working; the moment Entra
# is configured or dev login is used, sessions drive identity.
_ENFORCE = os.getenv("LOKI_ENFORCE_AUTH", "0") not in ("0", "false", "False", "")


def current_identity(
    request: Request,
    loki_session: str | None = Cookie(default=None),
) -> "auth.Identity | None":
    return _sessions.read(loki_session)


def require_actor(
    identity: "auth.Identity | None" = Depends(current_identity),
    x_actor: str | None = Header(default=None),
) -> str:
    """The verified actor for a write.

    With auth enforced, this comes only from the signed session -- the
    X-Actor header is ignored, so it can no longer be spoofed. With auth not
    enforced (local prototype), the header still works for backward
    compatibility.
    """

    if identity is not None:
        return identity.email
    if _ENFORCE:
        raise HTTPException(status_code=401, detail="sign in to do that")
    return (x_actor or "unknown").strip() or "unknown"


def require_capability(capability: str):
    """Dependency factory: allow the action only if the role permits it."""

    def _check(identity: "auth.Identity | None" = Depends(current_identity),
               x_actor: str | None = Header(default=None)) -> str:
        if identity is not None:
            if not auth.role_allows(identity.role, capability):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"your role ({identity.role}) cannot {capability.replace('_', ' ')}"
                    ),
                )
            return identity.email
        if _ENFORCE:
            raise HTTPException(status_code=401, detail="sign in to do that")
        return (x_actor or "unknown").strip() or "unknown"

    return _check


# All write routes derive their actor from the signed session via
# require_actor / require_capability. There is no longer a header-only actor
# resolver: when auth is enforced the X-Actor header cannot name the actor, and
# when it is not enforced the same dependencies fall back to the header for the
# local prototype.


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class TicketIn(BaseModel):
    number: str | None = None
    short_description: str = ""
    description: str = ""
    category: str = ""
    assignment_group: str = ""
    cmdb_ci: str = ""
    priority: str = ""

    def to_ticket(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = ""


class ParametersIn(BaseModel):
    parameters: dict[str, Any]


class ManualCompletionIn(BaseModel):
    evidence: str


class SkipIn(BaseModel):
    reason: str


class CancelIn(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "mode": "simulation"}


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    status = service.readiness()
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "backend": status["backend"],
                "schema_version": status["schema_version"],
            },
        )
    return {"status": "ready", **status}


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    return service.queue_summary()


@app.get("/api/procedures")
def procedures() -> dict[str, Any]:
    return {"procedures": service.list_procedures()}


@app.get("/api/risk-policy")
def risk_policy() -> dict[str, Any]:
    """The classification table, so clients display policy instead of hardcoding."""

    return {"policy": service.risk_policy_table()}


@app.get("/api/sample-tickets")
def sample_tickets(limit: int = Query(25, ge=1, le=200)) -> dict[str, Any]:
    return {"tickets": service.sample_tickets(limit)}


# ---------------------------------------------------------------------------
# Matching and proposing
# ---------------------------------------------------------------------------


@app.post("/api/match")
def match(ticket: TicketIn) -> dict[str, Any]:
    """Dry match. Shows what the engine would pick without creating a run."""

    return service.preview_match(ticket.to_ticket())


@app.post("/api/suggest")
def suggest(ticket: TicketIn, top: int = Query(3, ge=1, le=10)) -> dict[str, Any]:
    """Advisory, meaning-based procedure suggestions. Creates nothing.

    Useful when the keyword match returns nothing: it shows which procedures
    look semantically relevant so a human can decide. It never proposes a run,
    classifies risk, or authorises anything.
    """

    return service.semantic_suggestions(ticket.to_ticket(), top=top)


@app.post("/api/recommend-candidates")
def recommend_candidates(
    ticket: TicketIn, top: int = Query(5, ge=1, le=10)
) -> dict[str, Any]:
    """Ranked, evidence-cited procedure candidates with an abstain path.

    ADVISORY ONLY. Fuses the authoritative keyword matcher and the semantic
    matcher into one ranked list; each candidate cites the keywords, group
    match, resolved/missing parameters, and signals behind it, with a 0..1
    confidence. When the top candidate is below the confidence floor the
    decision is 'insufficient_evidence' and nothing is recommended. It never
    proposes a run, classifies risk, or authorises anything.
    """

    return service.recommend_candidates(ticket.to_ticket(), top=top)


@app.post("/api/recommend-steps")
def recommend_steps(ticket: TicketIn) -> dict[str, Any]:
    """Advisory, LLM-assisted step recommendation grounded in solved tickets.

    Retrieves the sanctioned procedure and the most similar real resolutions,
    then (when a Bedrock model is configured) synthesises and critiques a
    recommended approach with citations. Strictly advisory: it approves and
    executes nothing, and never changes the authoritative procedure match.
    Without a live model it returns the real retrieved grounding and says so.
    """

    return service.recommend_steps(ticket.to_ticket())


@app.post("/api/runs", status_code=201)
def create_run(
    ticket: TicketIn,
    allow_duplicate: bool = Query(
        False,
        description=(
            "Create a second run even though this ticket already has one in "
            "progress. Requires a deliberate choice."
        ),
    ),
    mode: str = Query(
        "simulation",
        description=(
            "Execution mode for the run: 'simulation' (default, touches nothing) "
            "or 'live'. A live run still only reaches a real connector when the "
            "gateway itself runs in live mode and the per-connector write switch "
            "is on, so this flag alone writes nothing."
        ),
    ),
    actor: str = Depends(require_capability("propose")),
) -> dict[str, Any]:
    try:
        run = service.propose(
            ticket.to_ticket(),
            actor=actor,
            allow_duplicate=allow_duplicate,
            mode=mode,
        )
    except DuplicateRunError as exc:
        # Structured so a client can offer "create anyway" instead of guessing.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_run",
                "message": str(exc),
                "ticket_id": exc.ticket_id,
                "existing_run_id": exc.existing_run_id,
                "existing_state": exc.existing_state,
                "retry_with": "?allow_duplicate=true",
            },
        ) from exc
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@app.get("/api/runs")
def list_runs(
    state: str | None = None,
    group: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Each run carries `runs_for_ticket` so a forked ticket is visible."""

    return {"runs": service.list_runs_annotated(state=state, group=group, limit=limit)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return service.get_run(run_id).to_dict()
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/audit")
def get_audit(run_id: str) -> dict[str, Any]:
    try:
        return {"events": service.audit(run_id)}
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/efficiency-advice")
def get_efficiency_advice(run_id: str) -> dict[str, Any]:
    """Advisory, read-only efficiency notes on a plan.

    Annotates which steps the agent can take and where a human must decide. It
    never reorders, removes, or changes any step -- `mutates_plan` is always
    False -- so it cannot affect what executes.
    """

    try:
        return service.plan_efficiency_advice(run_id)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/approvals")
def get_approvals(run_id: str) -> dict[str, Any]:
    try:
        return {"approvals": service.approvals(run_id)}
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Acting on a run
# ---------------------------------------------------------------------------


@app.patch("/api/runs/{run_id}/parameters")
def set_parameters(
    run_id: str,
    body: ParametersIn,
    actor: str = Depends(require_capability("set_parameters")),
) -> dict[str, Any]:
    try:
        run = service.set_parameters(run_id, body.parameters, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@app.post("/api/runs/{run_id}/chunks/{chunk_id}/decision")
def decide(
    run_id: str,
    chunk_id: str,
    body: DecisionIn,
    actor: str = Depends(require_capability("approve_chunk")),
) -> dict[str, Any]:
    """Approve or reject a chunk. Approving requires the approver role."""

    try:
        run = service.decide_chunk(
            run_id, chunk_id, body.decision, actor=actor, reason=body.reason
        )
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@app.post("/api/runs/{run_id}/chunks/{chunk_id}/execute")
def execute(
    run_id: str,
    chunk_id: str,
    actor: str = Depends(require_capability("approve_chunk")),
) -> dict[str, Any]:
    """Run an approved chunk in simulation, then stop at the next human step."""

    try:
        run = service.execute_chunk(run_id, chunk_id, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return run.to_dict()


@app.post("/api/runs/{run_id}/steps/{step_id}/complete")
def complete_step(
    run_id: str,
    step_id: str,
    body: ManualCompletionIn,
    actor: str = Depends(require_capability("complete_step")),
) -> dict[str, Any]:
    try:
        run = service.complete_step_manually(
            run_id, step_id, actor=actor, evidence=body.evidence
        )
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@app.post("/api/runs/{run_id}/steps/{step_id}/skip")
def skip_step(
    run_id: str,
    step_id: str,
    body: SkipIn,
    actor: str = Depends(require_capability("skip_step")),
) -> dict[str, Any]:
    try:
        run = service.skip_step(
            run_id, step_id, actor=actor, reason=body.reason
        )
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return run.to_dict()


@app.post("/api/runs/{run_id}/cancel")
def cancel(
    run_id: str,
    body: CancelIn = Body(default=CancelIn()),
    actor: str = Depends(require_capability("cancel_run")),
) -> dict[str, Any]:
    try:
        run = service.cancel_run(run_id, actor=actor, reason=body.reason)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run.to_dict()


# ---------------------------------------------------------------------------
# Static web client
# ---------------------------------------------------------------------------
# Two clients can be served, and the React build wins when it is present:
#
#   overlay/ui/dist  -- the React app (`cd overlay/ui && npm run build`)
#   overlay/web      -- the original dependency-free client, kept as a fallback
#
# The fallback matters: if the React build is missing or a build breaks, the
# tool still works rather than serving a blank page. Either way this is a
# convenience -- a desktop client ignores it and talks to /api/* directly.

_REACT_DIST = Path(__file__).resolve().parent / "ui" / "dist"
_REACT_INDEX = _REACT_DIST / "index.html"
_LEGACY_INDEX = _WEB_DIR / "index.html"

# The legacy client is always reachable at /static so it can be compared against
# the React build, and so its assets keep resolving.
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

if (_REACT_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_REACT_DIST / "assets")),
        name="assets",
    )


@app.get("/api/ui-info")
def ui_info() -> dict[str, Any]:
    """Which client is being served. Useful when a build is stale or missing."""

    return {
        "serving": "react" if _REACT_INDEX.is_file() else "legacy",
        "react_build_present": _REACT_INDEX.is_file(),
        "legacy_present": _LEGACY_INDEX.is_file(),
        "legacy_url": "/legacy" if _LEGACY_INDEX.is_file() else None,
        "build_hint": "cd overlay/ui && npm run build",
    }


@app.get("/legacy")
def legacy_index() -> FileResponse:
    """The dependency-free client, still available for comparison."""

    if not _LEGACY_INDEX.is_file():
        raise HTTPException(status_code=404, detail="legacy client is not present")
    return FileResponse(str(_LEGACY_INDEX))


@app.get("/")
def index() -> FileResponse:
    if _REACT_INDEX.is_file():
        return FileResponse(str(_REACT_INDEX))
    if _LEGACY_INDEX.is_file():
        return FileResponse(str(_LEGACY_INDEX))
    raise HTTPException(
        status_code=404,
        detail=(
            "No web client found. Build the React app with "
            "`cd overlay/ui && npm run build`."
        ),
    )


# ===========================================================================
# Customisation: uploaded handbooks and dictionaries
# ===========================================================================


class DraftDecisionIn(BaseModel):
    note: str = ""


class EnabledIn(BaseModel):
    enabled: bool


class DictionaryTestIn(BaseModel):
    text: str
    assignment_group: str = ""


@app.post("/api/knowledge/upload", status_code=201)
async def upload_knowledge(
    file: UploadFile = File(...),
    kind: str = Form(...),
    actor: str = Depends(require_capability("upload_knowledge")),
) -> dict[str, Any]:
    """Upload a handbook or dictionary.

    A handbook becomes **drafts** that a human must publish. A dictionary takes
    effect immediately but can only widen matching, never authorise execution.
    """

    raw = await file.read()
    try:
        return service.upload_knowledge(
            raw=raw,
            filename=file.filename or "upload",
            kind=kind,
            actor=actor,
        )
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/knowledge/sources")
def knowledge_sources(kind: str | None = None) -> dict[str, Any]:
    return {"sources": service.list_sources(kind)}


@app.get("/api/knowledge/formats")
def knowledge_formats() -> dict[str, Any]:
    """Tell clients what can be uploaded instead of hardcoding it in the UI."""

    from . import knowledge

    return {
        "handbook": sorted(knowledge.SUPPORTED_HANDBOOK),
        "dictionary": sorted(knowledge.SUPPORTED_DICTIONARY),
        "max_bytes": service.MAX_UPLOAD_BYTES,
    }


@app.get("/api/knowledge/drafts")
def knowledge_drafts(status: str | None = None) -> dict[str, Any]:
    return {"drafts": service.list_drafts(status)}


@app.get("/api/knowledge/drafts/{draft_id}")
def knowledge_draft(draft_id: str) -> dict[str, Any]:
    """Includes the per-step effect of publishing, so review is informed."""

    try:
        return service.get_draft(draft_id)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/knowledge/drafts/{draft_id}/publish")
def publish_draft(
    draft_id: str,
    body: DraftDecisionIn = Body(default=DraftDecisionIn()),
    actor: str = Depends(require_capability("publish_procedure")),
) -> dict[str, Any]:
    """The human review gate: the only path from an upload into the rule set."""

    try:
        return service.publish_draft(draft_id, actor=actor, note=body.note)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/knowledge/drafts/{draft_id}/reject")
def reject_draft(
    draft_id: str,
    body: DraftDecisionIn,
    actor: str = Depends(require_capability("reject_draft")),
) -> dict[str, Any]:
    try:
        return service.reject_draft(draft_id, actor=actor, note=body.note)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/knowledge/published")
def published_procedures() -> dict[str, Any]:
    return {"published": service.list_published()}


@app.patch("/api/knowledge/published/{procedure_id}")
def toggle_published(procedure_id: str, body: EnabledIn) -> dict[str, Any]:
    try:
        return service.set_published_enabled(procedure_id, body.enabled)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/knowledge/dictionary")
def dictionary() -> dict[str, Any]:
    return {"entries": service.list_dictionary()}


@app.patch("/api/knowledge/dictionary/{entry_id}")
def toggle_dictionary(entry_id: str, body: EnabledIn) -> dict[str, Any]:
    try:
        return service.set_dictionary_enabled(entry_id, body.enabled)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/knowledge/dictionary/test")
def test_dictionary(body: DictionaryTestIn) -> dict[str, Any]:
    """Paste a phrase and see how the dictionary rewrites and matches it."""

    return service.test_dictionary(body.text, body.assignment_group)


@app.post("/api/knowledge/reload")
def reload_procedures() -> dict[str, Any]:
    count = service.reload_procedures()
    return {
        "procedures_loaded": count,
        "registry_errors": service.registry.load_errors,
    }


# ===========================================================================
# Live ticket intake (read-only)
# ===========================================================================


class LiveConfigIn(BaseModel):
    enabled: bool | None = None
    table: str | None = None
    # Structured filters from the dropdowns; the encoded query is derived server-side.
    filters: dict[str, str] | None = None
    group: str | None = None
    # Advanced escape hatch: a raw encoded query that overrides the dropdowns.
    raw_query: str | None = None
    interval_seconds: int | None = None
    page_size: int | None = None
    auto_propose: bool | None = None
    only_groups: list[str] | None = None

    def changes(self) -> dict[str, Any]:
        # `group` and `raw_query` are intentionally allowed through as empty
        # strings (clear the group / clear the override), so only None is dropped.
        return {k: v for k, v in self.model_dump().items() if v is not None}


class IgnoreIn(BaseModel):
    reason: str = ""


@app.get("/api/live/status")
def live_status() -> dict[str, Any]:
    """Never returns credentials -- only whether they are present."""

    return service.live_status()


@app.get("/api/live/config")
def live_config() -> dict[str, Any]:
    return service.live_config()


@app.get("/api/live/query-options")
def live_query_options() -> dict[str, Any]:
    """Dropdown definitions so the UI never has to know the query syntax."""

    return service.live_query_options()


@app.patch("/api/live/config")
def update_live_config(
    body: LiveConfigIn,
    actor: str = Depends(require_capability("manage_live_feed")),
) -> dict[str, Any]:
    changes = body.changes()
    if not changes:
        raise HTTPException(status_code=422, detail="no settings supplied")
    try:
        return service.update_live_config(changes, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/live/probe")
def live_probe() -> dict[str, Any]:
    """Check connectivity and permissions without ingesting anything."""

    try:
        return service.live_probe()
    except DomainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/live/poll")
def live_poll(
    actor: str = Depends(require_capability("manage_live_feed")),
) -> dict[str, Any]:
    """Poll once on demand. Works with the background poller off."""

    try:
        return service.live_poll_now(actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/live/tickets")
def live_tickets(
    status: str | None = None, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    return {"tickets": service.list_live_tickets(status, limit)}


@app.post("/api/live/tickets/{live_id}/propose")
def propose_from_live(
    live_id: str,
    allow_duplicate: bool = Query(False),
    actor: str = Depends(require_capability("propose")),
) -> dict[str, Any]:
    try:
        return service.propose_from_live(
            live_id, actor=actor, allow_duplicate=allow_duplicate
        )
    except DuplicateRunError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_run",
                "message": str(exc),
                "ticket_id": exc.ticket_id,
                "existing_run_id": exc.existing_run_id,
                "existing_state": exc.existing_state,
                "retry_with": "?allow_duplicate=true",
            },
        ) from exc
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/live/tickets/{live_id}/ignore")
def ignore_live_ticket(
    live_id: str,
    body: IgnoreIn = Body(default=IgnoreIn()),
    actor: str = Depends(require_capability("manage_live_feed")),
) -> dict[str, Any]:
    try:
        return service.ignore_live_ticket(
            live_id, actor=actor, reason=body.reason
        )
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ===========================================================================
# Handbook library: read a file back, or remove it
# ===========================================================================


@app.get("/api/knowledge/sources/{source_id}/content")
def source_content(source_id: str) -> dict[str, Any]:
    """The raw uploaded text, so a handbook can be read in the UI."""

    try:
        return service.source_content(source_id)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/knowledge/sources/{source_id}")
def delete_source(
    source_id: str,
    actor: str = Depends(require_capability("delete_source")),
) -> dict[str, Any]:
    """Remove an upload and everything it produced, published included."""

    try:
        return service.delete_source(source_id, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ===========================================================================
# Access credentials centre
# ===========================================================================
# No endpoint here returns a secret value. Requests may *supply* one, which is
# written to the OS credential store and reduced to a pointer.


class CredentialIn(BaseModel):
    name: str
    connector: str
    account: str
    scope: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    note: str = ""
    # Supply exactly one of these.
    secret_ref: str = ""
    secret_value: str = ""


@app.get("/api/credentials/connectors")
def credential_connectors() -> dict[str, Any]:
    """What can be connected to, and which of those are actually implemented."""

    return {"connectors": service.connector_catalogue()}


@app.get("/api/credentials")
def list_credentials() -> dict[str, Any]:
    return {"credentials": service.list_credentials()}


@app.post("/api/credentials", status_code=201)
def add_credential(
    body: CredentialIn,
    actor: str = Depends(require_capability("manage_credentials")),
) -> dict[str, Any]:
    try:
        return service.add_credential(
            name=body.name,
            connector=body.connector,
            account=body.account,
            scope=body.scope,
            permissions=body.permissions,
            note=body.note,
            actor=actor,
            secret_ref=body.secret_ref,
            secret_value=body.secret_value,
        )
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/credentials/{credential_id}/test")
def test_credential(
    credential_id: str,
    actor: str = Depends(require_capability("manage_credentials")),
) -> dict[str, Any]:
    """Verify the pointer resolves, and the login too where a connector exists."""

    try:
        return service.test_credential(credential_id, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/credentials/{credential_id}")
def toggle_credential(
    credential_id: str,
    body: EnabledIn,
    actor: str = Depends(require_capability("manage_credentials")),
) -> dict[str, Any]:
    try:
        return service.set_credential_enabled(
            credential_id, body.enabled, actor=actor
        )
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/credentials/{credential_id}")
def delete_credential(
    credential_id: str,
    actor: str = Depends(require_capability("manage_credentials")),
) -> dict[str, Any]:
    try:
        return service.delete_credential(credential_id, actor=actor)
    except DomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ===========================================================================
# Authentication
# ===========================================================================
# Login establishes a verified identity. It does NOT hand the agent that
# identity's permissions -- the agent always acts through the scoped service
# accounts in the credentials centre. Login decides who may *approve*; it never
# lets the agent act *as* the person.

import secrets as _secrets  # noqa: E402


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,          # not readable by JS -> no XSS token theft
        samesite="lax",
        secure=os.getenv("LOKI_COOKIE_SECURE", "0") not in ("0", "false", ""),
        path="/",
    )


class DevLoginIn(BaseModel):
    email: str
    role: str = "operator"
    name: str = ""


@app.get("/api/auth/status")
def auth_status() -> dict[str, Any]:
    """How to sign in, and whether auth is enforced. Used by the login screen."""

    s = auth.status(_sessions)
    s["enforced"] = _ENFORCE
    return s


@app.get("/api/auth/me")
def auth_me(identity: "auth.Identity | None" = Depends(current_identity)) -> dict[str, Any]:
    if identity is None:
        return {"authenticated": False}
    return {"authenticated": True, **identity.to_public()}


@app.post("/api/auth/dev-login")
def dev_login(body: DevLoginIn, response: Response) -> dict[str, Any]:
    """Local fallback login. Available only when Entra is not configured."""

    if not auth.dev_login_allowed():
        raise HTTPException(
            status_code=403,
            detail="dev login is disabled; sign in with your work account",
        )

    email = body.email.strip()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="enter a valid email address")
    if body.role not in auth.ROLE_RANK:
        raise HTTPException(status_code=422, detail=f"unknown role '{body.role}'")

    identity = auth.Identity(
        email=email,
        name=body.name.strip() or email.split("@")[0],
        role=body.role,
        method="dev",
    )
    token = _sessions.issue(identity)
    _set_session_cookie(response, token)

    service.store.append_audit(
        f"auth:{email}", "auth.dev_login", email,
        {"role": body.role, "method": "dev"},
    )
    return {"authenticated": True, **identity.to_public()}


@app.get("/api/auth/login")
def auth_login(response: Response) -> dict[str, Any]:
    """Begin Entra sign-in. Returns the Microsoft URL for the client to visit."""

    cfg = auth.load_entra()
    if not cfg.configured:
        raise HTTPException(
            status_code=400,
            detail="Entra sign-in is not configured on this server",
        )

    state = _secrets.token_urlsafe(24)
    nonce = _secrets.token_urlsafe(24)
    # Carry state+nonce in a short-lived signed cookie so the callback can
    # verify the round trip without server-side session storage.
    txn = _sessions._serializer.dumps({"state": state, "nonce": nonce})
    response.set_cookie(
        "loki_auth_txn", txn, max_age=600, httponly=True, samesite="lax",
        secure=os.getenv("LOKI_COOKIE_SECURE", "0") not in ("0", "false", ""),
        path="/",
    )
    return {"authorize_url": cfg.authorize_url(state, nonce)}


@app.get("/api/auth/callback")
def auth_callback(
    code: str,
    state: str,
    response: Response,
    loki_auth_txn: str | None = Cookie(default=None),
) -> Any:
    """Entra redirects here with a code. Exchange it and start a session."""

    from fastapi.responses import RedirectResponse

    cfg = auth.load_entra()
    try:
        txn = _sessions._serializer.loads(loki_auth_txn or "", max_age=600)
    except Exception:
        raise HTTPException(status_code=400, detail="sign-in expired; try again") from None

    if state != txn.get("state"):
        raise HTTPException(status_code=400, detail="sign-in state mismatch")

    try:
        identity = auth.exchange_code_for_identity(cfg, code, txn.get("nonce", ""))
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    token = _sessions.issue(identity)
    # Land the user back on the app with the session set.
    redirect = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(redirect, token)
    redirect.delete_cookie("loki_auth_txn", path="/")

    service.store.append_audit(
        f"auth:{identity.email}", "auth.login", identity.email,
        {"role": identity.role, "method": "entra"},
    )
    return redirect


@app.post("/api/auth/logout")
def auth_logout(
    response: Response,
    identity: "auth.Identity | None" = Depends(current_identity),
) -> dict[str, Any]:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    if identity is not None:
        service.store.append_audit(
            f"auth:{identity.email}", "auth.logout", identity.email, {}
        )
    return {"authenticated": False}
