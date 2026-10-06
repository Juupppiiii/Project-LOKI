# Task Overlay

Human-in-the-loop ticket automation. Sits on top of System 1 (discovery) and
System 2 (procedure matching) and adds what they lacked: persistent per-step
state, risk classification, chunk approval, resumable human handoff, and an
append-only audit trail.

Three things you can do:

| Tab | What it is for |
|---|---|
| **Queue** | Work a ticket step by step: approve a chunk, let the agent run it, take over at the complex steps |
| **Knowledge** | Upload handbooks and dictionaries to customise the rule set without editing code |
| **Live** | Pull real tickets from ServiceNow into an inbox |

**Execution is simulated by default.** Ticket data can be read live, which does
not change that. One real connector now exists — the ServiceNow work-note
connector — but it is **dormant unless two explicit switches are set**
(`LOKI_EXECUTION_MODE=live` and `LOKI_SERVICENOW_WRITE_ENABLED=1`); until then
nothing is written to any target system. See [Real connectors](#real-connectors).
`system2/executor.live_execute` is never imported.

## Run it

```powershell
# first time only: build the React front end
cd overlay/ui ; npm install ; npm run build ; cd ../..

python -m uvicorn overlay.api:app --host 127.0.0.1 --port 8800
```

Then open <http://127.0.0.1:8800>. API docs at `/docs`.

The UI is a React app (`overlay/ui/`) with a light/dark toggle. If the build is
missing, the server falls back to the original dependency-free client so the tool
still works; `GET /api/ui-info` says which one you are looking at, and `/legacy`
always serves the old one. See `overlay/ui/README.md` — including the note that
this network needs the internal Nexus registry rather than public npm.

Pick a ticket from the dropdown (read from `data/incidents.csv`), press
**Match & add to queue**, then approve and run the chunks the agent is allowed
to touch.

Want live tickets instead of the CSV? See [Live ticket feed](#live-ticket-feed).

## Customising the rule set (Knowledge tab)

Two kinds of upload, with deliberately different power.

### Handbooks become drafts, not behaviour

Uploading a handbook **never changes what the system does.** It creates drafts.
A human publishes a draft, and only then does it join the live rule set. That is
the same review gate System 2 already used for auto-drafted procedures.

Supported: `.json`, `.yaml`/`.yml`, `.csv`, `.md`

The review screen shows, per step, the risk class, who would own it, and why, so
you approve a concrete plan rather than a filename.

**Markdown** — one heading per procedure, a numbered list for steps:

```markdown
# Unlock a locked account

keywords: account locked, unlock account, locked out
group: Global Service Desk
params: username

## Steps

1. check | Confirm the account is locked | command: Search-ADAccount -LockedOut {username} | risk: R0
2. run_command | Unlock the account | command: Unlock-ADAccount {username}
3. notify | Tell the requester
```

Step syntax is `action | description | field: value`. Recognised fields:
`command`, `target`, `service`, `expect`, `risk`, `owner`.

**CSV** — one row per step, grouped by `procedure_id`:

```csv
procedure_id,name,assignment_group,keywords,parameters,step,action,description,command,risk
custom.unlock_gitlab,Unlock a GitLab account,Global IT Security IDAM,unlock gitlab|gitlab account,username,1,check,Confirm it is locked,Get-GitLabUser {username},R0
custom.unlock_gitlab,,,,,2,run_command,Unlock it,Unblock-GitLabUser {username},
```

**JSON/YAML** — the same shape as `system2/procedures.json`. Most exact, and the
only format with no parsing ambiguity.

Working examples you can upload right now are in `knowledge/handbooks/` (both
hand-written and auto-drafted procedures) with dictionary samples in
`knowledge/dictionaries/`.

Free-form prose is deliberately *not* parsed into steps. A paragraph cannot be
turned into an executable runbook reliably, so text that is not a recognised step
becomes procedure notes and the parser tells you what it could not read.

### Dictionaries change matching, never permission

A dictionary maps a canonical term to the ways people actually write it. When an
alias appears in a ticket, the canonical term is added to the text the matcher
sees. It takes effect immediately, because the worst it can do is match a ticket
to a procedure a human still has to approve.

Supported: `.csv`, `.json`, `.yaml`/`.yml`, `.txt`

```csv
canonical,aliases,note
account locked,locked out|cannot login|can't log in,Service Desk wording varies
disk space,disk full|low disk|disk capacity,Monitoring alerts phrase this many ways
```

Or plain text: `account locked = locked out, cannot login`

**Why this is worth doing.** With the example dictionary loaded, the ticket
*"User cannot login, account is locked out"* matches the unlock procedure at
score 7.0. Without it, "cannot login" is not a trigger keyword and the ticket
matches nothing.

Use the **test box** to paste a phrase and see the score, which aliases fired,
and — when it does not match — exactly how far short it fell and why. A single
keyword scores 2.0 against a threshold of 3.0, so a dictionary can genuinely
work and the ticket still not match; the tool says so instead of just reporting
failure.

### Uploads cannot grant themselves execution rights

A handbook may *declare* `"risk": "R0"` on a step. That claim is surfaced as a
warning on the review screen when the action is one whose safety cannot be
verified (`run_command`, `reboot`, `delete_profile`), because that is precisely
the route a careless or hostile file would take to get something destructive
automated. Publishing is a human decision, and the warning makes sure the human
sees the part that matters.

Anything unclassified stays `RX`, which is human-only.

## Live ticket feed

Off by default. Enable it on the **Live** tab.

### Setup

Credentials come from the environment (the same ones System 1 uses), never from
the database and never from the UI:

```powershell
$env:SERVICENOW_INSTANCE = "yourinstance.service-now.com"
$env:SERVICENOW_USERNAME = "api_read_only"
$env:SERVICENOW_PASSWORD = "..."
```

`.env` in the project root works too. Then press **Test connection**, then
**Poll now**, or tick *Enable background polling*.

### What it does and does not do

| | |
|---|---|
| **Access** | Read-only. `_get()` is the only network call in `live.py` and it only issues HTTP GET against the Table API. There is no code path that writes to ServiceNow. |
| **Execution** | Still simulated. Reading live tickets does not enable live execution. |
| **Credentials** | Never stored in the database, never returned by the API. `/api/live/status` reports only whether they are present. |
| **Dedupe** | Keyed on `sys_id`, so re-polling will not duplicate a ticket or reset a triage decision you already made. |

**Multi-process leadership.** Every API process runs a lightweight supervisor,
but only the holder of the renewable `live-feed` database lease issues GETs.
The lease uses database time and a monotonically increasing fencing token;
standby processes can take over after expiry. Manual **Poll now** uses the same
lease and refuses concurrent polling. `/api/live/status` keeps the existing
`running` field and adds `supervising`, `leader_active`, `leader_here`, and the
local fencing token when this process is leader.

### Settings

| Setting | Default | Notes |
|---|---|---|
| `table` | `incident` | Any readable table |
| `query` | `active=true^u_record_type=incident^contact_type!=Automated` | See below |
| `interval_seconds` | `120` | Minimum 30 |
| `page_size` | `50` | 1–200 |
| `only_groups` | empty | Restrict ingest to named assignment groups |
| `auto_propose` | off | Create a run automatically on match — still only *proposed*, approval unchanged |

The default query matters on this instance. `u_record_type=incident` is required
because several record types share the incident table — without it you also get
`RQ` request records. `contact_type!=Automated` excludes the machine feed, which
is roughly 95% of raw volume.

Ingested tickets land in the inbox as `new` (a procedure matched) or `no_match`.
`no_match` is a correct answer, not a failure: it means the ticket stays entirely
with a human until someone publishes a procedure for it.

## Why it is shaped this way

The architecture exists to keep one promise: **the agent is granted work at the
step level, never at the ticket level.** A single ticket moves back and forth
between agent and human, and neither owns the whole thing.

```
domain.py     pure model    no framework, no DB, no transport
risk.py       policy        how a step is classified
knowledge.py  parsing       uploads -> reviewable drafts
registry.py   rule set      built-in + published procedures, plus the dictionary
planner.py    use case      ticket + procedure -> approvable plan
store.py      persistence   SQLite behind a repository seam
action_catalog.py  contract      immutable action versions and connector bindings
connectors.py      protocol      approval-bound envelope and structured result
action_gateway.py  execution     default-deny gateway; simulation default, scoped live mode for allowlisted connectors
connectors_servicenow.py real      ServiceNow work-note connector (dormant unless live mode + write switch)
simulator.py       compatibility legacy simulation helper (gateway is authoritative)
worker.py          background     consumes dispatched steps, executes via the gateway, reports to Step Functions
worker_transport.py seam          queue/reporter contracts + in-memory reference impls (offline-runnable)
worker_aws.py      transport      SQS + Step Functions implementations (lazy boto3)
worker_main.py     entrypoint     python -m overlay.worker_main for the ECS worker task
live.py       intake        read-only ServiceNow polling
service.py    application   every use case; the authority on all rules
api.py        transport     FastAPI; the contract every client speaks
ui/           one client    React app (light/dark); holds no logic
web/          fallback      original dependency-free client, served at /legacy
examples/     samples       handbooks and a dictionary you can upload as-is
```

## Why it is shaped this way

The architecture exists to keep one promise: **the agent is granted work at the
step level, never at the ticket level.** A single ticket moves back and forth
between agent and human, and neither owns the whole thing.

```
domain.py     pure model    no framework, no DB, no transport
risk.py       policy        how a step is classified
planner.py    use case      ticket + procedure -> approvable plan
store.py      persistence   SQLite behind a repository seam
action_catalog.py  contract      immutable action versions and connector bindings
connectors.py      protocol      approval-bound envelope and structured result
action_gateway.py  execution     default-deny gateway; simulation default, scoped live mode for allowlisted connectors
connectors_servicenow.py real      ServiceNow work-note connector (dormant unless live mode + write switch)
simulator.py       compatibility legacy simulation helper (gateway is authoritative)
service.py    application   every use case; the authority on all rules
api.py        transport     FastAPI; the contract every client speaks
web/          one client    thin browser client; holds no logic
```

## Converting to a desktop app later

This was built to make that a small change, not a rewrite. The rule that buys
it: **no business logic in the client.** The browser never decides what is
safe, approvable or runnable; it renders API responses and calls the API back.

To wrap it as a desktop app (Tauri, Electron, or a native WebView):

1. Point the client at the server by setting one global before the bundle loads:

   ```html
   <script>window.OVERLAY_API_BASE = "https://overlay.internal.example.com";</script>
   ```

   `api.js` reads `window.OVERLAY_API_BASE` and falls back to same-origin. That
   is the only code change required.

   Note the React app now has a build step (`npm run build`), so ship the
   contents of `overlay/ui/dist` rather than raw source files.

2. Allow the desktop origin on the server:

   ```powershell
   $env:OVERLAY_ALLOWED_ORIGINS = "tauri://localhost,https://overlay.internal.example.com"
   ```

3. Ship `overlay/web/` as the app's bundled front end. There is no build step,
   no framework and no bundler, so the files load as-is.

What must **not** move into the desktop app:

- credentials or connector calls,
- approval or risk decisions,
- run/step state,
- the audit log.

Those stay server-side because a shared queue, a background worker that
survives logoff, and an audit log that the audited user cannot edit all require
a central authority. The desktop app is a window onto that, the same as the
browser.

Other clients are equally cheap for the same reason: a CLI, or a Teams bot that
posts an approval card and calls `POST /api/runs/{id}/chunks/{id}/decision`.

## Authentication

The system answers one question the rest of it depends on: *who is this,
really?* A session is a signed cookie (`itsdangerous`); the browser holds it but
cannot forge it. There are two ways to sign in, chosen automatically.

**Entra ID (OIDC) — the real path.** Enabled when `LOKI_ENTRA_TENANT_ID`,
`LOKI_ENTRA_CLIENT_ID` and `LOKI_ENTRA_CLIENT_SECRET` are all set. The flow
redirects to Microsoft, returns an authorization code, and exchanges it for an
ID token. That token is then **verified, not merely decoded**:

- the RS256 signature is checked against the tenant JWKS
  (`.../discovery/v2.0/keys`), with the signing key selected by the token's
  `kid` and refetched automatically on key rollover;
- `iss` must equal the tenant v2.0 issuer, `aud` must equal the configured
  client id, and `exp`/`iat` must be present and current (60s clock-skew
  leeway);
- only `RS256` is accepted, so an HS256 algorithm-confusion token is rejected;
- the `nonce` must match the one minted at redirect time.

Any failure raises a safe `AuthError` with a user-facing message; token
contents are never logged. Only after verification are `email` and group/role
claims read. See `verify_id_token` in `overlay/auth.py`; the behaviour is
covered end-to-end by `tests/test_auth.py` using a locally generated RSA keypair
(no network).

**Dev login — local fallback.** Available ONLY when Entra is not configured and
`OVERLAY_ALLOW_DEV_LOGIN` is not `0`. It asks for an email and a role and trusts
them, so it can never ship silently as the auth mechanism in a real deployment.

**Roles.** Identity is separated from authority. A role (`viewer` → `operator` →
`approver` → `admin`) gates capabilities server-side; `LOKI_ENTRA_ROLE_MAP` maps
Entra group object-ids or app-role names to LOKI roles, and unmapped users fall
back to `LOKI_DEFAULT_ROLE`. A person's login never becomes the agent's
execution authority — the agent always acts through the scoped service accounts
in the credentials centre. Login proves who *approves*; it does not let the agent
act *as* that person.

## Background simulation worker

The Step Functions `RunWorkflow` routes an agent-owned step to a task that drops
a message on the Simulation SQS queue and waits for a task token. The
**simulation worker** picks that message up, runs the step, and hands the token
back. It is a Fargate service in the CDK platform stack (gated off with the rest
of the deployment), started with `python -m overlay.worker_main`.

The important property: **the worker does not contain a second execution path.**
It calls the exact seam the API uses — `OverlayService.execute_chunk` — which
runs through the default-deny `ActionGateway` in simulation mode, claims the
idempotency key before dispatch, writes the audit trail, and persists the
result. The worker only adds: pull from a queue, map the message to its chunk,
report the outcome.

Safety properties it inherits (not reimplemented):

- **Simulation-only.** `ActionGateway` refuses to construct in any other mode,
  and `worker_main` refuses to start unless `LOKI_EXECUTION_MODE=simulation`.
- **Exactly-once effect.** `execute_chunk` claims the idempotency key, so a
  redelivered SQS message replays the stored outcome instead of running twice.
  (When a redelivery arrives after the approval was already consumed, the worker
  re-reads the step's persisted state and reports success rather than failing a
  step that in fact succeeded.)
- **Default-deny.** Human-owned / R3 / RX / unresolved steps are refused by the
  gateway; the worker surfaces that as a Step Functions task failure for a human
  to pick up.

**Concurrency.** A single always-on worker. To stay safe even if two tasks ever
run, the loop holds the same kind of database-backed named lease the live feed
uses, and only the lease holder consumes; the idempotency ledger is the
belt-and-braces behind it.

**Transport is a seam.** `worker.py` imports no boto3. It talks to a `TaskQueue`
and a `TaskReporter` (`worker_transport.py`); the AWS implementations live in
`worker_aws.py` and are selected only by `worker_main.py`. The in-memory
implementations are the reference contract and make the whole worker runnable
and testable offline — see `tests/test_worker.py`.

## Real connectors

Until now every action resolved to the `simulation` connector, which touches
nothing. The **ServiceNow work-note connector** (`connectors_servicenow.py`) is
the first real one — chosen deliberately as the lowest-risk crossing of the
simulation→real line: posting a work note cannot alter infrastructure, is
trivially verifiable on the ticket, and reuses the credentials the read-only
live feed already uses.

**It is dormant by default.** Three independent gates must all be satisfied
before a single real write happens, and each is a separate, deliberate switch:

1. **A distinct catalogue action.** `servicenow.work_note.post@1.0.0` is bound to
   `(connector=servicenow, operation=post_work_note)`. The old simulated
   `notify` action is untouched; the real binding is a new entry on an explicit
   allowlist (`ActionGateway._LIVE_BINDINGS`). An action not on that allowlist
   can never reach a real connector — the gateway fails closed.
2. **Live gateway mode.** `LOKI_EXECUTION_MODE=live`. Only in live mode does the
   gateway even register the ServiceNow connector. The default is `simulation`;
   an unknown mode still fails at startup. A simulation-mode run can never be
   dispatched by a live gateway (the run mode and gateway mode must match), and
   a live gateway still refuses human-only / R3 / RX steps exactly as before.
3. **The write switch.** `LOKI_SERVICENOW_WRITE_ENABLED=1`. With live mode on but
   this off, the connector runs in **dry-run**: it validates everything,
   resolves credentials, and returns a `[DRY-RUN] would post…` result *without
   making any write call*. This lets the whole path be proven against a real
   instance before the first real write.

Credentials come from the environment via the live feed's resolver
(`SERVICENOW_INSTANCE` / `_USERNAME` / `_PASSWORD`, or the `SN_*` equivalents);
they never touch the database and are never returned to a client. The target
table defaults to `incident` (`LOKI_SERVICENOW_TABLE`). On a credential or HTTP
error (401/403/5xx) the connector raises `ConnectorFailure`, which the service
turns into a human handoff — it never silently drops the step.

**To actually enable a real write you will need to provide:**

- a ServiceNow **dev/sandbox instance** you are allowed to write to (not prod);
- a **service account** scoped to write work notes, separate from the read-only
  feed account;
- a **throwaway test ticket** to post to.

Then: set the credentials, `LOKI_EXECUTION_MODE=live`, run a `live`-mode run of
a ticket matched to the work-note action, approve the chunk, and — with
`LOKI_SERVICENOW_WRITE_ENABLED` still unset — watch the dry-run. Only once that
looks right do you set `LOKI_SERVICENOW_WRITE_ENABLED=1` for the first real
note. The behaviour is covered end-to-end by `tests/test_servicenow_connector.py`
against a mocked ServiceNow (no network).

## Risk model

Classification is **default-deny**: anything not explicitly classified becomes
`RX` and is never agent-executable.

| Class | Meaning | Agent may run |
|---|---|---|
| `R0` | Read-only observation | Yes, with chunk approval |
| `R1` | Low impact, additive or reversible | Yes, with chunk approval |
| `R2` | Real operational change | Yes, with its **own** step approval |
| `R3` | High impact or destructive | No — human only |
| `RX` | Unclassified | No — human only |

`run_command` maps to `RX` on purpose. A read-only `Get-PSDrive` and a
destructive `Remove-Item` are the same action, so the action alone cannot
justify execution. That is why the Wintel disk-cleanup procedure splits into
agent steps 1–2, human steps 3–5, agent steps 6–7.

Raw `run_command` steps are permanently human-owned in the versioned action
catalogue, even if an uploaded procedure declares a low risk. To automate a
specific operation, add a dedicated action definition (for example,
`windows.disk.free_space.read@1.0.0`) with a fixed connector, operation,
parameter schema and simulation behavior, then map it to a reviewed connector
implementation. Procedure metadata may narrow authority, but cannot turn an
arbitrary command into an executable action.

Existing procedures need no edits; their legacy action names resolve to pinned
`1.0.0` catalogue entries. Unclassified or human-only actions stay with a human.

## Safety controls (all enforced server-side)

| Control | Behaviour |
|---|---|
| No execution without approval | Refused, and the attempt is audited |
| Human-owned chunks cannot be approved for the agent | Refused |
| Approval binds to what was shown and what will execute | Digest covers procedure version, steps, rendered text, parameters, action catalogue version, exact action version and connector operation |
| Concurrent/replayed execution | A durable idempotency key is claimed before dispatch; only the first caller can execute the step |
| Unknown or reinterpreted actions | Default-denied unless an exact catalogue action/version and connector/operation binding exists |
| Editing parameters invalidates approval | Digest changes; re-approval required |
| One active approval per chunk | Prior approvals superseded on a new decision |
| Steps run in order | Refuses to jump ahead of outstanding work |
| `R2` needs individual approval | Cannot hide inside a low-risk chunk |
| `R3`/`RX` never agent-executed | Refused by policy and again by the action gateway |
| Unresolved `<param?>` blocks the step | Hands off to a human |
| Manual completion requires evidence | Empty evidence refused |
| Audit is append-only | No update or delete path |
| A ticket cannot be forked by accident | A second run for an in-progress ticket is refused with `409 duplicate_run`, naming the existing run. `?allow_duplicate=true` overrides it, and both audit trails cross-reference the other. Finished runs never block a new one. |
| Uploads are inert until published | Drafts are never loaded by the matcher |
| A draft with validation errors cannot be published | Refused, with the reasons listed |
| Declared low risk on an unverifiable action is flagged | Warning shown on the review screen before publishing |
| Dictionaries cannot affect permission | They only widen matching |
| Live feed is read-only | The polling path issues GET only; it has no write path to ServiceNow |
| Writes only via a gated connector | The sole write path is the ServiceNow work-note connector, dormant unless live mode AND the write switch are both set |
| Live credentials are never exposed | Read from the environment; the API reports presence only |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LOKI_STORE_BACKEND` | `sqlite` | `sqlite` for local use; `postgres` fails closed unless PostgreSQL is ready |
| `OVERLAY_DB` | `overlay_tasks.db` | SQLite path (ignored by PostgreSQL) |
| `LOKI_POSTGRES_DSN` | — | Optional secret DSN; otherwise use host/db/user/password variables |
| `LOKI_POSTGRES_HOST` | — | PostgreSQL or RDS Proxy endpoint |
| `LOKI_POSTGRES_PORT` | `5432` | PostgreSQL port |
| `LOKI_POSTGRES_DB` | `loki` | PostgreSQL database name |
| `LOKI_POSTGRES_USER` | — | Application database role |
| `LOKI_POSTGRES_PASSWORD` | — | Application database password; inject from a secret store |
| `LOKI_POSTGRES_SSLMODE` | `require` | TLS mode for PostgreSQL |
| `LOKI_POSTGRES_AUTO_MIGRATE` | `0` | Explicit only; production migrations run as a separate task |
| `LOKI_EXECUTION_MODE` | `simulation` | Execution boundary; `simulation` (default) or `live`. Any other value fails at startup |
| `LOKI_SERVICENOW_WRITE_ENABLED` | `0` | Second switch for real ServiceNow writes; dry-run unless `1` even in live mode |
| `LOKI_SERVICENOW_TABLE` | `incident` | ServiceNow table the work-note connector targets |
| `LOKI_ENFORCE_SOD` | `1` | Separation of duties: the approver cannot also trigger execution. `0` relaxes it for a single-user dev box |
| `LOKI_APPROVAL_TTL_SECONDS` | `3600` | Approval lifetime; execution refused past it. `0` disables expiry |
| `LOKI_SEMANTIC_SUGGESTIONS` | `1` | Advisory meaning-based procedure suggestions (`/api/suggest`); authorises nothing. `0` disables |
| `LOKI_SIMULATION_QUEUE_URL` | — | SQS FIFO queue the worker consumes (required by `worker_main`) |
| `LOKI_WORKER_LEASE_TTL_SECONDS` | `120` | Worker leadership lease TTL |
| `LOKI_WORKER_WAIT_SECONDS` | `20` | SQS long-poll wait per receive |
| `LOKI_WORKER_BATCH` | `1` | Max messages pulled per receive |
| `LOKI_SECRET_KEY` | random per process | Session cookie signing key; unset means everyone is logged out on restart |
| `LOKI_SESSION_MAX_AGE` | `28800` | Session lifetime in seconds (8h) |
| `LOKI_ENTRA_TENANT_ID` | — | Entra tenant; all three Entra values switch on real auth |
| `LOKI_ENTRA_CLIENT_ID` | — | Entra app (client) id; also the expected token audience |
| `LOKI_ENTRA_CLIENT_SECRET` | — | Entra client secret; inject from a secret store |
| `LOKI_ENTRA_REDIRECT_URI` | `http://127.0.0.1:8800/api/auth/callback` | OIDC redirect URI registered with Entra |
| `LOKI_JWKS_CACHE_SECONDS` | `3600` | How long a fetched JWKS is reused; key rollover still refetches on unknown `kid` |
| `LOKI_ENTRA_ROLE_MAP` | — | `value=role,value=role` mapping Entra groups/app-roles to LOKI roles |
| `LOKI_DEFAULT_ROLE` | `operator` | Role for a verified user with no mapped group |
| `OVERLAY_ALLOW_DEV_LOGIN` | `1` | Dev login escape hatch; ignored entirely when Entra is configured |
| `OVERLAY_ALLOWED_ORIGINS` | `*` | CORS allowlist; tighten before deployment |
| `OVERLAY_MAX_UPLOAD_BYTES` | `5242880` | Upload size cap (5 MB) |
| `SYS2_MIN_SCORE` | `3` | Minimum match confidence (System 2) |
| `SERVICENOW_INSTANCE` | — | Live feed host (also accepts `SN_INSTANCE`) |
| `SERVICENOW_USERNAME` | — | Live feed user (also accepts `SN_USERNAME`) |
| `SERVICENOW_PASSWORD` | — | Live feed password (also accepts `SN_PASSWORD`) |

**Health endpoints:** `/api/health` is process liveness only. `/api/ready`
checks the selected repository, required schema version, and loaded procedure
registry; it returns HTTP 503 without exposing connection details when the
application is not ready.

**Approval migration:** the execution binding digest is now version 2 and also
binds the action catalogue/action version and connector operation. Any active
approval created by an older build fails closed and must be approved again; old
runs and audit history remain readable.

## Known limitations

Honest list, because these matter before anyone trusts it:

- **Sign-in is verified; wiring the IdP is still a deployment step.** Entra ID
  tokens are now RS256-verified against the tenant JWKS with issuer, audience,
  expiry and nonce checks (see [Authentication](#authentication)), so a verified
  SSO identity is available. What remains is operational: creating the Entra app
  registration, setting the three `LOKI_ENTRA_*` values, and ensuring every
  write path reads the signed session rather than a client-asserted `X-Actor`
  header. Until the API enforces that on all routes, do not expose this build
  beyond a trusted network — anyone who can reach the API can still publish a
  procedure.
- **Separation of duties (partial).** The person who *approved* a chunk can no
  longer *trigger* its execution — that is enforced server-side in
  `execute_chunk` and audited (`LOKI_ENFORCE_SOD`, on by default; relax only for
  a single-user dev box). Not yet separated: upload vs publish by the same
  person. The audit records both actors regardless.
- **Approval expiry (enforced).** An approval older than
  `LOKI_APPROVAL_TTL_SECONDS` (default 1 hour; `0` disables) no longer
  authorises execution and must be renewed. Refusals are audited.
- **Persistence and horizontal-scale gates.** SQLite remains the local default;
  a pooled PostgreSQL adapter and explicit migration tooling now exist. Registry
  caches refresh through a database revision and one fenced lease selects the
  ServiceNow poller across processes. More than one web task remains blocked by
  abandoned execution-claim recovery and remaining multi-write transaction
  boundaries.
- **Background execution exists but is not yet the default path.** A simulation
  worker (`overlay/worker.py`) can consume dispatched steps off a queue and run
  them through the same gateway out-of-band, reporting back to Step Functions
  (see [Background simulation worker](#background-simulation-worker)). The
  synchronous in-request path in `execute_chunk` is still what the API uses
  today; wiring the API to enqueue instead of execute inline is a later step.
  Genuinely long-running or scheduled steps (a decommission soak) still need the
  workflow to model the wait.
- **No rollback execution.** The `rollback` field is loaded but never run.
- **Matching is keyword substring scoring.** Good enough to recommend, not to
  authorise. That is why approval is mandatory. A dictionary widens recall but
  does not make the scoring smarter.
- **Live polling is a recent-window read, not a backfill.** It fetches the most
  recently updated page each cycle and dedupes on `sys_id`. It will not
  retroactively pull history, and a ticket updated while the feed was off is
  only picked up if it is still in the recent window.
- **Markdown parsing is best-effort.** Structured formats (JSON/YAML/CSV) are
  exact; Markdown depends on the documented heading and list conventions.
