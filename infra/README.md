# LOKI AWS infrastructure and deployment gates

This CDK application implements the production foundation and an explicitly
gated ECS service path. It cannot enable live job execution: every deployed
service is forced to `LOKI_EXECUTION_MODE=simulation`.

No AWS resource has been deployed from this workspace.

## Deployment modes

### Foundation-only (default)

`loki:deployService=false` is committed in `cdk.json`. CDK creates the network,
data, workflow, queue, registry, and observability foundations, but no ECS task,
load balancer, DNS record, or service.

```powershell
npm ci
npm run build
npm run synth
```

### Gated application service

The service branch is synthesized only when all of these are supplied:

- `loki:deployService=true`
- `loki:storeBackend=postgres`
- immutable `loki:imageTag` (the tag `latest` is rejected)
- `loki:certificateArn`
- restricted `loki:allowedIngressCidr`
- external `loki:authSecretArn`
- `loki:hostedZoneId`, `loki:hostedZoneName`, and `loki:serviceDomainName`

Example **synthesis only**:

```powershell
npx cdk synth Loki-dev-Platform --strict `
  -c loki:deployService=true `
  -c loki:storeBackend=postgres `
  -c loki:imageTag=release-0.1.0 `
  -c loki:certificateArn=arn:aws:acm:REGION:ACCOUNT:certificate/ID `
  -c loki:allowedIngressCidr=10.0.0.0/8 `
  -c loki:authSecretArn=arn:aws:secretsmanager:REGION:ACCOUNT:secret:loki/auth-ID `
  -c loki:hostedZoneId=ZONE_ID `
  -c loki:hostedZoneName=internal.example.com `
  -c loki:serviceDomainName=loki.internal.example.com
```

The authentication secret must contain these JSON keys:

```json
{
  "session_key": "long-random-session-signing-key",
  "tenant_id": "entra-tenant-id",
  "client_id": "entra-application-id",
  "client_secret": "entra-client-secret"
}
```

The secret is supplied externally; CDK does not create or print identity-provider
credentials. If it uses a customer-managed KMS key, its key policy must allow the
ECS execution role to decrypt through Secrets Manager.

## Stacks

### `Loki-<environment>-Network`

- Two-AZ VPC.
- Public ingress, private application, isolated execution, and isolated data
  subnets.
- One NAT gateway in development, two in production.
- Restricted default security group.
- VPC Flow Logs retained in CloudWatch Logs.
- S3 gateway endpoint and private endpoints for ECR, CloudWatch Logs, KMS,
  Secrets Manager, Systems Manager, EC2 Messages, SSM Messages, and STS.

### `Loki-<environment>-Data`

- Customer-managed KMS key with rotation.
- Versioned, private, TLS-only evidence bucket.
- Versioned, private, TLS-only audit bucket with S3 Object Lock.
- Aurora PostgreSQL Serverless v2 writer and reader, encryption, backups,
  deletion protection, retained resources, PostgreSQL logs, and RDS Proxy.
- Separate generated `loki_app` credential secret for the application. The
  migration/bootstrap process must create and grant this database role before
  the service is started.
- RDS Proxy endpoint and application-secret ARN outputs.

### `Loki-<environment>-Platform`

Always present:

- KMS-encrypted, immutable-tag ECR repository with scan-on-push.
- ECS cluster with Container Insights.
- EventBridge domain-event bus.
- Dedicated queues and matching DLQs for human approval, simulation,
  ServiceNow, Systems Manager, Active Directory, vCenter, AWS-native, and HTTP
  connectors. Execution queues and their DLQs are FIFO.
- Step Functions Standard callback workflow.
- X-Ray workflow tracing, CloudWatch logs, Managed Prometheus, queue dashboard,
  and DLQ alarms.

Only with the explicit service gate:

- Least-privilege ECS execution role.
- Read-only-root-filesystem Fargate task using PostgreSQL through RDS Proxy.
- Entra/auth secrets injected from Secrets Manager.
- Auth enforcement, secure cookies, disabled developer login, strict same-origin
  CORS, and simulation-only execution.
- Internal TLS ALB, ACM certificate, restricted CIDR ingress, access logs,
  private Route 53 alias, `/api/ready` target health check, and unhealthy-host
  alarm.
- One task only. Registry invalidation and live-feed lease leadership are now
  coordinated; multi-task scaling remains blocked by remaining multi-write
  transaction boundaries and abandoned execution-claim recovery.

## PostgreSQL repository

`overlay/postgres_store.py` implements the full current repository contract with
Psycopg connection pooling and JSONB. `overlay/store_factory.py` selects the
backend explicitly:

```text
LOKI_STORE_BACKEND=sqlite   # default, local development
LOKI_STORE_BACKEND=postgres # production; never falls back to SQLite
```

PostgreSQL schema files are versioned under `overlay/sql/postgres/`.
Applications check the required schema version but should not run DDL on every
startup. Run migrations in a one-off migration task before updating the service.

Required PostgreSQL runtime variables:

```text
LOKI_POSTGRES_HOST
LOKI_POSTGRES_PORT=5432
LOKI_POSTGRES_DB=loki
LOKI_POSTGRES_USER
LOKI_POSTGRES_PASSWORD
LOKI_POSTGRES_SSLMODE=require
LOKI_POSTGRES_POOL_MIN=1
LOKI_POSTGRES_POOL_MAX=10
LOKI_POSTGRES_AUTO_MIGRATE=0
```

`LOKI_POSTGRES_DSN` can replace the individual settings, but must be injected as
a secret and never logged.

## SQLite-to-PostgreSQL migration

Dry-run is the default and opens SQLite read-only. It does not connect to
PostgreSQL:

```powershell
python scripts/migrate_sqlite_to_postgres.py
```

To migrate, supply PostgreSQL settings for an administrative migration identity,
then run:

```powershell
python scripts/migrate_sqlite_to_postgres.py --apply
```

The tool:

1. takes an advisory migration lock;
2. applies pending versioned DDL;
3. refuses any non-empty target table;
4. copies all LOKI tables in one transaction;
5. verifies each table using count and normalized SHA-256 checksum.

There is deliberately no truncate or overwrite option. Use `--verify-only` to
compare an existing target without copying.

After data migration, create/grant the `loki_app` role using the generated
application secret through a separately reviewed administrative bootstrap step.
The web task receives only this least-privilege account, never the cluster admin.

## Health model

- `/api/health`: process liveness only; used by the container.
- `/api/ready`: selected database connectivity, required schema version, and
  loaded procedure-registry check; used by the ALB. Failure returns 503 without
  returning endpoint or credential details.

## Container runtime

From the repository root:

```powershell
docker build -t loki:simulation .
docker run --rm -p 8800:8800 -v loki-data:/var/lib/loki loki:simulation
```

The image builds the existing React UI unchanged, runs as a non-root Linux user,
and uses one Uvicorn worker. SQLite under `/var/lib/loki` is for local/sandbox
simulation only. The gated ECS path overrides it with PostgreSQL.

The UI build requires the corporate Nexus registry and trusted CA. Never copy a
workstation `.npmrc`, token, `.env`, or private CA material into the image.

## Pinned toolchain

- `aws-cdk-lib` 2.271.0
- AWS CDK CLI 2.1143.0
- `constructs` 10.8.1
- `cdk-nag` 3.0.2
- TypeScript 5.9.3
- `tsx` 4.23.15
- Psycopg 3.3.6 and Psycopg Pool 3.3.3

## Deployment prerequisites

Before any `cdk deploy`:

1. Build and scan an immutable application image in CI and push the exact tag.
2. Deploy the foundation stacks and run the migration/bootstrap task.
3. Verify migration counts/checksums and `/api/ready` against a sandbox target.
4. Validate Entra token signatures and claims; do not rely on decoded unsigned
   token content.
5. Create the external auth secret and restrict its resource/KMS policies.
6. Confirm certificate, private DNS, ingress CIDR, and controlled HTTPS egress.
7. Keep live execution disabled; connector workers require separate review,
   task roles, runbooks, secrets, postconditions, rollback, and kill switches.
8. Complete threat modelling, backup restore, failover, and failure-mode tests.
9. Run `cdk diff` and review replacements. Never use hotswap or express mode in
   production.
10. Obtain explicit change approval. Synthesis is not authorization to deploy.

## Remaining scale gates

PostgreSQL removes the local-file limitation. Database-triggered revision
counters now invalidate published-procedure/dictionary caches across tasks, and
a renewable fenced database lease selects one ServiceNow poller with standby
takeover. These items still block more than one web task:

- atomic multi-write service use cases beyond the execution claim;
- abandoned execution-claim lease recovery;
- external background workers for long-running connector actions.

## Cost notes

The largest steady costs are Aurora capacity, NAT gateways, interface endpoints,
retained logs, ALB, and Managed Prometheus. Private endpoints improve network
control but are not automatically cheaper. Measure traffic before changing the
NAT/endpoint balance.

## Validation limitations on this workstation

- Docker is not installed, so the Python 3.12 image was not built or run.
- Local Python is 3.14; pinned dependencies and API imports are validated there.
- Nexus UI audit reports six advisories but proposes implausible downgrades while
  installed React/Vite packages are the latest Nexus offers. Do not force-fix;
  the package-security/registry owners must validate those advisories.
- CDK foundation-only and explicit gated-service synthesis pass locally. Dummy
  ARNs were used only to validate template construction; no AWS API mutation or
  deployment was attempted.
