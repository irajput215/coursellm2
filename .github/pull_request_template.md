# Pull request

## What

<!-- One paragraph. What does this change do, in the imperative? -->

## Why

<!-- The problem it solves. Link the issue or ADR: `Refs #123`, `Implements ADR-0007`. -->

## Architecture impact

<!--
Which layers move: API, retrieval, graph, security, evaluation, infrastructure,
frontend? Any new dependency, table, migration, prompt version or configuration
key must be named here. Write "None" if nothing outside one module changes.
-->

- [ ] No change to an interface other modules depend on
- [ ] New/changed database migration is expand-only and backward compatible across the rollout window
- [ ] New/changed prompt has a version recorded in the evaluation report
- [ ] No secret-shaped value is committed (and no `secret-scan: allow` pragma was added)

## Tests

<!--
Paste the commands you ran and their outcome. A claim without a command is not
evidence. The CI gates are: `make lint`, `make typecheck`, `make secrets-scan`,
`make test-unit`, `make test-security`, `make test-integration`,
`make eval-retrieval && make eval-gate`, `make build-web`.
-->

```
$ make lint
$ make typecheck
$ make test-unit
```

- [ ] Unit tests added or updated
- [ ] Integration tests run against PostgreSQL as `coursellm_app` (RLS enforced)
- [ ] Security tests unaffected or extended

## Metrics

<!--
Required when the change touches retrieval, prompts or the evaluation dataset.
Paste the eval-gate output. If a metric is `not_measured`, say so and give the
reason code; do not leave it blank and do not estimate a number.
-->

```
$ make eval-retrieval
$ make eval-gate EVAL_CURRENT=evals/reports/retrieval.json
```

| Metric | Baseline | This branch | Change |
|--------|----------|-------------|--------|
| | | | |

## Trade-offs

<!--
What was the alternative, and why is this one better here? Note anything you
wanted to do but deliberately did not, and anything a reviewer should push back
on. "None" is rarely a true answer.
-->

## Reviewer notes

<!-- Anything non-obvious to check, or a suggested review order. -->
