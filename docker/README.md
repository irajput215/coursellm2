# CourseLLM containers

Docker packaging for the API and the web client, plus the local compose stack
that brings the whole system up from a clean checkout.

```bash
cp .env.example .env     # then set a real SECRET_KEY
make up                  # docker compose up --build -d
```

* API → <http://localhost:8000/docs>
* Web → <http://localhost:5173>
* Postgres → `127.0.0.1:5432`, Redis → `127.0.0.1:6379`

**There is no Vercel.** The web image builds `apps/web` to static assets and
serves them with nginx; the same `dist/` directory is what production syncs to
S3 and serves through CloudFront, with `/api/*` routed to the API origin
(`docs/architecture/deployment.md` §2a). nginx here is the local/self-hosted
delivery mechanism for exactly those assets, not a second application.

## Files

| File | Purpose |
|------|---------|
| `docker/api.Dockerfile` | Multi-stage, non-root API image (also used by `migrate`) |
| `docker/web.Dockerfile` | Builds `apps/web` to static assets, serves them with nginx |
| `docker/nginx.conf` | nginx `server {}` block: SPA fallback, security headers, cache policy |
| `docker/otel-collector.yaml` | Optional OTLP collector config (compose `otel` profile) |
| `docker-compose.yml` | `postgres` + `redis` + `migrate` + `api` + `web` (+ optional `otel-collector`) |

## Services

| Service | Image | Published | Healthcheck | Restart |
|---------|-------|-----------|-------------|---------|
| `postgres` | `pgvector/pgvector:pg17` | `127.0.0.1:5432` | `pg_isready`, 5 s / 3 s / 10 | `unless-stopped` |
| `redis` | `redis:7-alpine` | `127.0.0.1:6379` | `redis-cli ping`, 5 s / 3 s / 10 | `unless-stopped` |
| `migrate` | `coursellm-api:local` (one-shot) | — | none (exits 0) | `no` |
| `api` | `coursellm-api:local` | `${API_PORT:-8000}:8000` | `/readyz` **and** pgvector assertion, 10 s / 5 s / 3 | `unless-stopped` |
| `web` | `coursellm-web:local` | `${WEB_PORT:-5173}:8080` | `GET /healthz` (nginx), 10 s / 3 s / 3 | `unless-stopped` |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.128.0` | `127.0.0.1:4317/4318/8889/13133` | none — distroless, no shell | `unless-stopped` |

Postgres, Redis and the collector are bound to loopback; only `api` and `web`
are reachable from outside the host. Postgres uses `POSTGRES_HOST_AUTH_METHOD=trust`
because that is the local posture `scripts/bootstrap_db.sql` documents (the app
role is created with no password). It is a workstation setting and is called out
as such in the compose file and in the bootstrap script.

## Startup order (and why it makes RLS real)

```text
postgres (healthy) ─┐
redis    (started) ─┼─▶ migrate (owner, one-shot, must exit 0) ──▶ api ──▶ web
                    │      1. CREATE EXTENSION IF NOT EXISTS vector
                    │      2. assert the extension exists
                    │      3. alembic upgrade head
                    │      4. psql -f scripts/bootstrap_db.sql
                    └─▶ api also waits for postgres to be healthy
```

`api` uses `depends_on: migrate: condition: service_completed_successfully`.
It waits for the migration task to **exit 0**, not merely for Postgres to accept
connections. The reason is in `docs/architecture/deployment.md` §7: under a
rolling deploy two API replicas start at once, and if each ran
`alembic upgrade head` at boot they would race — both read the same current
revision, both attempt the same DDL, and Alembic's version table is not a
distributed lock. A one-shot task makes the ordering explicit and the failure
visible before any traffic moves.

### Why the two-role order is load-bearing

The same reasoning as `make db-setup` locally:

1. **`migrate` connects as the owner** (`ALEMBIC_DATABASE_URL`, Postgres user
   `POSTGRES_USER`, the image's superuser). It applies the migrations, which
   create the tables and run `ALTER TABLE … ENABLE ROW LEVEL SECURITY` **and**
   `FORCE ROW LEVEL SECURITY` with a `tenant_isolation` policy.
2. **Then, still as owner, it runs `scripts/bootstrap_db.sql`.** The tables now
   exist, so `GRANT … ON ALL TABLES` and `ALTER DEFAULT PRIVILEGES …` (which
   only affect objects created by the granting role, i.e. the owner that just ran
   the migrations) cover them. The script creates
   `coursellm_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`.
3. **`api` connects as `coursellm_app`** (`DATABASE_URL`), which is neither a
   superuser nor `BYPASSRLS` — and not the table owner either. PostgreSQL
   therefore evaluates the policies for it.

Run it in the other order and step 2 fails on tables that do not exist yet;
skip step 2 and the API has no role to connect as; run the API as the owner and
PostgreSQL ignores every policy **silently**, so tenant isolation looks present
while other tenants' rows come back. That silent failure is why
`_verify_tenant_isolation` refuses to start in production for a role that can
bypass RLS, and why the acceptance check is `db-inspect` rather than "RLS is
enabled":

```bash
docker compose exec api python -m coursellm.cli db-inspect
# connected as     : coursellm_app
# superuser        : False
# bypasses RLS     : False
# tenant isolation : ENFORCED by Row-Level Security
```

### pgvector

Postgres is the `pgvector/pgvector:pg17` image so the extension **can** be
installed. `migrate` installs it explicitly and then asserts it is present,
failing the one-shot task (and therefore blocking `api`) if it is not:

```sh
psql -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS vector'
psql -tAc "SELECT 1 FROM pg_extension WHERE extname='vector'" | grep -q 1
```

The compose `api` healthcheck additionally runs that same assertion as the
application role and requires `/readyz` to return 200, so the container is
reported **unhealthy** rather than silently serving without the extension. The
image-level `HEALTHCHECK` is liveness-only; see below.

## Health endpoints

| Probe | Endpoint | Where it is wired | What it asserts |
|-------|----------|-------------------|-----------------|
| Liveness | `GET /healthz` | image `HEALTHCHECK` | The process is up. No I/O, no dependencies. |
| Readiness | `GET /readyz` | compose `api` healthcheck | The database check (registered as critical) passes; 503 when it does not. |
| Web liveness | `GET /healthz` | image + compose | nginx is answering; not the API's endpoint. |

The division is deliberate. A dependency-aware check inside the image
`HEALTHCHECK` makes Docker restart a perfectly healthy process every time the
database hiccups — a restart loop caused by a transient dependency failure.
`/readyz` is the signal an orchestrator acts on (remove from the load balancer,
do not kill), so that is where the dependency check lives. Docker does not
restart a container merely for being unhealthy, so using `/readyz` as the
compose healthcheck cannot cause a loop either.

## Image contents and size

### API image

`builder` (`python:3.13-slim`) installs `build-essential` and `libpq-dev`,
creates `/opt/venv`, and installs the package plus the core dependencies and
`docx`. `runtime` (`python:3.13-slim`) copies **only** `/opt/venv`, the source
tree, the Alembic environment, `prompts/` and `scripts/bootstrap_db.sql`. It has
no compiler and no headers. It does install `postgresql-client` (`psql`), which
is needed by the one-shot `migrate` service and by the readiness assertion; that
is a client, not a build toolchain.

The package is installed **editable** at `/app/apps/api/src`. That is not an
accident: `coursellm.core.config` derives `REPO_ROOT` from its own file location
and resolves `prompts/` and `uploads/object_store` relative to it. Keeping the
source at `/app/apps/api/src` makes `REPO_ROOT` resolve to `/app`, so those paths
land where the application expects. A normal site-packages install would point
`REPO_ROOT` at the virtualenv and silently relocate both.

**Non-root.** The runtime creates `app` with the fixed uid/gid `10001` and drops
to it. A fixed uid — rather than a random one — is what lets the `uploads` named
volume be writable: a fresh named volume inherits the ownership of the image
directory it is mounted over.

| Variant | Contents | Approximate size (uncompressed) |
|---------|----------|---------------------------------|
| default (`INSTALL_EXTRAS=`) | core + `docx` | ~0.6–0.8 GB (base ~0.13 GB, `psql` + libs ~0.03 GB, venv ~0.45–0.65 GB) |
| `INSTALL_EXTRAS=embeddings,rerank` | adds PyTorch + transformers + sentence-transformers (+scipy/scikit-learn) | ~2–3.5 GB on linux/amd64 (PyPI's default torch wheel bundles CUDA), ~1.5 GB on arm64 (CPU wheel) |
| web image | `nginxinc/nginx-unprivileged:1.27-alpine` + `dist/` | ~50–55 MB (base ~50 MB, assets 1–3 MB) |

The heavy-extras figure is not a guess. The repository's own `install-all`
virtualenv (which also carries the `dev` extras) measures **1.47 GB** of
site-packages, of which **843 MB** is `torch` (583 MB), `transformers` (110 MB),
`scipy` (98 MB), `scikit-learn` (47 MB) and `sentence-transformers` (5.8 MB).
Removing that leaves 627 MB, which still includes pytest/mypy/ruff and the
`mcp`/`langsmith`/`ragas` extras the image does not install. The measured
extras roughly triple the venv, and on linux/amd64 they are larger still because
PyPI's default `torch` wheel pulls the CUDA runtime libraries.

These are expectations from the dependency sets and one measured venv, not
built images: see "Verification status" below. The single biggest lever is the
extras split — PyTorch in the API image would multiply size, cold-start time and
memory floor for a capability most requests do not need
(`docs/architecture/deployment.md` §3.1). Only set `INSTALL_EXTRAS` when the API
must run the models in-process:

```bash
INSTALL_EXTRAS=embeddings,rerank make up
# or, for a one-off build:
docker build -f docker/api.Dockerfile --build-arg INSTALL_EXTRAS=embeddings,rerank -t coursellm-api .
```

### The default image and the embedding/rerank settings

The default image has no PyTorch, but `.env.example` selects
`EMBEDDING_PROVIDER=local` and `RERANK_ENABLED=true`. The two behave differently:

* **Reranking degrades.** `get_reranker` catches the missing-extra error, logs
  `reranker_fallback` and substitutes `LexicalReranker`. `RERANK_ENABLED=true`
  on the default image is therefore safe, just less accurate.
* **Local embeddings do not.** `get_embedder` raises a domain
  `ServiceUnavailableError` when `sentence-transformers` is absent. The embedder
  is built lazily on the first ingest or retrieval, not at import, so the
  process starts and `/healthz` and `/readyz` stay green — but a document upload
  or a grounded query returns 503 until the extra is present.

For a dependency-free local stack, set `EMBEDDING_PROVIDER=hashing` (the
deterministic CI provider — it exercises the full storage/retrieval path without
a model) in `.env`, or build with `INSTALL_EXTRAS=embeddings`. The fully offline
combination used by CI is `LLM_ENABLED=false EMBEDDING_PROVIDER=hashing
RERANK_ENABLED=false`; with only the default image, keep `EMBEDDING_PROVIDER`
away from `local`.

All base images are pinned by tag **and** by the multi-architecture
manifest-list digest, so a moved tag cannot silently change a build. Refresh a
digest with `docker buildx imagetools inspect <image>`.

### Web image

`docker/web.Dockerfile` runs `npm ci && npm run build` in `node:24-alpine` and
copies `apps/web/dist` into `nginxinc/nginx-unprivileged:1.27-alpine`, which
serves it **as the non-root nginx user (uid 101)** using `docker/nginx.conf`.
Because a non-root process cannot bind port 80, the container listens on 8080;
compose maps `${WEB_PORT:-5173}` onto it. The server block provides:

* SPA fallback (`try_files $uri $uri/ /index.html`) so client routes render the
  app rather than nginx's 404;
* security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, CSP);
* `Cache-Control: no-cache` on `index.html` (`expires -1`) and a one-year cache
  on the content-addressed `/assets/` filenames (`expires 1y`);
* a `/api/` reverse proxy to `http://api:8000` with `proxy_buffering off` (SSE
  chat streaming), `proxy_request_buffering off` and `client_max_body_size 25m`
  so document uploads are not cut off by nginx's 1 MiB default. Keep that value
  in step with `MAX_UPLOAD_BYTES`. This is the local analogue of CloudFront
  routing `/api/*` to the API origin.

Swapping in a different build output is a one-line change: repoint the
`COPY --from=build /app/dist /usr/share/nginx/html` line (or copy a prebuilt
artefact in place of the build stage).

`VITE_API_BASE_URL` is inlined into the bundle at build time and must never be a
secret. Leave it empty for the compose stack (same-origin via the nginx proxy);
set it to a public URL when the assets are served from a different origin. If it
is set, add that origin to the nginx `connect-src` directive.

## Secrets

* Nothing is baked into an image. `.dockerignore` excludes `.env` and `.env.*`
  from every build context, so a developer's real `.env` cannot leak into a
  layer even by accident.
* `.env` is gitignored and is the single source of run-time configuration;
  compose reads it for interpolation and injects it into the containers.
  `.env.example` contains only non-secret placeholders.
* `SECRET_KEY` is mandatory: compose uses `${SECRET_KEY:?…}` and the application
  refuses to start in `prod` with a placeholder or a key shorter than 32 chars.
* The API image needs no credentials at build time. The web image inlines only
  public values.
* `docker compose config` resolves environment references. Run it after
  `cp .env.example .env` (so `SECRET_KEY` and `POSTGRES_*` are defined) when you
  want to confirm that no secret is written in the compose file itself — the
  only credential-shaped value in the output is then the documented placeholder
  from the example file. With a real `.env` present it will necessarily echo the
  interpolated values, which is why `.env` is not tracked.

## Environment variables

Everything the stack needs already exists in `.env.example`. The compose-only
additions are in the `Docker compose (local stack)` section there:
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PORT`, `REDIS_PORT`, `API_PORT`,
`WEB_PORT`, `INSTALL_EXTRAS`, `VITE_API_BASE_URL`.

The compose file always overrides `DATABASE_URL`, `ALEMBIC_DATABASE_URL`,
`REDIS_URL`, `PROMPTS_DIR` and `OTEL_EXPORTER_OTLP_ENDPOINT` with in-network
addresses, so the `localhost` values in `.env.example` are for running the API
outside Docker.

## Make targets

| Target | What it does |
|--------|--------------|
| `make up` | Build and start the stack detached |
| `make down` | Stop the stack, keeping named volumes |
| `make logs` | Tail logs from every service |
| `make ps` | Container status (`migrate` should read `exited (0)`) |
| `make migrate-docker` | Re-run migrations and the role bootstrap as the owner |

`docker compose down -v` additionally drops the `pgdata`, `redisdata` and
`uploads` volumes.

## Debugging a failure

Start from `docker compose ps`: it separates "not started", "unhealthy" and
"exited non-zero", which are three different problems.

**`migrate` exited non-zero.** Read `docker compose logs migrate`. The four
steps print as they run, so the last line names the stage that failed:

* `CREATE EXTENSION` fails → the Postgres image is not pgvector-enabled, or the
  owner role cannot create extensions.
* the `pg_extension` assertion fails silently after `CREATE EXTENSION` → the
  extension was created in a different database than `PGDATABASE`.
* `alembic upgrade head` fails → a migration error, or `ALEMBIC_DATABASE_URL`
  points at the restricted role instead of the owner.
* `bootstrap_db.sql` fails → the tables did not exist yet, which means step 3
  did not actually complete.

`api` will not start while `migrate` is failing. That is intended.

**`api` unhealthy.** The compose check requires `/readyz` to be 200 *and* the
pgvector row to be visible to `coursellm_app`. Inspect both directly:

```bash
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').read().decode())"
docker compose exec api psql -tAc "SELECT extname FROM pg_extension"
docker compose logs api | head -50   # startup logs the tenant-isolation verdict
```

A `tenant_isolation_not_enforced` warning in the logs means the API connected as
a role that bypasses RLS — check `DATABASE_URL` and whether `migrate` completed
the bootstrap step.

**`web` serves but the API calls fail.** `curl -s localhost:5173/api/v1/...`
exercises the nginx proxy. A 502 means nginx is up and `api` is not; a 200 from
`localhost:5173` alone does not prove the API is reachable.

**Postgres looks empty after a restart.** Named volumes survive `down`; only
`down -v` removes them. Conversely, an old `pgdata` volume from a previous run
can hide a broken bootstrap — `docker compose down -v` before re-testing a
schema change.

**A service cannot be reached from the host.** Only `api` and `web` are
published on all interfaces; Postgres, Redis and the collector are bound to
`127.0.0.1`.

## Verification status

`docker compose config -q` parses and validates the compose file offline and is
the only step in the acceptance list that does not require a running Docker
daemon. The full list, to run on a machine with the daemon available:

```bash
docker compose config -q
docker compose build
docker compose up -d
docker compose ps                                     # every service healthy, migrate exited 0
curl -s localhost:8000/healthz                        # 200
curl -s localhost:8000/readyz                         # 200, database check passing
curl -s -o /dev/null -w '%{http_code}' localhost:5173 # 200
docker compose exec api python -m coursellm.cli db-inspect   # ENFORCED
docker compose down -v
```

In the environment this packaging was authored in, the Docker CLI is installed
but the daemon is not running and cannot be started, so `build`, `up`, `ps`, the
`curl` checks and the `db-inspect` check could **not** be executed and are not
claimed as passing. Only the offline parse/validation (`docker compose config -q`
and `docker compose config`) was run. Treat the remaining steps as the
acceptance criteria, not as an observed result.
