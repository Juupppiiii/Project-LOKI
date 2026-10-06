# Project LOKI — repository map

Human-in-the-loop ticket automation. **Execution is simulation only** — nothing
in this repo writes to or acts on a real target system.

This file is the map: it tells you which folder is for what, so you can find the
right place without opening everything. Each area has its own deeper README
where noted.

---

## The quickest answer: "which folder is which?"

| Folder / file | Concern | In one line |
|---|---|---|
| `deploy/` | **Deployment runbook** | Single entry point for the AWS deploy: steps, blockers, config. Points at the files below. |
| `infra/` | **AWS / ECS infrastructure (CDK)** | All the cloud: ECS Fargate service, ALB, SQS, Step Functions, RDS, networking. TypeScript CDK. |
| `Dockerfile`, `.dockerignore` | **Container build** | How the app is packaged into the image ECS runs. |
| `overlay/` | **The running application** | The FastAPI service + React UI. This is the thing the container runs and ECS hosts. |
| `system2/` | **Procedure engine** | The rulebook: match a ticket to a procedure, render/simulate its steps. |
| `src/` | **Discovery pipeline (offline)** | Pulls ServiceNow data and finds automation candidates. Analytics, not the runtime. |
| `tests/` | **Test suite** | pytest for `overlay/` + `system2/`; CDK template tests live under `infra/test/`. |
| `data/` | **Input data** | ServiceNow CSV exports the app and pipeline read. |
| `output/` | **Generated analysis** | Reports/PDFs produced by the discovery pipeline and doc builders. |
| `sample_data/` | **Example input** | A small `incidents.csv` for trying things without the full export. |
| `knowledge/` | **Knowledge base** | All knowledge artifacts in one place: pulled tickets, handbooks, dictionaries. |
| `scripts/` | **Operational / one-off scripts** | Run directly (migration, demo, doc builders, knowledge pull). See below. |
| `docs/` | **Project docs** | Progress notes and archived build history. |
| *(root `requirements*.txt`, `pytest.ini`)* | **Dependencies & test config** | The three dependency sets and pytest markers. |

> **Rule of thumb:** `infra/` is *where it runs*, `overlay/` is *what runs*,
> `system2/` is *the rules it runs*, `src/` is *the offline analysis that decided
> what was worth automating*.

---

## 1. AWS / ECS infrastructure — `infra/`

Everything cloud. The web/API tier runs on **ECS Fargate** (not Lambda) behind
an internal ALB; connector work is modelled with SQS + Step Functions.

```
infra/
  bin/            CDK app entry point
  lib/
    network-stack.ts    VPC, subnets, security groups
    data-stack.ts       Aurora PostgreSQL, RDS Proxy, secrets, KMS
    platform-stack.ts   ECS cluster + API Fargate service + simulation worker, ALB, ECR, SQS, Step Functions, Prometheus, dashboards
  test/           CDK template assertions (node --test)
  cdk.json        CDK config
```

The ECS application service is **gated off by default** (`loki:deployService`)
until the remaining multi-write transaction and claim-recovery work lands. See
`infra/README.md`.

## 2. Container build — `Dockerfile` + `.dockerignore`

The image ECS runs. Two-stage build: stage 1 builds the React UI from its
lockfile; stage 2 is a slim Python runtime that serves the API as a non-root
user on port 8800. `.dockerignore` keeps secrets and local state out of the
build context.

## 3. The running application — `overlay/`

The FastAPI service and the React UI it serves. This is the code inside the
container. Full detail (run steps, upload formats, auth, safety controls,
config) is in `overlay/README.md`. Highlights:

```
overlay/
  api.py            FastAPI transport (the contract every client speaks)
  service.py        Application layer — the authority on all rules
  domain.py         Pure model (no framework, DB, or transport)
  risk.py           Default-deny risk classification
  planner.py        Ticket + procedure -> approvable plan
  store.py          SQLite repository (local default)
  postgres_store.py PostgreSQL repository (production backend)
  action_gateway.py Default-deny simulation execution boundary
  connectors.py     Connector protocol (approval-bound envelope)
  worker.py         Background worker: consumes dispatched steps, reports to Step Functions
  worker_*.py       Worker transport seam (in-memory + SQS/Step Functions) and entrypoint
  auth.py           Entra OIDC sign-in + session cookies
  live.py           Read-only ServiceNow polling
  ui/               React app (its own README; build output in ui/dist)
```

## 4. Procedure engine — `system2/`

The domain-agnostic rulebook. Matches a ticket to a procedure and renders or
simulates its steps. Knowledge lives in `system2/procedures.json`. Layer C (real
execution) is hard-disabled. See the System 2 section of `PROGRESS.md`.

## 5. Discovery pipeline (offline) — `src/`

Not the runtime. Pulls ServiceNow data, loads it into DuckDB, and scores
automation candidates. `src/database.py` (open in your editor) is this: it loads
CSVs into DuckDB for analysis — read-only, offline, not a governed runtime store.
Run via `python src/main.py`. See the System 1 section of `docs/PROGRESS.md`.

## 6. Operational / one-off scripts — `scripts/`

One-off tooling, run directly. Each script resolves the project root from its
own location (`__file__`), so it works from any working directory — run them as
`python scripts/<name>.py`. The app-importing ones put the project root on
`sys.path` before importing `overlay`/`system2`.

| Script | What it does |
|---|---|
| `01_pull_closed_incidents.py` | Pull closed incidents from ServiceNow (read-only). |
| `02_build_knowledge_json.py` | Structure pulled tickets into JSON knowledge files. |
| `03_mine_resolutions.py` | Cluster recurring resolution patterns. |
| `04_autodraft_procedures.py` | Auto-draft procedures from patterns using Opus 5. |
| `05_compare_matchers.py` | Shadow-compare keyword vs semantic matchers. |
| `06_demo_agent_run.py` | End-to-end simulated agent run. |
| `migrate_sqlite_to_postgres.py` | Migrate SQLite to PostgreSQL (dry-run by default). |
| `08_build_architecture_pdf.py` | Generate the architecture PDF. |
| `09_build_architecture_diagram_pdf.py` | Generate the architecture diagram PDF. |
| `10_build_powerpoint.py` | Generate the PowerPoint overview deck. |

See `scripts/README.md` for the full guide and the pipeline order.

## 7. Deployment — `deploy/`

A runbook for deploying LOKI to AWS (ECS + Aurora + Bedrock). The actual files
(`Dockerfile`, `requirements-api.txt`, `infra/`) stay in their functional
locations (Docker/CDK require it), but `deploy/README.md` is the single entry
point: deployment steps, blockers, and runtime configuration. See also
`infra/README.md` for CDK-specific detail.

## 8. Docs — `docs/`

| File | Purpose |
|---|---|
| `docs/PROGRESS.md` | Living status of all three systems and findings. |
| `docs/Phase1_Conversation_Archive.md` | Archived build history. |

## 9. Dependencies & test config (repository root)

| File | Purpose |
|---|---|
| `requirements.txt` | Discovery pipeline (`src/`) dependencies. |
| `requirements-api.txt` | Application (`overlay/`) runtime dependencies. |
| `requirements-dev.txt` | Test/lint/dev tooling. |
| `pytest.ini` | Test configuration and markers. |

## 10. Data & generated artifacts

| Folder | Tracked? | Contents |
|---|---|---|
| `data/` | git-ignored | ServiceNow CSV exports (input). |
| `output/` | git-ignored | Generated reports, PDFs, summaries. |
| `sample_data/` | tracked | Small example `incidents.csv`. |
| `*.duckdb` | git-ignored | DuckDB analytics databases (regenerated). |

Local SQLite runtime databases (`overlay_tasks.db`, `overlay_demo.db`,
`smoke_test.db` and their `-wal`/`-shm` sidecars) are git-ignored scratch state
and are safe to delete — they are recreated on demand.

---

## Running the pieces

```powershell
# Application (API + UI) locally
python -m uvicorn overlay.api:app --host 127.0.0.1 --port 8800   # http://127.0.0.1:8800

# Discovery pipeline
python src/main.py

# Tests
python -m pytest -q

# Infrastructure (synthesize CloudFormation; nothing is deployed)
cd infra ; npx cdk synth
```
