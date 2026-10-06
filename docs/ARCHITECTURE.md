# LOKI — Architecture

_Last updated: 2026-10-06_

LOKI is a **human-in-the-loop ticket-automation platform**. It pulls real
ServiceNow tickets, matches them to codified procedures, and runs a governed
**propose → approve → execute → audit** lifecycle in which the agent only ever
performs bounded, approved, low-risk steps and a human owns everything
irreversible.

This document describes what exists today. It is explicit about three states:

- ✅ **Real & tested** — built, covered by the test suite, exercised.
- 🟡 **Built but dormant** — code exists and is tested, but off by default and
  not yet exercised against a real external system.
- 🔴 **Scaffolded, not deployed** — written (e.g. CDK) but nothing is live.

---

## The one principle everything serves

**The agent is granted work at the _step_ level, never the _ticket_ level.** A
ticket moves back and forth between agent and human; neither owns the whole
thing. Every control below exists to keep that promise.

---

## The three systems

### System 1 — Discovery (`src/`, offline) ✅
Pulls ServiceNow exports into DuckDB and scores which repetitive work is worth
automating. Read-only, offline analytics. Operational scripts live in
`scripts/`:
- `pull_closed_incidents.py` — fetch closed incidents with resolution content.
- `mine_resolutions.py` — cluster substantive resolutions into candidate
  procedures (`--exclude-group` to drop noisy auto-ack feeds).

Finding of record: of 5,000 closed incidents, ~10% carried a substantive
resolution; the realistic yield is a handful of codifiable procedures, not
thousands.

### System 2 — Procedure engine (`system2/`) ✅
The domain-agnostic rulebook. Matches a ticket to a procedure (keyword scoring)
and renders/simulates its steps. Knowledge lives in `procedures.json`.
- `engine.py` — keyword match + parameter extraction (hostname, instance_id,
  queue_name, service, username) from alert text.
- `executor.py` — render / simulate. Live execution is hard-disabled here.

### System 3 — Task overlay (`overlay/`) — the governed runtime ✅
The FastAPI service + React UI that runs the lifecycle. This is "LOKI".

---

## Overlay module map

| Module | Role | State |
|---|---|---|
| `api.py` | FastAPI transport; the only contract clients speak | ✅ |
| `service.py` | Application authority — every policy decision | ✅ |
| `domain.py` | Pure model: runs, steps, chunks, approvals, audit | ✅ |
| `risk.py` | Default-deny risk classification (R0–RX) | ✅ |
| `planner.py` | Ticket + procedure → approvable plan + binding digest | ✅ |
| `store.py` / `postgres_store.py` | Repository: runs, approvals, idempotency ledger, audit | ✅ (SQLite); 🟡 (Postgres) |
| `action_catalog.py` | Immutable, versioned action allowlist | ✅ |
| `connectors.py` | Approval-bound envelope + result contract | ✅ |
| `action_gateway.py` | Default-deny dispatch; simulation + scoped live mode | ✅ |
| `connectors_servicenow.py` | First real connector (work notes) | 🟡 dormant |
| `efficiency.py` | Advisory resolution-time + automatability ranking | ✅ |
| `semantic_engine.py` | Advisory meaning-based matching | ✅ |
| `knowledge_base.py` | Retrieval of solved-ticket grounding (RAG) | ✅ |
| `knowledge_llm.py` | LLM backend seam (Bedrock + offline echo) | ✅ |
| `auth.py` | Entra OIDC token verification + sessions | ✅ (enforcement off by default) |
| `live.py` | Read-only ServiceNow polling | ✅ |
| `worker.py` + `worker_*.py` | Background SQS→execute→Step Functions worker | 🟡 built, not default path |
| `ui/` | React app (Queue / Knowledge / Live + advisory panels) | ✅ |

---

## The request lifecycle

```
ticket ──► service.propose ──► planner.build_plan ──► store (run, state=awaiting_approval)
                                   │
                                   ├─ risk.classify every step (default-deny)
                                   └─ group into approval chunks + binding digest

human ──► service.decide_chunk (approve) ──► store (Approval, bound to a digest)

human ──► service.execute_chunk ──► guards: active approval? digest match? in order?
                                              separation of duties? not expired?
                                   │
                                   ├─ build_envelope (idempotency key)
                                   ├─ store.claim_action_execution (claim-before-dispatch)
                                   └─ action_gateway.dispatch ──► connector (simulation|live)
                                              │
                                              └─ store.complete/fail + audit event
```

Every transition writes an **append-only audit event** distinguishing
`actor=agent`, `triggered_by`, and `approved_by`.

---

## Safety model (all server-side, all tested)

- **Default-deny risk.** Every step is R0/R1/R2/R3/RX. The agent may run only
  R0/R1/R2 it was explicitly approved for. R3/RX are human-only. `run_command`
  is deliberately human-only (a read-only and a destructive command share the
  action, so the action alone cannot justify execution).
- **Two-layer enforcement.** `risk.py` classifies and `action_gateway.py`
  independently refuses anything not on the versioned allowlist. A step passes
  both or does not execute.
- **Approval binding.** An approval is bound by digest to exactly what was
  shown (procedure version, steps, rendered text, parameters, action version,
  connector operation). Editing a parameter invalidates it.
- **Idempotency.** A durable key is claimed before dispatch; a redelivered or
  replayed execution returns the stored outcome instead of running twice.
- **Separation of duties.** The approver of a chunk cannot also trigger it
  (`LOKI_ENFORCE_SOD`, on by default).
- **Approval expiry.** Stale approvals are refused (`LOKI_APPROVAL_TTL_SECONDS`).
- **Simulation is the floor.** The gateway refuses to construct in any mode but
  `simulation` unless `live` is deliberately selected, and a real connector
  still needs an allowlisted binding plus its own write switch.

---

## Advisory intelligence (recommends, never acts)

A deliberate boundary: these features **enrich and recommend** but never
approve, execute, reorder, or skip. The keyword matcher stays the authoritative
pick; a human stays the approver.

| Feature | Endpoint | What it does |
|---|---|---|
| Semantic suggestions | `POST /api/suggest` | Meaning-based procedure hints when keyword match misses |
| Efficiency ranking | on `POST /api/match` | Ranks procedures by historical resolution time + automatability |
| Plan efficiency advice | `GET /api/runs/{id}/efficiency-advice` | Per-step agent/human annotation + handoff point; `mutates_plan` always false |
| LLM step recommendation | `POST /api/recommend-steps` | Claude (Opus 5 on Bedrock) synthesises a critiqued, cited recommendation grounded in real solved tickets (RAG) |

### LLM knowledge base (RAG)

```
ticket ──► knowledge_base retrieval (REAL grounding, no model)
             ├─ sanctioned matched procedure
             ├─ candidate procedures
             └─ most similar SOLVED tickets (real close_notes/work_notes)
                          │
                          ▼
             knowledge_llm backend (advisory)
             ├─ BedrockBackend  → Claude via Converse API (model id = config)
             └─ EchoBackend     → offline; returns grounding, no fabricated plan
                          │
                          ▼
             cited, critiqued recommendation  ── a human reads it
```

Retrieval is separated from synthesis so the grounding is inspectable and the
model can never fabricate its evidence. Off by default (`LOKI_LLM_ENABLED=0`);
it never makes a paid/network call unprompted. Verified live against
`eu.anthropic.claude-opus-5` in eu-west-1.

---

## Cloud architecture (`infra/`, CDK) 🔴 not deployed

Three stacks: `Loki-dev-Network`, `Loki-dev-Data`, `Loki-dev-Platform`.

```
            internal ALB (TLS)
                   │
            ECS Fargate: API task  ──┐
            ECS Fargate: worker task ─┤─► Aurora PostgreSQL (via RDS Proxy)
                   │                  │
            Step Functions ◄──► SQS queues (per connector)
                   │
            Prometheus / Grafana / CloudWatch
```

- Synthesizes cleanly; **nothing deployed**.
- The application service is gated OFF (`loki:deployService=false`) and
  single-task until multi-write transactions + claim recovery are finished.
- **Shared knowledge for every user** is inherent once deployed on the shared
  Aurora store (today it runs on per-machine SQLite).
- Blocked by: a broken shared `CDKToolkit` bootstrap in the target account, and
  the account/isolation decision — both org actions, not code.

---

## Configuration (key toggles)

| Variable | Default | Purpose |
|---|---|---|
| `LOKI_EXECUTION_MODE` | `simulation` | `simulation` or `live`; anything else fails at startup |
| `LOKI_STORE_BACKEND` | `sqlite` | `sqlite` local, `postgres` production |
| `LOKI_ENFORCE_AUTH` | `0` | Enforce Entra session on write routes |
| `LOKI_ENFORCE_SOD` | `1` | Approver cannot also trigger |
| `LOKI_APPROVAL_TTL_SECONDS` | `3600` | Approval expiry |
| `LOKI_LLM_ENABLED` | `0` | Enable the Bedrock advisory model |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | Advisory model (inference profile id) |
| `LOKI_SERVICENOW_WRITE_ENABLED` | `0` | Second switch for the real work-note connector |

---

## Honest status line

- **Functionally**: rich and tested — full lifecycle, governance, advisory AI
  live against Opus 5.
- **Operationally**: not yet deployed; auth enforcement off; running on SQLite
  locally; one real connector built but never run against a real system.
- **The gap to company use** is operational scaffolding (deploy, enforced auth
  wired to Entra, a real connector against a sandbox, secrets management,
  observability) — most of which needs organizational inputs, not more code.
