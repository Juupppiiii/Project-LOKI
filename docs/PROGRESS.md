# Phase 1 Discovery Framework — Progress

_Last updated: 2026-09-24_

A DevOps Capability Enablement platform that pulls ServiceNow data, discovers
automation opportunities, and surfaces them via analysis + a dashboard.

Three systems, each building on the last:

| | System | Role | Status |
|---|--------|------|--------|
| 1 | Discovery | What work is repetitive and worth automating | Done |
| 2 | Procedure engine | How that work gets done (the rulebook) | Done |
| 3 | Task overlay | Who does which step, with human approval | Phase 2 done |

**Execution is simulation only** in all three. Ticket data can now be read live
from ServiceNow (read-only), which does not change that: nothing in this repo
writes to, or acts on, a target system.

## Pipeline

```
SYSTEM 1 — discovery
  .env → config.py → extractor.py → data/*.csv → analyzer.py → output/*.csv
                                                     ↓
                                     database.py → automation_discovery.duckdb
                                                     ↓
                                               dashboard.py (Streamlit)

SYSTEM 2 — procedures (learned from System 1 resolutions)
  data/*.csv → draft_procedures.py → procedures_drafts.json
                                          ↓  (human review + promotion)
                                     procedures.json
                                          ↓
                              engine.py (match) → executor.py (render/simulate)

SYSTEM 3 — task overlay (who does which step)
  CSV tickets ─┐
  live.py  ────┤ (read-only ServiceNow poll)
               ↓
  registry.py (built-in + published procedures, + dictionary aliases)
               ↓
  engine match → planner.py (risk + chunks) → store.py (SQLite)
               ↓
  api.py  ←→  web/ (browser) or any future client
               ↓
  approval → simulator.py → audit trail → human handoff

  uploads: knowledge.py → drafts → [human publishes] → registry
```

## Components

| File | Role | Status |
|------|------|--------|
| `config.py` | Single source of truth: creds, tuning, per-table fields + queries | Done |
| `extractor.py` | Paginated, retrying ServiceNow extractor | Done |
| `main.py` | Stage orchestrator (`--only`, `--skip-*` flags) | Done |
| `analyzer.py` | Summary, tasks, groups, scoring, resolution-text mining | Done |
| `database.py` | Loads CSVs into DuckDB | Done |
| `dashboard.py` | Streamlit view of automation candidates | Done (basic) |

## Key decisions & findings

1. **Credentials fixed** — `.env` was misnamed (`gg .env`) and used
   `SERVICENOW_*` names while code read `SN_*`. Config now accepts both;
   login is automatic (no prompt).
2. **"Manual tickets" = `contact_type != Automated`.** Of ~604k 3-month
   incidents, only **11,603** are human-raised (Email, Phone, IM, Walk-in,
   Self-service, Virtual Agent). The rest are the `svcgbsnsoap` / GATE Import
   machine feed.
3. **Pagination bug fixed** — extractor used to stop on a short-but-non-empty
   page (caused 4,309 / 27,069 truncations). Now pages against the
   authoritative stats count and stops only when complete or truly empty.
4. **Resolution content lives in `close_notes`** (~70% filled) and
   `work_notes` — NOT in `resolution_code` / `actions_taken` / `cause`
   (all empty on this instance).

## Data currently extracted (3-month window)

| Table | Rows |
|-------|------|
| incidents (manual only) | 11,603 |
| request_items | 7,824 |
| requests | 7,519 |
| changes | 4,649 |
| catalog_tasks | 3,432 |
| change_tasks | 2,969 |
| problem_tasks | 203 |
| problems | 117 |

## Outputs generated (`output/`)

- `dataset_summary.csv`
- `top_tasks.csv`
- `top_assignment_groups.csv`
- `top_automation_opportunities.csv`
- `resolution_summaries.csv` (how manual incidents were fixed)
- `resolution_keywords.csv`
- `automation_candidates.csv` (dashboard view)

## How to run

```powershell
python src/main.py                 # full: extract → analyze → load
python src/main.py --skip-extract  # re-analyze existing CSVs
python src/main.py --only extract  # single stage
streamlit run src/dashboard.py     # dashboard
```

## Open / next steps (System 1)

- [ ] Semantic grouping of near-duplicate resolutions (e.g. "reset password"
      vs "password reset")
- [ ] Add a resolution-patterns view to the dashboard
- [ ] Decide whether other tables need a "manual" filter (signal differs per table)
- [ ] Clean up diagnostic scripts (preflight, counts, test_extract,
      discover_manual, distribution, check_resolution_fields)
- [ ] Rotate the ServiceNow password (it was pasted in chat)

---

# System 2 — Universal Execution Engine (SIMULATION ONLY)

Reads structured *procedures* (the rules gathered from System 1 resolutions),
matches a ticket to one, and runs it in a safe mode. The engine is
domain-agnostic; all knowledge lives in `system2/procedures.json`.

**No live execution.** No connectors, no credentials, no network calls.
`LIVE_EXECUTION_ENABLED = False` and `live_execute()` raises.

## Files (`system2/`)

| File | Role |
|------|------|
| `schema.py` | Procedure / Step / Parameter / Trigger model + validating loader |
| `procedures.json` | The rule set (6 Wintel procedures) |
| `engine.py` | Match ticket → procedure; extract parameters |
| `executor.py` | Layer A runbook, Layer B dry-run; Layer C hard-disabled |
| `run_engine.py` | CLI: `--list`, `--desc/--ci`, `--sample N`, `--mode` |
| `draft_procedures.py` | Auto-draft new procedures from resolution clusters |
| `procedures_drafts.json` | Draft procedures awaiting human review (not loaded) |

## Execution layers

- **A. runbook** — render an ordered, parameter-filled checklist for a human.
- **B. dry_run** — simulate each step with mock outcomes. Nothing real touched.
- **C. live** — DISABLED. Requires connectors + approval gate + rollback; a
  separate, deliberate future project.

## Accuracy (against 907 real Wintel incidents)

- Triggers match only the PROBLEM text, never resolution notes (this fixed
  massive over-matching). Minimum score threshold `SYS2_MIN_SCORE=3`.
- 6 procedures → **97/907 matched**, all high-confidence, ~86% params resolved.
- Coverage grows as more procedures are promoted from drafts.

## Procedures so far

`disk_cleanup`, `server_reboot`, `service_restart`, `delete_old_profile`,
`vcenter_health_check`, `high_cpu` (promoted from auto-draft).

## How to run

```powershell
cd system2
python run_engine.py --list
python run_engine.py --desc "TPVOL ldn2ws9696 C Disk Space" --ci ldn2ws9696
python run_engine.py --sample 5 --mode runbook
python draft_procedures.py --min-frequency 5   # generate new drafts
```

## Open / next steps (System 2)

- [ ] Review + promote remaining drafts (login_access, high_memory_swap);
      narrow packet_loss / hardware_crit keywords first
- [ ] Add procedures for groups beyond Wintel
- [ ] Small review UI for drafts
- [ ] Layer C (real connectors + guardrails) — only as a separate project
```

---

# System 3 — Task Overlay (SIMULATION ONLY)

The human-in-the-loop layer. System 2 could say *"this ticket matches disk
cleanup, here are the 7 steps"* but had no state, no risk model, no approval and
no memory. The overlay adds exactly that.

**Core principle: the agent is granted work at the _step_ level, never at the
ticket level.** A ticket moves back and forth between agent and human, and
neither owns the whole thing.

## Files (`overlay/`)

| File | Role |
|------|------|
| `domain.py` | Pure model: risk classes, states, `TaskRun`, `StepRun`, `Chunk`, `Approval`, `AuditEvent`. No framework, no DB, no transport. |
| `risk.py` | Classification policy. Default-deny. |
| `planner.py` | Ticket + procedure → approvable plan; chunking; binding digest |
| `store.py` | SQLite behind a repository seam (the only module that knows SQL) |
| `simulator.py` | Simulated step execution; where connectors will eventually plug in |
| `service.py` | Every use case; the authority on all rules |
| `api.py` | FastAPI; the contract every client speaks |
| `knowledge.py` | Parses uploaded handbooks/dictionaries into reviewable drafts |
| `registry.py` | The effective rule set: built-in + published, plus dictionary aliases |
| `live.py` | Read-only ServiceNow polling and ingest |
| `web/` | Thin browser client (Queue / Knowledge / Live tabs) — holds no business logic |
| `examples/` | Sample handbooks and a dictionary that upload as-is |
| `_system2.py` | The single place the overlay reaches into System 2 |
| `README.md` | Run steps, upload formats, live setup, safety controls, limitations |

## Risk model (default-deny)

| Class | Meaning | Agent may run |
|-------|---------|---------------|
| `R0` | Read-only observation | Yes, with chunk approval |
| `R1` | Low impact, additive/reversible | Yes, with chunk approval |
| `R2` | Real operational change | Yes, with its **own** step approval |
| `R3` | High impact or destructive | No — human only |
| `RX` | Unclassified | No — human only |

`run_command` maps to `RX` deliberately: a read-only `Get-PSDrive` and a
destructive `Remove-Item` are the same action, so the action alone cannot
justify execution. Anything unclassified stays with a human.

This is why `wintel.disk_cleanup` splits itself into:

- steps 1–2 → agent (`connect`, `check`, R0)
- steps 3–5 → **human** (`run_command`, RX)
- steps 6–7 → agent (`check`, `notify`, R0/R1)

That is the "AI does a bounded chunk, human takes the complex part" model
working in practice.

## Safety controls (all enforced server-side, all tested)

| Control | Behaviour |
|---------|-----------|
| No execution without approval | Refused; the attempt is audited |
| Human-owned chunks can't be approved for the agent | Refused |
| Approval binds to what was shown | Digest over procedure version, steps, rendered text, parameters |
| Editing parameters invalidates approval | Digest changes → re-approval required |
| One active approval per chunk | Prior approvals superseded on a new decision |
| Steps run in order | Refuses to jump ahead of outstanding work |
| `R2` needs individual approval | Cannot hide inside a low-risk chunk |
| `R3`/`RX` never agent-executed | Refused by policy, again by the simulator |
| Unresolved `<param?>` blocks the step | Hands off to a human |
| Manual completion requires evidence | Empty evidence refused |
| Audit is append-only | No update or delete path |
| No accidental ticket forks | A second run for an in-progress ticket is refused (`409 duplicate_run`) naming the existing run; `?allow_duplicate=true` overrides deliberately |

Audit distinguishes three things: `actor=agent` (what acted), `triggered_by`
(who clicked), `approved_by` (who authorised). A deliberate duplicate is
recorded on **both** runs, so either audit trail leads to the other.

## Data-format finding (affects System 1 and 2, not just the overlay)

`data/incidents.csv` stores ServiceNow **reference fields as objects**, not
names:

```
assignment_group = @{display_value=Global Wintel Server Support; link=https://.../sys_user_group/19e...}
```

Matching compares plain names, so `group_match` never fired. With
`MIN_SCORE=3` (two keywords, or one keyword plus a group match) this silently
discarded valid tickets. Measured on the current extract: **0 matches before
flattening, 10 after.** The 10/88 Wintel hit rate (~11%) lines up with the
97/907 (~11%) recorded for System 2, so the two are consistent once the field is
flattened.

`overlay/service.py::normalize_ticket()` flattens these on the way in. Note the
files in `output/` contain clean group names, so the extract that produced the
analysis had a different field format than the CSV now on disk — worth
confirming which extractor settings are current before re-running analysis.

Also fixed: the CSV reader hit `_csv.Error: field larger than field limit
(131072)` because `work_notes` can exceed Python's default cap. The limit is now
raised at import.

## Bug found and fixed during build

Timestamps store only to the second, so two approvals created in the same second
ordered non-deterministically — meaning a **stale approval could win and
authorise a plan the reviewer never saw**. Fixed by explicitly superseding prior
approvals on any new decision (`Store.supersede_approvals`), plus a `rowid`
tiebreaker.

## How to run

```powershell
python -m uvicorn overlay.api:app --host 127.0.0.1 --port 8800
# then open http://127.0.0.1:8800   (API docs at /docs)
```

## Staying convertible to a desktop app

The browser client holds no logic — it renders API responses and calls back, and
even fetches the risk table from `/api/risk-policy` rather than hardcoding it.
Wrapping it as a desktop app (Tauri/Electron) needs one line:

```html
<script>window.OVERLAY_API_BASE = "https://overlay.internal.example.com";</script>
```

plus allowing that origin via `OVERLAY_ALLOWED_ORIGINS`. No build step, no
bundler. What must **never** move into a desktop client: credentials, approval
or risk decisions, run state, the audit log.

## Verified

9/9 endpoint and control checks pass. A full ticket lifecycle reaches
`completed` 7/7 steps. An unmatched ticket is refused with 422 and stays with a
human. Connectors disabled; `system2/executor.live_execute` still raises.

## Phase 2: customisation + live data

Two additions, each with its own safety boundary.

**Uploadable knowledge.** Handbooks (`.json`, `.yaml`, `.csv`, `.md`) and
dictionaries (`.csv`, `.json`, `.yaml`, `.txt`) can be uploaded through the UI.

- A handbook produces **drafts**. Drafts are never loaded by the matcher.
  Publishing is the human gate and the only path into the live rule set.
- The review screen shows, per step, the risk class and owner that publishing
  would produce — so the reviewer approves a plan, not a filename.
- A file may *declare* a low risk to try to get a step automated. When the action
  is one whose safety cannot be verified (`run_command`, `reboot`,
  `delete_profile`) that claim is flagged as a warning before publishing.
- A **dictionary** maps a canonical term to real-world phrasings and takes effect
  immediately, because it can only widen *matching* — never permission.
  Measured: with the example dictionary, *"User cannot login, account is locked
  out"* matches the unlock procedure at score 7.0; without it, nothing matches.

**Live ticket feed (read-only).** Polls ServiceNow into an inbox.

- `_get()` is the only network call in `live.py` and issues **GET only**. No code
  path writes to ServiceNow.
- Reading live data does **not** enable live execution. Runs still need approval
  and steps are still simulated.
- Credentials come from the environment, never the database, and the API reports
  only whether they are present.
- Dedupes on `sys_id`, so re-polling never duplicates a ticket or resets a triage
  decision.
- Default query is `active=true^u_record_type=incident^contact_type!=Automated`.
  The `u_record_type` clause is required on this instance: several record types
  share the incident table, and without it the feed also returns `RQ` records
  (found during testing).

Verified against the live instance: 31/31 API checks pass, including
upload → draft → *not active* → publish → matches, an uploaded procedure still
refusing execution without approval, and a real poll of 50 current tickets.

## Open / next steps (System 3)

- [ ] **Auth.** `X-Actor` is a header the client asserts — it is NOT
      authentication. Needs SSO-derived identity + server-side roles before this
      is exposed beyond a trusted network. This now also gates *publishing*:
      anyone who can reach the API can publish a procedure.
- [ ] **Separation of duties.** Nothing yet stops one person approving and
      triggering their own work, or uploading and publishing their own handbook
      (the audit records both).
- [ ] Live polling is a recent-window read, not a backfill. A ticket updated
      while the feed was off is only picked up if still in the recent window.
- [ ] **Approval expiry.** Approvals currently never time out.
- [ ] Classify safe `run_command` steps explicitly (e.g. `"risk": "R0"` on a
      read-only `Get-PSDrive`) to grow agent coverage — a `procedures.json`
      edit, no code change.
- [ ] Background worker, so long-running/scheduled steps (a decommission soak
      period) are possible. Execution currently happens inside the request.
- [ ] Transactional server DB + leases for multi-team use (SQLite today).
- [ ] Rollback execution — the `rollback` field is loaded but never run.
- [ ] Layer C connectors behind the approval gate — still a separate,
      deliberate project.
