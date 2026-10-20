# BUCKY Infrastructure (Terraform)

Terraform infrastructure for Project BUCKY / BUCKY, following the TP ICAP
`pace/pace-team/manila/ecs` convention: a `modules/` library composed by a
per-environment root under `environments/`.

This replaces the earlier AWS CDK stacks.

## Layout

```
infrastructure/
â”œâ”€â”€ environments/
â”‚   â””â”€â”€ dev/                 Root module for the dev environment
â”‚       â”œâ”€â”€ backend.tf       GitLab-managed HTTP state backend
â”‚       â”œâ”€â”€ providers.tf     AWS provider + assume_role (platform automation role)
â”‚       â”œâ”€â”€ variables.tf     All inputs
â”‚       â”œâ”€â”€ main.tf          Composes the modules
â”‚       â””â”€â”€ outputs.tf       ALB DNS, ECR URLs, queue URL, etc.
â””â”€â”€ modules/
    â”œâ”€â”€ ecr/                 Image repositories (api, worker)
    â”œâ”€â”€ ecs-cluster/         Fargate cluster + shared log group
    â”œâ”€â”€ s3/                  ALB access-logs bucket
    â”œâ”€â”€ rds/                 Aurora PostgreSQL Serverless v2 + RDS Proxy + secret
    â”œâ”€â”€ alb/                 Internal ALB + HTTPS listener + target group
    â”œâ”€â”€ sqs/                 Simulation FIFO queue + DLQ
    â”œâ”€â”€ stepfunctions/       Run-workflow state machine
    â”œâ”€â”€ iam/                 Execution role + per-service task roles
    â””â”€â”€ ecs-service/         API + worker task definitions and Fargate services
```

## What it creates

```
            internal ALB (HTTPS)
                   â”‚
   ECS Fargate: api  â”€â”€â”€â”
   ECS Fargate: worker â”€â”¤â”€â”€â–º Aurora PostgreSQL (via RDS Proxy)
                   â”‚     â”‚
        Step Functions â—„â”´â”€â–º SQS simulation queue (+ DLQ)
                   â”‚
        CloudWatch logs / Container Insights
```

BUCKY needs Aurora + an ALB (a web service), which the manila ETL repo does not;
it does **not** need Redshift or Glue, which manila does. So this follows
manila's *structure* with BUCKY's *resource set*.

## How to deploy

The VPC, subnets, TLS cert, and the platform automation role are provided by the
platform team â€” this stack does not create the network.

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

# 3. Apply (images must already be in ECR â€” CI builds + pushes first)
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
   exists â€” so either apply ECR first (`terraform apply -target=module.ecr`),
   push the images, then apply the rest; or run the CI build stage before the
   infra stage. The CI pipeline is ordered build â†’ infra for this reason.

## Notes

- The API and worker both run as **single tasks** (`desired_count = 1`) until
  the multi-write transaction boundaries and abandoned execution-claim recovery
  are implemented â€” matching the application's documented limitation.
- Execution stays `simulation` mode. A real connector requires its own reviewed
  change plus the per-connector write switch; it is not enabled by this infra.
- The `assume_role` to the platform automation role is how deploy permissions
  (e.g. `iam:CreateRole`) are obtained â€” the interactive DevOps SSO role lacks
  them, which is why a direct `cdk deploy` / `terraform apply` from that role
  could not create IAM roles.
