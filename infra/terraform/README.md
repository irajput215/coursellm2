# CourseLLM AWS infrastructure (Terraform)

> ## NOT APPLIED — nothing in this directory has ever been applied
>
> There is **no live AWS deployment**. No account is referenced, no VPC, ECS
> service, ALB, RDS instance, ElastiCache node, S3 bucket, CloudFront
> distribution, secret or alarm exists as part of this repository, and **no AWS
> bill is being generated**.
>
> `terraform apply` has **not** been run and must not be run from a review of
> this PR. `terraform plan` creates no resources and is the only command that
> has been executed against this configuration. It is the whole point of the
> PR: a configuration that can be applied deliberately, with the cost, the
> failure modes and the security boundary understood in advance — not a claim
> that any of it is running.
>
> Every figure below is an estimate derived from published AWS list prices with
> the assumptions stated. None of it is an observed bill. No resource is
> claimed to exist.

This directory implements the target architecture in
[`docs/architecture/deployment.md`](../../../docs/architecture/deployment.md)
sections 4–12: CloudFront → ALB → ECS/Fargate, a private-only data tier with RDS
PostgreSQL + pgvector and ElastiCache Redis, S3 for objects, Secrets Manager for
credentials and SSM Parameter Store for non-secret configuration, and
CloudWatch for logs, metrics, alarms and a dashboard.

---

## 1. Topology

```text
                         ┌──────────────────────────────────────────────┐
   browser ──HTTPS──▶    │ CloudFront (prod only; S3 origin + ALB /api/*)│
                         └───────────────┬──────────────────────────────┘
                                         │ HTTP(S) to the ALB origin
                         ┌───────────────▼──────────────────────────────┐
                         │ ALB  public subnets, 2 AZs, health /readyz    │
                         └───────┬───────────────────────┬──────────────┘
                     /api/*      │                       │  /*
                    ┌────────────▼─────────┐   ┌─────────▼───────────┐
                    │ ECS service: api      │   │ ECS service: web    │
                    │ Fargate, private sub. │   │ Fargate, nginx      │
                    │ + ADOT sidecar        │   └─────────────────────┘
                    └───┬────────┬────────┬─┘
        private subnets │        │        │
              ┌─────────▼──┐  ┌──▼─────┐  └──▶ S3 (documents / web / logs)
              │ RDS  PG16  │  │ Redis  │       Secrets Manager + SSM
              │ pgvector   │  │ 7.x    │       CloudWatch Logs / X-Ray
              │ sg ← app-sg│  │sg←app  │       VPC endpoints (ECR, SM, Logs, S3)
              └────────────┘  └────────┘
```

- **CloudFront** terminates TLS at the edge and caches the content-hashed SPA
  bundle. `/api/*` is forwarded to the ALB with caching disabled, because API
  responses are authenticated and tenant-scoped — caching one would be a
  data-leak defect. CloudFront is **off in dev and on in prod**.
- **ALB** lives in the public subnets and is the only internet-facing component.
  It routes `/api/*`, `/docs`, `/openapi.json` to the API target group and
  everything else to the web target group.
- **ECS/Fargate** services run in the private subnets in `awsvpc` mode, so each
  task gets its own ENI and security group. The API task definition runs the API
  container plus an **ADOT collector sidecar** (`essential = false`, so a
  telemetry outage does not become an availability outage).
- **RDS PostgreSQL 16 + pgvector** and **ElastiCache Redis** occupy the private
  subnets, accept traffic **only** from the application security group, and have
  no public path.

### Why ECS/Fargate and not EKS

The reasoned trade-off is in
[`docs/architecture/deployment.md` §5](../../../docs/architecture/deployment.md).
This workload is stateless HTTP services plus an optional worker; the binding
constraint is attention, not compute. A Kubernetes control plane, ingress layer
and node-group upgrade cadence would add operational surface for no capability
the system uses. The trigger to revisit is explicit: more than roughly a dozen
services, a service-mesh or custom-operator requirement, or a hard multi-cloud
requirement.

---

## 2. Layout

```text
infra/terraform/
  README.md                     this file
  versions.tf                   canonical Terraform + provider constraints (>= 1.5.0, aws ~> 5.60, random ~> 3.6)
  modules/
    network/                    VPC, 2 public + 2 private subnets, NAT, route tables, VPC endpoints
    security/                   security groups by reference + execution/task IAM roles
    database/                   RDS PostgreSQL 16 + pgvector, subnet group, parameter group, generated credentials
    cache/                      ElastiCache Redis, subnet group, generated AUTH token
    storage/                    S3 documents / web / logs buckets with policy, versioning, encryption, lifecycle
    secrets/                    JWT key + provider secret entries + SSM configuration parameters
    ecs/                        cluster, ECR, log groups, task definitions, services, ALB, target groups, autoscaling
    observability/              CloudWatch alarms + dashboard + the ADOT collector configuration
    edge/                       CloudFront distribution and the web bucket's OAC policy
  environments/
    dev/                        main.tf, variables.tf, outputs.tf, terraform.tfvars.example, backend.tf
    prod/                       same shape, different sizing and retention
```

Each module has `variables.tf` (every variable typed and described) and
`outputs.tf`. Small valid sets carry `validation` blocks: `environment`,
instance-class patterns, region shape, storage types, retention values, CPU
architectures and log-retention values.

Every taggable resource inherits `Project`, `Environment` and `ManagedBy =
"terraform"` from the provider-level `default_tags` block in each environment
root. That is what makes a cost report possible: without it, a bill cannot be
attributed to an environment or a project.

---

## 3. The two database roles (this is load-bearing)

PostgreSQL ignores Row-Level Security for a superuser and for any role with
`BYPASSRLS`, silently. An application that connects as the owner runs every
query under policies that do not apply, and the tenant-isolation tests pass for
the wrong reason.

So there are two roles, and Terraform creates the credentials for both but only
one of them can bypass anything:

| Role | Who uses it | Attributes | What it can do |
|------|-------------|------------|----------------|
| `coursellm_owner` (RDS master user, generated password) | the **one-off migration task** | member of `rds_superuser` | create the schema, run `alembic upgrade head`, `CREATE EXTENSION vector`, run `scripts/bootstrap_db.sql` |
| `coursellm_app` (generated password) | the **API service task** | `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS` | DML on tables, `USAGE/SELECT` on sequences, `EXECUTE` on the single auth lookup function — nothing else |

`scripts/bootstrap_db.sql` creates `coursellm_app` with those attributes and is
idempotent: re-running it re-asserts `NOSUPERUSER NOBYPASSRLS` rather than
trusting an earlier state. The application task connects as `coursellm_app`
only; `DATABASE_URL` in the task definition resolves to a Secrets Manager ARN
whose value uses that user.

That is what makes RLS **enforced** rather than assumed:
`docs/architecture/security.md` §7.3 explains why `FORCE ROW LEVEL SECURITY`
matters (table owners otherwise bypass policies) and §7.4 explains why the
tenant GUC is set with `SET LOCAL` per transaction (a session-scoped `SET`
leaks the tenant across a multiplexed pool connection).

Terraform does not create the application role itself — that would mean running
SQL from a plan. It creates the role's credentials in Secrets Manager; the
migration task runs the SQL.

---

## 4. Migrations run once, before the service rolls

**Migrations run as a one-off ECS task before the service is updated. They never
run in the API container entrypoint.**

A rolling deployment starts two new replicas concurrently. If each ran
`alembic upgrade head` at boot they would read the same current revision and
race to apply the same DDL; Alembic's version table is not a distributed lock,
so "it usually works" is a coincidence of timing, and the failure mode is a
version table that disagrees with the schema. Running the migration once, as a
separate task the pipeline waits on, makes the ordering explicit and the failure
visible **before any traffic moves**.

The pipeline order (deployment.md §7):

1. build and push the image tagged `:<git-sha>`
2. `terraform apply` targeted at task definitions
3. **run the one-off `migrate` task → blocks until it exits**
4. update the `api` service to the new task definition
5. update the `web` service
6. wait for stability; the deployment circuit breaker rolls back on failure

The `migrate` task definition (`modules/ecs/services.tf`) uses the **same API
image** as the service, so the migration code and the application code are
provably the same commit, and overrides the image entrypoint with a shell
script that:

1. `alembic upgrade head` as the schema owner (`OWNER_DATABASE_URL`),
2. `psql -f /app/scripts/bootstrap_db.sql` as the schema owner, which creates
   `coursellm_app` with `NOSUPERUSER NOBYPASSRLS`,
3. sets that role's password from the app-role secret via a `psql -v` variable
   (never on the command line, never in the task definition).

Ordering matters: migrations first, then bootstrap, so the grants cover the
tables the migration just created and `auth_login_lookup` exists when the script
tightens its `EXECUTE` grant. `ALTER DEFAULT PRIVILEGES` covers tables a later
migration adds.

Terraform only defines the task definition. **Running it is a pipeline step**,
deliberately outside Terraform, so a plan can never trigger a migration.

---

## 5. Networking and security groups

| Element | What this configuration creates |
|---------|--------------------------------|
| VPC | `/16`, DNS hostnames and support on |
| Public subnets | one per AZ (`/20` derived from the VPC CIDR), `map_public_ip_on_launch = false`; ALB and NAT only |
| Private subnets | one per AZ; ECS tasks, RDS, ElastiCache |
| NAT gateway | **dev: one**; **prod: one per AZ** (see cost consequences below) |
| Internet gateway | attached to the VPC, used by the public route table |
| VPC endpoints | S3 gateway endpoint on the private route tables, plus interface endpoints for ECR (`ecr.api`, `ecr.dkr`), Secrets Manager and CloudWatch Logs — the four names in deployment.md §6. Each additional interface endpoint is billed per endpoint-hour; add more only with the cost in mind. |
| Flow logs | **not created** (see "Hardening not done here") |

Security groups are referenced **by group id, never by CIDR on the data tier**:

```text
alb-sg   ingress 80/443 from var.alb_ingress_cidrs (0.0.0.0/0 by default)
         egress  → app-sg:8000, web-sg:8080            (egress = [] then explicit rules)
app-sg   ingress 8000 from alb-sg only                (egress left at the AWS default)
web-sg   ingress 8080 from alb-sg only
rds-sg   ingress 5432 from app-sg only, egress = []   ← no CIDR ingress at all
redis-sg ingress 6379 from app-sg only, egress = []   ← no CIDR ingress at all
vpce-sg  ingress 443 from the VPC CIDR, egress = []
```

The data-tier groups have **no `0.0.0.0/0` ingress rule**, and their egress is
empty: a database has no reason to open an outbound connection. The `app-sg`
keeps the AWS default egress because the API must reach LLM providers through
the NAT and the provider address ranges are not stable — ingress is the boundary
that matters here, and it is the ALB group alone.

---

## 6. The data tier

### RDS PostgreSQL with pgvector

**Engine version selected: `16`** (i.e. the current RDS PostgreSQL 16 minor),
parameter group family **`postgres16`**.

**How pgvector support was verified.** The RDS for PostgreSQL extension tables
were read from AWS's own release-notes page,
[Extension versions for Amazon RDS for PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html),
section "Extensions supported for RDS for PostgreSQL 16". In that table the
`pgvector` row is populated for **every** listed 16.x minor — `16.1` (pgvector
0.4.4) through `16.15` (pgvector 0.8.2) — so any PostgreSQL 16 minor on RDS
supports `CREATE EXTENSION vector`. The migration already runs
`CREATE EXTENSION IF NOT EXISTS vector`, so engine support is the only
requirement; nothing else has to be preloaded.

The parameter group is custom (`postgres16`, `rds.force_ssl = 1`,
`log_min_duration_statement = 1000`). pgvector needs no
`shared_preload_libraries` entry, so the custom group does not have to add one.
TLS is enforced by the server; the generated `DATABASE_URL` asks for it
(`?ssl=require`).

Other settings:

| Setting | dev | prod |
|---------|-----|------|
| instance class | `db.t4g.micro` | `db.t4g.medium` |
| Multi-AZ | off | **on** |
| storage | gp3, 20 GiB, no autoscaling | gp3, 100 GiB → 500 GiB |
| `storage_encrypted` | `true` (AWS-managed key) | `true` |
| automated backups | 1 day | 14 days |
| `deletion_protection` | off | **on** |
| `skip_final_snapshot` | `true` | **`false`** (named final snapshot) |
| `publicly_accessible` | **`false`** | **`false`** |
| Performance Insights | on (7-day free tier) | on (7-day free tier) |
| log exports | postgresql, upgrade | postgresql, upgrade |

`apply_immediately = false`: reading a plan must not mutate a running database.

**Credentials.** `random_password` generates the master and application-role
passwords (32 alphanumeric characters — no specials, because the values are
embedded in URLs and passed through `psql`). Both are written to Secrets
Manager: `${project}-${env}/rds/master` (JSON with `OWNER_DATABASE_URL`) and
`${project}-${env}/rds/app` (JSON with `DATABASE_URL`, assembled inside the
secret). **Never a literal in a file or in the task definition.** The task
definition references the JSON key:
`valueFrom = "${app_secret_arn}:DATABASE_URL::"`.

Because Terraform manages the passwords, their values exist in **Terraform
state**. That is unavoidable for any Secrets Manager value Terraform writes. It
is why the backend block is commented out with instructions (see §10): state
must be remote, encrypted and versioned, and `*.tfstate` is gitignored.

### ElastiCache Redis

`cache.t4g.micro` (dev) / `cache.t4g.small` with 2 nodes, automatic failover and
Multi-AZ (prod), in the private subnets, reachable only from `app-sg`.
Encryption at rest is always on. Transit encryption is on in both environments,
so the client must use `rediss://` with the generated AUTH token, which is
assembled into a `REDIS_URL` secret (same pattern as `DATABASE_URL`).

The cache is not durable state. Losing it degrades latency and fails rate
limiting **open** (security.md §11); it does not lose documents.

---

## 7. Storage

Three buckets, all with public access blocked, ACLs disabled
(`BucketOwnerEnforced`), encryption at rest, TLS enforced by bucket policy, a
lifecycle rule that aborts incomplete multipart uploads, and explicit
expiration so a bucket cannot bill forever:

| Bucket | Contents | dev | prod |
|--------|----------|-----|------|
| `documents` | raw uploads and extracted artefacts | no versioning, no expiry | versioning, transition to STANDARD_IA at 30 days |
| `web` | the built client, read by CloudFront through an origin access control | no versioning | versioning |
| `logs` | S3 server access logs and exports | expire at 30 days | expire at 365 days |

The web bucket's policy is owned by `modules/edge`, because only that module
knows the distribution ARN the policy must name. It is never public.

> **Honest gap.** The application currently implements only a **local** object
> store (`apps/api/src/coursellm/storage/local.py`); there is no S3 adapter in
> the code today. The buckets, the bucket policy and the prefix-scoped IAM grant
> are provisioned ahead of the adapter. The task role grant
> (`s3:GetObject/PutObject/DeleteObject` on `${documents_bucket}/tenants/*`
> plus a prefix-conditioned `ListBucket`) is therefore real infrastructure that
> the current code does not yet exercise.

---

## 8. Least-privilege IAM

Two roles, and the split is the point:

**Execution role** — used by the ECS agent *before* the container starts:
- `ecr:GetAuthorizationToken` (resource `*`: the action has no resource-level
  permissions — the only `*` resource on the pull path, and it is a named
  action, not `ecr:*`)
- `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage`,
  `ecr:GetDownloadUrlForLayer` on `arn:aws:ecr:<region>:<account>:repository/<prefix>-*`
- `logs:CreateLogStream`, `logs:PutLogEvents` on `/ecs/<prefix>/*`
- `secretsmanager:GetSecretValue` on the specific secret ARNs
- `ssm:GetParameter`, `ssm:GetParameters` on the specific parameter ARNs
- `kms:Decrypt` on customer keys only if any are supplied

**Task role** — what the running application may do:
- `secretsmanager:GetSecretValue` on its own secret ARNs
- `s3:GetObject/PutObject/DeleteObject/AbortMultipartUpload` on
  `${documents_bucket}/tenants/*` only
- `s3:ListBucket` on the documents bucket, conditioned on `s3:prefix`
- `logs:CreateLogStream`, `logs:PutLogEvents`
- `xray:PutTraceSegments`, `xray:PutTelemetryRecords` (no resource-level
  permissions exist for these actions)

A reviewer can read either policy and say what it **cannot** do: it cannot touch
another bucket, another bucket prefix, another secret, another AWS service, or
any IAM resource. There are no `*` actions and no `AdministratorAccess`; the
only wildcard resources are the two AWS APIs that do not support resource-level
permissions, each with a comment saying so.

`enable_execute_command` (ECS Exec) is **off**: it is an interactive shell into
a container holding tenant data.

---

## 9. Health checks: readiness gates traffic, liveness restarts

The ALB target group for the API health-checks **`/readyz`**, not `/healthz`.

- `/readyz` asserts the database is reachable and `alembic current == head`.
  **Readiness is what should gate traffic**: a task that cannot reach its
  database must not receive requests.
- `/healthz` asserts only that the process is up, and is what the *container*
  health check uses (matching `docker/api.Dockerfile`). **Liveness is what
  restarts a bad container.**

A load balancer pointed at liveness routes traffic to a process that cannot
serve it; a liveness probe pointed at readiness restarts a healthy process every
time a dependency hiccups. The two endpoints answer different questions, so the
configuration uses different ones in the two places.

The web target group checks `/healthz` because the nginx image serves only that
liveness endpoint; the web container has no dependencies to be ready for.

`deregistration_delay` is 120 s and the ALB `idle_timeout` is 300 s, because a
short deregistration delay kills a streamed tutor answer mid-token.

---

## 10. State, secrets and configuration

### State

**One state per environment, never one state file for both.** `dev` and `prod`
are separate root modules; `backend.tf` in each carries a **commented-out** S3
backend block with the exact bootstrap commands, so a reviewer can run
`terraform init -backend=false && terraform validate` with no bucket and no
credentials, which is what a local review actually needs.

The commented block documents the consequence: the generated database passwords
live in state, so state must be a remote backend with encryption and
versioning, access must be restricted, and `*.tfstate` must never be committed
(it is gitignored, along with `*.tfvars` and `.terraform/`).

### Secrets Manager vs SSM Parameter Store

| Kind | Store | Examples in this configuration |
|------|-------|-------------------------------|
| Credentials | Secrets Manager | RDS master + application-role credentials, assembled `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY` (the JWT signing key), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY` |
| Non-secret config | SSM Parameter Store | `LOG_LEVEL`, `DB_POOL_SIZE`, `RERANK_ENABLED`, `RETRIEVAL_TOP_K_PER_RETRIEVER`, `RRF_K`, `RERANK_TOP_K`, `CONTEXT_TOKEN_BUDGET`, `EMBEDDING_MODEL`, `MAX_OUTPUT_TOKENS`, `OTEL_SAMPLE_RATIO`, `LANGSMITH_ENABLED`, `CORS_ALLOWED_ORIGINS`, plus the ADOT collector config |

Putting a feature flag in Secrets Manager hides it; putting a provider key in
Parameter Store plaintext leaks it. ECS resolves both through the `secrets`
block (`valueFrom`), so neither appears as a plaintext `environment` value.

`DATABASE_URL` is **assembled inside the secret** from the generated password
and the instance endpoint, and the task definition references it by ARN and JSON
key — it is never an `environment` value.

### Rotation

- **JWT signing key**: rotating it invalidates every live token unless the
  application verifies with the old key and signs with the new one during a
  two-key window. That is a code requirement, recorded here because it
  constrains the procedure. Rotate by writing a new secret value and starting a
  new task definition revision; the previous revision stays available, so the
  rotation is rollback-able.
- **Provider keys**: rotate with the provider, write the new value, roll a new
  revision.
- **Database credentials**: `random_password` changes only if the resource is
  replaced. Rotating the *application role* password is `ALTER ROLE` plus a new
  secret version plus a new task revision.
- **No automatic rotation Lambda is created** — that is a separate deliverable
  (a Lambda plus its VPC attachment). The secrets are created with a
  description stating the rotation rule, and this README states the procedure
  rather than half-implementing it.

Provider secrets are created with the placeholder `REPLACE_ME_AT_DEPLOY` and
`lifecycle { ignore_changes = [secret_string] }`, so a later apply cannot
overwrite an operator's real value with the placeholder. The application refuses
to start in prod on a placeholder (`security.md` §10), which is the intended
failure: a crash, not a running service with a predictable credential.

---

## 11. Observability

- **Log groups**: one per service (`/ecs/<prefix>/{api,web,migrate,adot}`) with
  an explicit retention (14 days dev, 90 days prod). A log group with no
  retention bills forever.
- **Alarms** (all publish to an SNS topic; the email subscription must be
  confirmed before it delivers anything):
  - ALB 5xx **rate**, computed with metric math
    (`IF(requests > 0, (target5xx + elb5xx) / requests * 100, 0)`) rather than a
    raw count, because a fixed 5xx count means something different at 10 and at
    10,000 requests per minute
  - API target-response-time **p99** (8 s, the interactive budget)
  - API unhealthy host count
  - RDS CPU, connections and free storage
  - ElastiCache CPU and memory
  - estimated charges (billing), **created only when the provider region is
    `us-east-1`**, because that is the only region where the `AWS/Billing`
    `EstimatedCharges` metric exists
- **Dashboard**: one CloudWatch dashboard covering the panels from
  `docs/architecture/observability.md` §8 that map to AWS-native metrics.
  Panels with no CloudWatch-native source (injection verdicts, retrieval
  candidate counts, degradation reasons, evaluation score) are emitted by the
  application as OTel/EMF metrics and are **not** scraped here.
- **ADOT collector**: the configuration is stored in SSM Parameter Store
  (`/<project>/<env>/adot/config`) and injected into the sidecar as
  `AOT_CONFIG_CONTENT`, so the exporter can change without a new task
  definition. The default exports traces to X-Ray and metrics as CloudWatch EMF.
  The sidecar is non-essential.

**The dashboard and the alarms do not exist.** They are specified, not deployed.

---

## 12. Scaling and rollback

- API autoscaling: two target-tracking policies on the ECS service — **ALB
  `RequestCountPerTarget`** (primary; request count tracks actual load, CPU lags
  on an I/O-bound async service) and **average CPU** (secondary). Scale-out
  cooldown 60 s, scale-in 300 s: scale-in is deliberately slower.
- Web autoscaling: average CPU.
- `lifecycle { ignore_changes = [desired_count] }` on both services so
  autoscaling owns the count and a plan does not drag it back.
- Rolling deployment with the **circuit breaker enabled and rollback on**: a
  deployment that cannot reach steady state rolls itself back to the previous
  task definition. `deployment_minimum_healthy_percent = 100`,
  `deployment_maximum_percent = 200`.
- Images are tagged `:<git-sha>` in **IMMUTABLE** ECR repositories; `latest` is
  never referenced by a task definition. A rollback names a fixed artefact.
- Database migration reversibility: **expand/contract**. Migrations are additive
  and backward-compatible with the previous revision during the rollout window,
  which is exactly what makes "migrate before the service update" correct.
  `alembic downgrade` is a schema-shape tool, not a data-recovery mechanism.

**Note.** Connection-pool arithmetic is not solved by this configuration. With a
pool of 20 per task and 10 tasks that is 200 server connections before
migrations, the worker and any admin session; the intended production shape is a
pool of roughly 5–10 per task behind RDS Proxy, with `DB_POOL_SIZE` supplied
from configuration (it is an SSM parameter here). **RDS Proxy is not created by
this configuration** — it is a documented next step, and the
`rds_connections_threshold` alarm is the signal that says when it is needed.

---

## 13. Cost drivers (estimates, not a bill)

> **These are estimates from published AWS list prices, not a bill anyone
> observed.** No resource in this repository has been created, so there is no
> invoice to read. Unit prices below were read from the public **AWS Price List
> API** bulk offer files for **us-east-1** (`pricing.us-east-1.amazonaws.com`)
> on **2025-09-25**; prices change and vary by region, and the offer files
> themselves say they are informational. **No single monthly total is asserted
> as verified** — the "arithmetic" column is my multiplication of the verified
> unit prices by the stated assumptions, and it excludes the lines marked
> unverified.

### Unit prices read from the AWS Price List API (us-east-1, 2025-09-25)

| Driver | Billing unit | Verified list price |
|--------|--------------|---------------------|
| Fargate (Linux/x86) | per vCPU-hour | $0.04048 |
| Fargate (Linux/x86) | per GB-hour | $0.004445 |
| RDS `db.t4g.micro` PostgreSQL | instance-hour, Single-AZ | $0.016 |
| RDS `db.t4g.micro` PostgreSQL | instance-hour, Multi-AZ | $0.032 |
| RDS `db.t4g.medium` PostgreSQL | instance-hour, Multi-AZ | $0.129 |
| RDS gp3 storage | GB-month | $0.115 (Single-AZ); $0.23 (Multi-AZ) |
| ElastiCache `cache.t4g.micro` Redis | node-hour | $0.016 |
| ElastiCache `cache.t4g.small` Redis | node-hour | $0.032 |
| NAT gateway | gateway-hour | $0.045 |
| NAT gateway | GB processed | $0.045 |
| Public IPv4 address (the NAT elastic IP) | address-hour | $0.005 |
| Application Load Balancer | load-balancer-hour | $0.0225 |
| Application Load Balancer | LCU-hour | $0.008 |
| S3 Standard | GB-month | $0.023 |
| CloudWatch Logs | GB ingested | $0.50 |
| CloudWatch Logs | GB-month stored | $0.03 |
| CloudWatch alarm | alarm-month (standard) | $0.10 |
| Secrets Manager | secret-month | $0.40 |
| Secrets Manager | API request | $0.000005 (=$0.05 / 10,000) |
| ECR | GB-month | $0.10 |
| VPC interface endpoint | endpoint-hour | $0.01 |
| VPC interface endpoint | GB processed | $0.01 |
| X-Ray | indexed span | $0.00000075 |

**Not verified in this environment** (the offer files I could read did not
contain them, so they are deliberately excluded from the arithmetic):
CloudFront data-transfer and request pricing, the CloudWatch dashboard charge,
RDS backup storage beyond the provisioned volume, and LLM token prices. Treat
those lines as additional.

### Driver table with explicit assumptions

Assumptions common to both columns: **730 hours in a month**; the numbers are
for **steady state**, 24×7, with no free-tier consumption and no commitment
discounts (no Savings Plans, no Reserved Instances); every task and node is
assumed to run for the whole month.

| Driver | dev assumption | dev arithmetic | prod assumption | prod arithmetic |
|--------|----------------|----------------|-----------------|-----------------|
| Fargate API | 1 task × (0.5 vCPU, 1 GiB) | $18.02 | 2 tasks × (1 vCPU, 2 GiB) | $72.08 |
| Fargate web | 1 task × (0.25 vCPU, 0.5 GiB) | $9.01 | 2 tasks × (0.5 vCPU, 1 GiB) | $36.04 |
| Fargate migrate | occasional one-off | < $0.10 | occasional one-off | < $0.10 |
| RDS instance | `db.t4g.micro` Single-AZ | $11.68 | `db.t4g.medium` Multi-AZ | $94.17 |
| RDS storage | 20 GiB gp3 | $2.30 | 100 GiB gp3 Multi-AZ | $23.00 |
| RDS backups | 1 day | within the free backup allocation | 14 days | **unverified**, excluded |
| ElastiCache | 1 × `cache.t4g.micro` | $11.68 | 2 × `cache.t4g.small` | $46.72 |
| NAT gateway | 1 gateway + its EIP | $36.50 | 2 gateways + EIPs | $73.00 |
| NAT data processed | ~10 GB | $0.45 | ~100 GB | $4.50 |
| ALB | 1 ALB, ~1 LCU average | $22.27 | 1 ALB, ~10 LCU average | $74.83 |
| Interface VPC endpoints | 4 endpoints | $29.20 | 4 endpoints | $29.20 |
| S3 | ~5 GB Standard | $0.12 | ~50 GB Standard | $1.15 |
| Secrets Manager | 7 secrets | $2.80 | 7 secrets | $2.80 |
| CloudWatch Logs | ~2 GB ingest + storage | $1.06 | ~20 GB ingest + storage | $10.60 |
| CloudWatch alarms | 8 alarms | $0.80 | 9 alarms | $0.90 |
| ECR | ~2 GB | $0.20 | ~5 GB | $0.50 |
| CloudFront | not created in dev | $0.00 | price **unverified**, excluded | — |
| LLM tokens | off unless evaluation runs | not estimated | **expected to dominate the variable cost** | not estimated |

**Arithmetic subtotal: roughly $145/month for dev and roughly $460/month for
prod, before the excluded lines.** Stated as a range, not a quote: it is
`unit price × stated assumption`, nothing about it was measured, and the prod
figure moves a long way with autoscaling (the API can reach 10 tasks) and with
LCU.

### What actually dominates, honestly

- **In dev the fixed network layer is the bill.** NAT + four interface VPC
  endpoints + the ALB is about $88 of the ~$145, and none of it shrinks when
  traffic is zero. They are billed per hour even when idle. The single biggest
  dev saving is dropping the NAT gateway and interface endpoints in favour of a
  public task, or accepting a short-lived environment that is destroyed between
  uses — which is why `deletion_protection` and `skip_final_snapshot` are
  **off in dev**.
- **Prod adds availability, and availability is a fixed cost.** The second NAT
  gateway, the Multi-AZ standby, the second cache node and the second API task
  are all on the "survive one AZ" bill. Each is a deliberate availability
  purchase, not an accident.
- **The variable cost that can actually run away is LLM tokens.** It is not
  estimable without a usage profile and it is not controlled by this
  configuration; it is controlled by `MAX_OUTPUT_TOKENS`, `CONTEXT_TOKEN_BUDGET`,
  model routing and the per-tenant budget in `security.md` §11. The billing
  alarm is the backstop, and it only works in `us-east-1`.
- **A small always-on saving:** every log group has a retention period, every
  bucket has a lifecycle rule, and ECR expires untagged images after 14 days and
  keeps only the most recent 30 tags. Those are the three places a bill grows
  silently.

---

## 14. How to run it (validating and planning only)

```bash
# Terraform is not vendored here. If it is not installed:
#   brew install terraform
# or download the official binary from releases.hashicorp.com.

# 1. Format (must be clean)
terraform -chdir=infra/terraform fmt -recursive -check

# 2. Validate both environments (no credentials, no backend needed)
terraform -chdir=infra/terraform/environments/dev  init -backend=false && \
terraform -chdir=infra/terraform/environments/dev  validate
terraform -chdir=infra/terraform/environments/prod init -backend=false && \
terraform -chdir=infra/terraform/environments/prod validate

# 3. Plan the dev environment (needs AWS credentials; creates nothing)
terraform -chdir=infra/terraform/environments/dev plan -var-file=terraform.tfvars.example
```

The same three steps are the `tf-fmt`, `tf-validate` and `tf-plan` Makefile
targets; `tf-plan` points at `terraform.tfvars.example`, and `terraform.tfvars`
itself is gitignored.

**Do not run `terraform apply`.** Nothing in this PR has been applied and the
configuration is not approved for an account.

### This repository's own verification status

`terraform fmt -recursive -check`, `terraform init -backend=false` and
`terraform validate` were run and passed in both environments. `terraform plan`
was attempted and **could not complete because no AWS credentials exist in this
environment** (`Error: No valid credential sources found`); it planned the
provider-independent resources (the generated passwords and the bucket-name
suffix) and then stopped at the provider. That partial plan is evidence the
configuration parses and the dependency graph is coherent, and it is **not** a
claim that a plan succeeded.

---

## 15. Hardening and features deliberately not present

Stated plainly, because a document that lists only what exists is fiction:

- **No AWS WAF.** The `web_acl_id` input exists on the CloudFront module but is
  empty by default. WAF is a separate, priced resource.
- **No CloudFront managed prefix list on the ALB ingress.** The ALB accepts
  80/443 from `0.0.0.0/0` by default. Narrowing it to
  `com.amazonaws.global.cloudfront.origin-facing` is a one-variable change
  (`alb_ingress_cidrs` is not enough; it needs a prefix-list input, which is a
  follow-up).
- **No RDS Proxy.** See §12.
- **No VPC flow logs, no GuardDuty, no Config rules, no Security Hub.** These
  are account-level services, not per-application infrastructure.
- **No CMK.** Encryption at rest uses the AWS-managed service keys
  (`aws/rds`, `aws/secretsmanager`, `aws/elasticache`) and SSE-S3. A
  customer-managed KMS key is accepted as an optional input on the database,
  cache and storage modules and costs about $1/key/month.
- **No automatic secret rotation Lambda.**
- **No Route 53 records, no ACM certificates, no hosted zone.** Certificates are
  inputs (`alb_certificate_arn`, `cloudfront_acm_certificate_arn`) because
  issuing one requires a domain and a validation record that this configuration
  does not own. With no certificate the ALB is HTTP-only, which is acceptable
  only behind CloudFront or in dev with synthetic data.
- **The S3 object-store adapter is not implemented** in the application; see §7.
- **The migration task is not run by Terraform.** Running it is a pipeline step.
- **No CI wiring.** Nothing in this PR has been added to a GitHub Actions
  workflow; the Makefile targets are the entry points.

---

## 16. What is NOT deployed

- There is **no live AWS deployment**. No account, VPC, ECS service, RDS
  instance, ElastiCache node, S3 bucket, CloudFront distribution, secret,
  dashboard or alarm exists as part of this repository. **No AWS bill is being
  generated.**
- This configuration is **validated, not applied**. Every "output" in
  `environments/*/outputs.tf` is a reference to a resource that *would* be
  created; none of them is a value read from a running system.
- **Every cost number above is an estimate from published list prices with the
  stated assumptions.** None was measured. The unit prices were read from the
  AWS Price List API on the date noted; the month totals are arithmetic, not
  observations.
- **No performance, latency or throughput number is claimed.** The p99 alarm
  threshold is a target, not a measurement.
- **No compliance certification is claimed.** Nothing here asserts SOC 2,
  ISO 27001, HIPAA or GDPR conformance; see
  [`docs/architecture/security.md`](../../../docs/architecture/security.md) §13.
