# Deployment â€” AWS

Everything you need to deploy BUCKY to AWS, in one place. The container files
stay at the repo root (Docker needs the Dockerfile at the build-context root),
so this is the **map and the runbook**, not a copy.

> Status: **not deployed.** The code and infrastructure are ready; deployment is
> blocked on organizational actions (see "Blockers" below), not on code.

## The pieces and where they live

| Piece | Location | Purpose |
|---|---|---|
| Container image | `Dockerfile`, `.dockerignore` (repo root) | Builds the API + worker image ECS runs. Two-stage: Node builds the UI, Python serves it. Must stay at root so `COPY requirements-api.txt` and `COPY overlay` resolve. |
| Python dependencies | `requirements-api.txt` (runtime), `requirements-dev.txt`, `requirements.txt` (repo root) | `requirements-api.txt` is what the image installs. |
| Infrastructure (Terraform) | `infrastructure/` | Modules + `environments/dev/`, in the `pace/manila` convention. See `infrastructure/README.md`. |
| Dev root module | `infrastructure/environments/dev/` | `backend.tf` (GitLab state), `providers.tf` (assume_role), `variables.tf`, `main.tf`, `outputs.tf`. |
| Modules | `infrastructure/modules/{ecr,ecs-cluster,s3,rds,alb,sqs,stepfunctions,iam,ecs-service}` | ECR; Fargate cluster; ALB logs bucket; Aurora + RDS Proxy; internal ALB; SQS; Step Functions; IAM roles; the API + worker services. |

## What gets created when deployed

```
            internal ALB (TLS)
                   â”‚
   ECS Fargate: API task  â”€â”€â”
   ECS Fargate: worker task â”¤â”€â”€â–º Aurora PostgreSQL (via RDS Proxy)
                   â”‚         â”‚
         Step Functions â—„â”€â”€â–º SQS queues (per connector)
                   â”‚
         Prometheus / Grafana / CloudWatch
```

Once deployed, the knowledge base (uploaded handbooks, procedures) lives in the
shared Aurora store, so **every user hits the same library** â€” which is the
"shared for everyone" goal. Today it runs on per-machine SQLite.

## Deployment steps (once inputs are provided)

Terraform needs no CDK bootstrap. The CI pipeline runs build â†’ infra; to deploy
by hand:

```bash
# 1. Build and push both images to ECR (CI's docker-build stage does this)
docker build -t <ecr>/bucky/api:<tag> .
docker build -t <ecr>/bucky/worker:<tag> .
#    docker push both

# 2. Init Terraform against the GitLab-managed state backend
cd infrastructure/environments/dev
terraform init -backend-config=...     # see infrastructure/README.md

# 3. First apply must create ECR before the services can reference an image:
terraform apply -target=module.ecr -var-file=dev.tfvars   # then push images
terraform apply -var-file=dev.tfvars \
  -var="api_image_tag=<tag>" -var="worker_image_tag=<tag>"
```

The services default to `desired_count = 1` and `simulation` mode.

## Blockers (organizational, not code)

Verified against account `813714503704` / eu-west-1:

| Blocker | Status with Terraform | Who clears it |
|---|---|---|
| Broken shared `CDKToolkit` bootstrap | **Resolved** â€” Terraform needs no CDK bootstrap | n/a |
| `DevOps` SSO role lacks `iam:CreateRole` | **Resolved** â€” the provider assumes the platform automation role (same as `pace/manila`) | Needs the automation role ARN + trust |
| `automation_role_arn` to assume | Required input | Platform team |
| Account decision: `813714503704` vs dedicated | Open | You + platform team |
| VPC/subnets, DNS hostname + TLS cert | Required inputs (`vpc_id`, `subnet_ids`, `certificate_arn`) | Platform / networking team |
| GitLab Terraform state backend variables | `TF_STATE_*` CI/CD variables | You |
| Secrets in AWS Secrets Manager (not `.env`) | `auth_secret_arn` input | You â€” and rotate the exposed ServiceNow password |

## Runtime configuration (set on the ECS task / environment)

See `overlay/README.md` for the full table. The deployment-critical ones:

| Variable | Deploy value | Why |
|---|---|---|
| `BUCKY_STORE_BACKEND` | `postgres` | Shared, multi-user store |
| `BUCKY_EXECUTION_MODE` | `simulation` | Keep simulation until a connector is signed off |
| `BUCKY_ENFORCE_AUTH` | `1` | Require a verified Entra session on every write |
| `BUCKY_LLM_ENABLED` | `1` (optional) | Advisory Opus 5 recommendations |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | The access-enabled model in eu-west-1 |

For the full infrastructure detail, see `infra/README.md` and
`docs/ARCHITECTURE.md`.
