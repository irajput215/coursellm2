# CI workflows

Continuous integration for CourseLLM. Every workflow is a thin wrapper around the
repository `Makefile`, so a green pipeline means the same thing as a green local
run — and a red one can be reproduced on a workstation with the command in the
"Local equivalent" column.

## Status badges

Paste this block into the main `README.md` once these workflows have run on
`main`:

```markdown
[![backend](https://github.com/irajput215/coursellm2/actions/workflows/backend.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/backend.yml)
[![integration](https://github.com/irajput215/coursellm2/actions/workflows/integration.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/integration.yml)
[![eval](https://github.com/irajput215/coursellm2/actions/workflows/eval.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/eval.yml)
[![security](https://github.com/irajput215/coursellm2/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/security.yml)
[![frontend](https://github.com/irajput215/coursellm2/actions/workflows/frontend.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/frontend.yml)
[![docker](https://github.com/irajput215/coursellm2/actions/workflows/docker.yml/badge.svg?branch=main)](https://github.com/irajput215/coursellm2/actions/workflows/docker.yml)
```

## What each workflow gates

| Workflow | Trigger | Gates | Local equivalent |
|----------|---------|-------|------------------|
| [`backend.yml`](./backend.yml) | push to `main`, every PR | `ruff check` + `ruff format --check`, `mypy`, the secret scan, unit tests, security tests | `make lint typecheck secrets-scan test-unit test-security` |
| [`integration.yml`](./integration.yml) | push to `main`, every PR | migrations + `scripts/bootstrap_db.sql` against `pgvector/pgvector:pg17`, then `pytest -m integration` as `coursellm_app` | `make db-setup test-integration` |
| [`eval.yml`](./eval.yml) | push to `main`, PRs touching retrieval / prompts / evals | `run_retrieval_eval` against the committed baseline; the report is uploaded as an artefact with 30-day retention | `make eval-retrieval eval-gate EVAL_CURRENT=evals/reports/retrieval.json` |
| [`security.yml`](./security.yml) | push to `main`, every PR, weekly on cron | the secret scan, `pip-audit` over the installed environment, `pytest -m security` (the injection corpus) | `make secrets-scan audit-deps test-security` |
| [`frontend.yml`](./frontend.yml) | push to `main`, every PR | `apps/web`: lint, typecheck, test, build — or a notice and exit 0 when `apps/web/package.json` is absent | `make lint-web typecheck-web test-web build-web` |
| [`docker.yml`](./docker.yml) | push to `main`, PRs touching `docker/` or `apps/` | builds `docker/api.Dockerfile` and `docker/web.Dockerfile`, tagged by git SHA. **Nothing is pushed.** | `docker build -f docker/api.Dockerfile -t coursellm-api:local .` |

## Conventions

* **Actions are pinned to a major version** (`actions/checkout@v4`), never
  `@main`. The version is bumped by Dependabot (see
  [`../dependabot.yml`](../dependabot.yml)).
* **`permissions:` is explicit and minimal** on every workflow — `contents: read`
  — because no job writes back to the repository.
* **Every job sets `timeout-minutes`.** An unbounded job is how a stuck test
  burns a quota.
* **Every workflow has a `concurrency` group with `cancel-in-progress: true`**, so
  a superseded push cancels the run it replaced.
* **Python dependencies are cached** on the hash of `apps/api/pyproject.toml` and
  the root `pyproject.toml`. The repository has no lockfile yet; when one lands
  (`uv.lock`, `requirements*.txt` or equivalent), add it to the `hashFiles()`
  call in each workflow so the cache invalidates on a real dependency change.

## The app-role convention (the one detail that matters)

`integration.yml` and `eval.yml` both create the `coursellm_app` role with
`scripts/bootstrap_db.sql` and assert that `make db-inspect` reports
`ENFORCED by Row-Level Security` before running anything. PostgreSQL ignores
every Row-Level Security policy for a superuser or a role holding `BYPASSRLS`,
silently and without warning, so a suite that connected as the schema owner would
make every isolation test pass for the wrong reason. The assertion is a hard gate:
if it fails, the job fails.

## The evaluation gate

`eval.yml` runs the LLM-free retrieval half only. No provider key is configured,
so the runner records every judge-derived metric as `not_measured` with a stable
reason; the workflow must not turn a missing measurement into a failure. A
mistake worth naming: `make eval-gate` defaults to `evals/reports/latest.json`,
which is gitignored and stale in a clean checkout. The workflow passes
`EVAL_CURRENT=evals/reports/retrieval.json` so the gate compares the report the
previous step actually wrote against the committed retrieval baseline.

The job is skipped on fork pull requests. The retrieval half needs no secret, but
skipping forks means a fork can never spend evaluation budget or observe a
provider key if the LLM half is wired in later.

## Deliberately *not* in CI

* **Deployment.** There is no deploy job, no Vercel step, no S3 sync and no
  `terraform apply`. Deployment is out of scope for this repository's automation.
* **`terraform plan`.** It is a manual, reviewed action. Nobody should be able to
  change infrastructure by merging a pull request, and a plan is only meaningful
  against real remote state and real credentials. A future AWS deployment job
  would be placed behind a GitHub *environment* with required reviewers, so a
  human approves the apply, plus short-lived OIDC credentials rather than stored
  access keys.
* **`terraform apply`.** Not automated here under any circumstance.
* **The LLM-dependent evaluation half** (`make eval`, judging, cost and token
  metrics). It costs money and needs provider keys; it is run deliberately, by a
  person, rather than on every push. When it is wired in it must be gated on a
  provider secret and blocked on fork pull requests.
* **Pushes of any image.** `docker.yml` builds and stops. Registry publishing and
  release tagging are separate, reviewed steps.
* **Frontend deployment.** The web image is built and verified; serving it is not
  this repository's CI concern.
