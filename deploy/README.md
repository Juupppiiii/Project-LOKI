# Deployment — AWS

Everything you need to deploy LOKI to AWS, in one place. The actual files stay
in their functional locations (Docker needs the Dockerfile at the build-context
root; CDK needs its code in `infra/`), so this is the **map and the runbook**,
not a copy.

> Status: **not deployed.** The code and infrastructure are ready; deployment is
> blocked on organizational actions (see "Blockers" below), not on code.

## The pieces and where they live

| Piece | Location | Purpose |
|---|---|---|
| Container image | `Dockerfile`, `.dockerignore` (repo root) | Builds the API + UI image ECS runs. Two-stage: Node builds the UI, Python serves it. Must stay at root so `COPY requirements-api.txt` and `COPY overlay` resolve. |
| Python dependencies | `requirements-api.txt` (runtime), `requirements-dev.txt`, `requirements.txt` (repo root) | `requirements-api.txt` is what the image installs. |
| Infrastructure (CDK) | `infra/` | Three stacks: `Loki-dev-Network`, `Loki-dev-Data`, `Loki-dev-Platform`. TypeScript CDK. |
| CDK config | `infra/cdk.json`, `infra/bin/loki.ts` | App entry + context flags (`loki:deployService`, etc.). |
| Stacks | `infra/lib/network-stack.ts`, `data-stack.ts`, `platform-stack.ts` | VPC; Aurora PostgreSQL + RDS Proxy + secrets; ECS Fargate + ALB + ECR + SQS + Step Functions + observability. |

## What gets created when deployed

```
            internal ALB (TLS)
                   │
   ECS Fargate: API task  ──┐
   ECS Fargate: worker task ┤──► Aurora PostgreSQL (via RDS Proxy)
                   │         │
         Step Functions ◄──► SQS queues (per connector)
                   │
         Prometheus / Grafana / CloudWatch
```

Once deployed, the knowledge base (uploaded handbooks, procedures) lives in the
shared Aurora store, so **every user hits the same library** — which is the
"shared for everyone" goal. Today it runs on per-machine SQLite.

## Deployment steps (once blockers are cleared)

```powershell
# 1. Bootstrap CDK in the target account (needs iam:CreateRole)
cd infra ; npx cdk bootstrap aws://<ACCOUNT>/eu-west-1

# 2. Build and push the image to ECR (the platform stack creates the repo)
docker build -t loki:<release-tag> .
#    then tag + push to the ECR repository URI from the platform stack output

# 3. Deploy the foundation (service gated OFF by default)
npx cdk deploy Loki-dev-Network Loki-dev-Data Loki-dev-Platform

# 4. Enable the service once PostgreSQL/auth/TLS contexts are supplied
#    (loki:deployService=true + the required context values in loki.ts)
```

## Blockers (organizational, not code)

Verified against account `813714503704` / eu-west-1:

| Blocker | Who clears it |
|---|---|
| Shared `CDKToolkit` bootstrap is `UPDATE_ROLLBACK_FAILED` | Platform team (repair) or use a dedicated account |
| The `DevOps` SSO role lacks `iam:CreateRole` | Cloud admin — bootstrap and the stacks create IAM roles |
| Account decision: shared `813714503704` vs a dedicated LOKI account | You + platform team |
| Internal DNS hostname + TLS cert covering it (wildcard didn't cover a two-label name) | Platform / networking team |
| Secrets in AWS Secrets Manager (not `.env`) | You — and rotate the ServiceNow password that was exposed |

## Runtime configuration (set on the ECS task / environment)

See `overlay/README.md` for the full table. The deployment-critical ones:

| Variable | Deploy value | Why |
|---|---|---|
| `LOKI_STORE_BACKEND` | `postgres` | Shared, multi-user store |
| `LOKI_EXECUTION_MODE` | `simulation` | Keep simulation until a connector is signed off |
| `LOKI_ENFORCE_AUTH` | `1` | Require a verified Entra session on every write |
| `LOKI_LLM_ENABLED` | `1` (optional) | Advisory Opus 5 recommendations |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | The access-enabled model in eu-west-1 |

For the full infrastructure detail, see `infra/README.md` and
`docs/ARCHITECTURE.md`.
