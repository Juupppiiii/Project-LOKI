# LOKI Infrastructure (Terraform)

Terraform infrastructure for Project STEPH / LOKI, following the TP ICAP
`pace/pace-team/manila/ecs` convention: a `modules/` library composed by a
per-environment root under `environments/`.

This replaces the earlier AWS CDK stacks.

## Layout

```
infrastructure/
├── environments/
│   └── dev/                 Root module for the dev environment
│       ├── backend.tf       GitLab-managed HTTP state backend
│       ├── providers.tf     AWS provider + assume_role (platform automation role)
│       ├── variables.tf     All inputs
│       ├── main.tf          Composes the modules
│       └── outputs.tf       ALB DNS, ECR URLs, queue URL, etc.
└── modules/
    ├── ecr/                 Image repositories (api, worker)
    ├── ecs-cluster/         Fargate cluster + shared log group
    ├── s3/                  ALB access-logs bucket
    ├── rds/                 Aurora PostgreSQL Serverless v2 + RDS Proxy + secret
    ├── alb/                 Internal ALB + HTTPS listener + target group
    ├── sqs/                 Simulation FIFO queue + DLQ
    ├── stepfunctions/       Run-workflow state machine
    ├── iam/                 Execution role + per-service task roles
    └── ecs-service/         API + worker task definitions and Fargate services
```

## What it creates

```
            internal ALB (HTTPS)
                   │
   ECS Fargate: api  ───┐
   ECS Fargate: worker ─┤──► Aurora PostgreSQL (via RDS Proxy)
                   │     │
        Step Functions ◄┴─► SQS simulation queue (+ DLQ)
                   │
        CloudWatch logs / Container Insights
```

LOKI needs Aurora + an ALB (a web service), which the manila ETL repo does not;
it does **not** need Redshift or Glue, which manila does. So this follows
manila's *structure* with LOKI's *resource set*.

## How to deploy

The VPC, subnets, TLS cert, and the platform automation role are provided by the
platform team — this stack does not create the network.

```bash
cd infrastructure/environments/dev

# 1. Init with the GitLab-managed state backend (values from CI/CD variables)
terraform init \
  -backend-config="address=$TF_STATE_ADDRESS" \
  -backend-config="lock_address=$TF_STATE_ADDRESS/lock" \
  -backend-config="unlock_address=$TF_STATE_ADDRESS/lock"

# 2. Validate / plan (plan reaches AWS via the assumed automation role)
terraform fmt -check
terraform validate
terraform plan -var-file=dev.tfvars

# 3. Apply (images must already be in ECR — CI builds + pushes first)
terraform apply -var-file=dev.tfvars
```

## Required inputs (dev.tfvars)

| Variable | Example | Source |
|---|---|---|
| `automation_role_arn` | `arn:aws:iam::<acct>:role/automation-role-platform` | Platform team |
| `vpc_id`, `vpc_cidr`, `subnet_ids` | existing private network | Platform team |
| `certificate_arn` | ACM cert covering the service hostname | Platform/networking |
| `allowed_ingress_cidrs` | internal client CIDRs | You |
| `auth_secret_arn` | Secrets Manager ARN (Entra + session key) | You / Entra admins |
| `api_image_tag`, `worker_image_tag` | immutable commit tags | CI build stage |
| `enable_bedrock`, `bedrock_model_arns` | advisory LLM | Optional |

## Order of operations

Terraform creates the ECR repos, but the task definitions reference an image
tag that must already exist. So the pipeline is:

1. `terraform apply` the **first time** will fail the ECS services if no image
   exists — so either apply ECR first (`terraform apply -target=module.ecr`),
   push the images, then apply the rest; or run the CI build stage before the
   infra stage. The CI pipeline is ordered build → infra for this reason.

## Notes

- The API and worker both run as **single tasks** (`desired_count = 1`) until
  the multi-write transaction boundaries and abandoned execution-claim recovery
  are implemented — matching the application's documented limitation.
- Execution stays `simulation` mode. A real connector requires its own reviewed
  change plus the per-connector write switch; it is not enabled by this infra.
- The `assume_role` to the platform automation role is how deploy permissions
  (e.g. `iam:CreateRole`) are obtained — the interactive DevOps SSO role lacks
  them, which is why a direct `cdk deploy` / `terraform apply` from that role
  could not create IAM roles.
