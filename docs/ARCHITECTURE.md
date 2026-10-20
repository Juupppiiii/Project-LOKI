# BUCKY â€” Architecture

_Last updated: 2026-10-06_

BUCKY is a **human-in-the-loop ticket-automation platform**. It pulls real
ServiceNow tickets, matches them to codified procedures, and runs a governed
**propose â†’ approve â†’ execute â†’ audit** lifecycle in which the agent only ever
performs bounded, approved, low-risk steps and a human owns everything
irreversible.

This document describes what exists today. It is explicit about three states:

- âœ… **Real & tested** â€” built, covered by the test suite, exercised.
- ðŸŸ¡ **Built but dormant** â€” code exists and is tested, but off by default and
  not yet exercised against a real external system.
- ðŸ”´ **Scaffolded, not deployed** â€” written (e.g. CDK) but nothing is live.

---

## The one principle everything serves

**The agent is granted work at the _step_ level, never the _ticket_ level.** A
ticket moves back and forth between agent and human; neither owns the whole
thing. Every control below exists to keep that promise.

---

## The three systems

### System 1 â€” Discovery (`src/`, offline) âœ…
Pulls ServiceNow exports into DuckDB and scores which repetitive work is worth
automating. Read-only, offline analytics. Operational scripts live in
`scripts/`:
- `pull_closed_incidents.py` â€” fetch closed incidents with resolution content.
- `mine_resolutions.py` â€” cluster substantive resolutions into candidate
  procedures (`--exclude-group` to drop noisy auto-ack feeds).

Finding of record: of 5,000 closed incidents, ~10% carried a substantive
resolution; the realistic yield is a handful of codifiable procedures, not
thousands.

### System 2 â€” Procedure engine (`system2/`) âœ…
The domain-agnostic rulebook. Matches a ticket to a procedure (keyword scoring)
and renders/simulates its steps. Knowledge lives in `procedures.json`.
- `engine.py` â€” keyword match + parameter extraction (hostname, instance_id,
  queue_name, service, username) from alert text.
- `executor.py` â€” render / simulate. Live execution is hard-disabled here.

### System 3 â€” Task overlay (`overlay/`) â€” the governed runtime âœ…
The FastAPI service + React UI that runs the lifecycle. This is "BUCKY".

---

## Overlay module map

| Module | Role | State |
|---|---|---|
| `api.py` | FastAPI transport; the only contract clients speak | âœ… |
| `service.py` | Application authority â€” every policy decision | âœ… |
| `domain.py` | Pure model: runs, steps, chunks, approvals, audit | âœ… |
| `risk.py` | Default-deny risk classification (R0â€“RX) | âœ… |
| `planner.py` | Ticket + procedure â†’ approvable plan + binding digest | âœ… |
| `store.py` / `postgres_store.py` | Repository: runs, approvals, idempotency ledger, audit | âœ… (SQLite); ðŸŸ¡ (Postgres) |
| `action_catalog.py` | Immutable, versioned action allowlist | âœ… |
| `connectors.py` | Approval-bound envelope + result contract | âœ… |
| `action_gateway.py` | Default-deny dispatch; simulation + scoped live mode | âœ… |
| `connectors_servicenow.py` | First real connector (work notes) | ðŸŸ¡ dormant |
| `efficiency.py` | Advisory resolution-time + automatability ranking | âœ… |
| `semantic_engine.py` | Advisory meaning-based matching | âœ… |
| `knowledge_base.py` | Retrieval of solved-ticket grounding (RAG) | âœ… |
| `knowledge_llm.py` | LLM backend seam (Bedrock + offline echo) | âœ… |
| `auth.py` | Entra OIDC token verification + sessions | âœ… (enforcement off by default) |
| `live.py` | Read-only ServiceNow polling | âœ… |
| `worker.py` + `worker_*.py` | Background SQSâ†’executeâ†’Step Functions worker | ðŸŸ¡ built, not default path |
| `ui/` | React app (Queue / Knowledge / Live + advisory panels) | âœ… |

---

## The request lifecycle

```
ticket â”€â”€â–º service.propose â”€â”€â–º planner.build_plan â”€â”€â–º store (run, state=awaiting_approval)
                                   â”‚
                                   â”œâ”€ risk.classify every step (default-deny)
                                   â””â”€ group into approval chunks + binding digest

human â”€â”€â–º service.decide_chunk (approve) â”€â”€â–º store (Approval, bound to a digest)

human â”€â”€â–º service.execute_chunk â”€â”€â–º guards: active approval? digest match? in order?
                                              separation of duties? not expired?
                                   â”‚
                                   â”œâ”€ build_envelope (idempotency key)
                                   â”œâ”€ store.claim_action_execution (claim-before-dispatch)
                                   â””â”€ action_gateway.dispatch â”€â”€â–º connector (simulation|live)
                                              â”‚
                                              â””â”€ store.complete/fail + audit event
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
  (`BUCKY_ENFORCE_SOD`, on by default).
- **Approval expiry.** Stale approvals are refused (`BUCKY_APPROVAL_TTL_SECONDS`).
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
ticket â”€â”€â–º knowledge_base retrieval (REAL grounding, no model)
             â”œâ”€ sanctioned matched procedure
             â”œâ”€ candidate procedures
             â””â”€ most similar SOLVED tickets (real close_notes/work_notes)
                          â”‚
                          â–¼
             knowledge_llm backend (advisory)
             â”œâ”€ BedrockBackend  â†’ Claude via Converse API (model id = config)
             â””â”€ EchoBackend     â†’ offline; returns grounding, no fabricated plan
                          â”‚
                          â–¼
             cited, critiqued recommendation  â”€â”€ a human reads it
```

Retrieval is separated from synthesis so the grounding is inspectable and the
model can never fabricate its evidence. Off by default (`BUCKY_LLM_ENABLED=0`);
it never makes a paid/network call unprompted. Verified live against
`eu.anthropic.claude-opus-5` in eu-west-1.

---

## Cloud architecture (`infra/`, CDK) ðŸ”´ not deployed

Three stacks: `Bucky-dev-Network`, `Bucky-dev-Data`, `Bucky-dev-Platform`.

```
            internal ALB (TLS)
                   â”‚
            ECS Fargate: API task  â”€â”€â”
            ECS Fargate: worker task â”€â”¤â”€â–º Aurora PostgreSQL (via RDS Proxy)
                   â”‚                  â”‚
            Step Functions â—„â”€â”€â–º SQS queues (per connector)
                   â”‚
            Prometheus / Grafana / CloudWatch
```

- Synthesizes cleanly; **nothing deployed**.
- The application service is gated OFF (`bucky:deployService=false`) and
  single-task until multi-write transactions + claim recovery are finished.
- **Shared knowledge for every user** is inherent once deployed on the shared
  Aurora store (today it runs on per-machine SQLite).
- Blocked by: a broken shared `CDKToolkit` bootstrap in the target account, and
  the account/isolation decision â€” both org actions, not code.

---

## Configuration (key toggles)

| Variable | Default | Purpose |
|---|---|---|
| `BUCKY_EXECUTION_MODE` | `simulation` | `simulation` or `live`; anything else fails at startup |
| `BUCKY_STORE_BACKEND` | `sqlite` | `sqlite` local, `postgres` production |
| `BUCKY_ENFORCE_AUTH` | `0` | Enforce Entra session on write routes |
| `BUCKY_ENFORCE_SOD` | `1` | Approver cannot also trigger |
| `BUCKY_APPROVAL_TTL_SECONDS` | `3600` | Approval expiry |
| `BUCKY_LLM_ENABLED` | `0` | Enable the Bedrock advisory model |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | Advisory model (inference profile id) |
| `BUCKY_SERVICENOW_WRITE_ENABLED` | `0` | Second switch for the real work-note connector |

---

## Honest status line

- **Functionally**: rich and tested â€” full lifecycle, governance, advisory AI
  live against Opus 5.
- **Operationally**: not yet deployed; auth enforcement off; running on SQLite
  locally; one real connector built but never run against a real system.
- **The gap to company use** is operational scaffolding (deploy, enforced auth
  wired to Entra, a real connector against a sandbox, secrets management,
  observability) â€” most of which needs organizational inputs, not more code.
