# migrate-guard — Specification

> **Catch behavior changes when you rewrite a legacy service — in the response AND in the database.**

Status: draft v0.1 · Owner: Marcos Lozina · License: MIT

---

## 1. Problem

Teams are rewriting legacy services with AI assistants (C# → Python, Flask → FastAPI, SQL Server →
Postgres). The new code compiles, the unit tests pass, and the behavior still changes in the edge
cases nobody wrote a test for: rounding, nulls, time zones, list ordering, discounts applied in a
different order. Worse, two services can return the **same HTTP response** and still write
**different rows** to the database.

Existing tools cover part of this:

- **Response diffing** (Twitter Diffy, `apiparity-check`): compares HTTP responses only.
- **Schema migration linters** (squawk, `migrate-guard` repos on GitHub): check DDL, not behavior.
- **Record/replay test tools**: focus on generating tests, not on comparing two implementations.

**Gap:** nobody compares the response **and the resulting database state** of an old and a new
implementation, with tolerance rules for the differences that are expected after a migration.

## 2. Goals and non-goals

**Goals (v0.x)**

1. Replay a corpus of requests against a **baseline** (legacy) and a **candidate** (rewrite).
2. Compare responses (status, selected headers, JSON body) with **tolerance rules**.
3. Compare the **database state** each implementation leaves after each request, across different
   schemas and engines.
4. Run from the CLI and from **pytest**, with CI-friendly exit codes and reports.
5. (Phase 2) **AI triage**: classify each difference as *expected* or *likely bug*, opt-in only.

**Non-goals**

- Converting or generating code.
- Load or performance testing.
- Capturing production traffic (v0.x reads a corpus file; capture is a later adapter).
- Non-HTTP protocols (gRPC, queues) in v0.x.
- Proving full equivalence. A green run is evidence for the corpus, not a proof.

## 3. Concepts

- **Case:** one request (method, path, query, headers, body) plus an optional state **seed**.
- **Target:** a way to execute a case. `http` (running service on loopback), `asgi` or `wsgi`
  (in-process app).
- **State probe:** reads the watched tables of a target's database after a case runs.
- **Rule:** a tolerance for an expected difference (ignore a path, numeric tolerance, unordered
  array, datetime normalization, column mapping).
- **Difference:** one mismatch, located by a JSON Pointer (response) or `table/pk/column` (state).
- **Report:** the result of a run (terminal, JSON, JUnit XML).

## 4. Configuration

`migrate-guard.yaml`:

```yaml
version: 1
baseline:
  target: http
  url: http://127.0.0.1:5001
  state:
    dsn_env: LEGACY_DB_DSN          # DSN is read from the environment, never from this file
    tables:
      prices: { key: [sku] }
      promotions: { key: [promo_id] }
candidate:
  target: asgi
  app: new_pricing.main:app
  state:
    dsn_env: NEW_DB_DSN
    tables:
      prices: { key: [sku], map_from: Prices }          # legacy table name
      promotions: { key: [promo_id], map_from: tblPromo }
corpus: cases/
seed:
  strategy: script                   # script | transaction
  baseline: seeds/legacy.sql
  candidate: seeds/new.sql
rules:
  response:
    - ignore: /request_id
    - ignore: /generated_at
    - numeric: { path: /items/*/price, decimals: 2 }
    - unordered: { path: /items, key: sku }
    - datetime: { path: /promo/ends_at, normalize: utc }
  state:
    - ignore_column: { table: prices, column: updated_at }
    - map_column: { table: prices, from: PriceAmt, to: price }
    - numeric: { table: prices, column: price, decimals: 2 }
allow_mutating: false
```

Case file (`cases/apply_promo.yaml`):

```yaml
name: apply stacked promotions to a cart
request:
  method: POST
  path: /cart/price
  json: { items: [{ sku: "A1", qty: 3 }], promo_codes: ["SUMMER10", "VIP5"] }
mutating: true
```

## 5. Functional requirements

Each requirement lists its acceptance scenarios (Given / When / Then), including failure modes.

### R1 — Run a corpus against two targets

- **Given** a valid config and a corpus of 10 cases, **when** I run `migrate-guard run`, **then**
  every case runs against both targets and the report lists 10 results.
- **Given** a candidate that is not reachable, **when** I run, **then** the run stops before the
  first case, reports `target unreachable: candidate`, and exits with code **2**.
- **Given** a case that times out on one target, **when** I run, **then** that case is reported as
  `error` (not `match`), the run continues, and the exit code is **1**.

### R2 — Compare responses with tolerance rules

- **Given** both targets return `{"price": 10.005}` vs `{"price": 10.01}` and a rule
  `numeric decimals: 2`, **when** compared, **then** it is a match, because both round to `10.01`
  with the rule's rounding mode (default `half_up`, configurable).
- **Given** `{"price": 2.675}` vs `{"price": 2.67}` and the same rule, **then** it is a
  difference (`2.68` vs `2.67`). JSON numbers are parsed as `Decimal`, never as `float`, so the
  comparison itself can't introduce rounding errors.
- **Given** a body field `/request_id` that differs and an `ignore` rule for it, **then** no
  difference is reported.
- **Given** arrays with the same items in a different order and an `unordered` rule keyed by `sku`,
  **then** it is a match. **Given** a duplicated key in one array, **then** it is a difference
  (`duplicate key A1 in candidate`), never a silent match.
- **Given** `null` on both sides under a value rule (`numeric`, `datetime`, `unordered`), **then**
  it is a match: there is no value to tolerate. **Given** equal values of the wrong type (`"abc"`
  vs `"abc"` under `numeric`), **then** it is still `rule not applicable`, so a misconfigured rule
  never passes silently. `null` on one side only is a type change.
- **Given** a rule whose path matches nothing in either response, **then** the report shows a
  warning `rule never applied`, so stale rules are visible.

### R3 — Compare database state

- **Given** a case that updates one price, **when** both targets run it, **then** the state probe
  compares the watched tables row by row (by key) and reports `added`, `removed` or `changed`
  rows, with the column and both values.
- **Given** a legacy table `Prices.PriceAmt` and a new table `prices.price` with a `map_column`
  rule, **then** they are compared as the same column.
- **Given** type differences that are equivalent (`DECIMAL(10,2)` vs `NUMERIC`, `DATETIME` vs
  `TIMESTAMPTZ` in UTC), **then** values are normalized before comparing.
- **Given** a watched table with no key defined, **then** the config is rejected with exit code
  **2** (no row-order guessing).
- **Given** a table with more than `max_rows` (default 10 000) rows, **then** the probe stops and
  reports `state too large: <table>` instead of loading it into memory.

### R4 — Seed and isolate state

- **Given** `seed.strategy: script`, **when** each case starts, **then** both databases are reset
  with their seed script **before** the case runs.
- **Given** `seed.strategy: transaction` and an in-process target, **then** each case runs inside
  a transaction that is rolled back afterwards.
- **Given** the seed fails on one target, **then** that case is reported as `error: seed failed`
  and **no request is sent** to either target.

### R5 — CLI and exit codes

- `migrate-guard run [--config] [--case NAME] [--report json|junit|terminal]`
- `migrate-guard init` writes a commented example config.
- Exit codes: **0** all match · **1** at least one difference or case error · **2** invalid
  config, unreachable target, or safety refusal.

### R6 — pytest plugin

- **Given** `pytest --migrate-guard=migrate-guard.yaml`, **then** each case becomes one test named
  `migrate_guard[<case name>]`, and a difference fails that test with the readable diff.

### R7 — Reports

- Terminal: summary plus the first N differences per case.
- JSON: full result, stable schema (`report_version: 1`).
- JUnit XML: one testcase per case, for CI.

### R8 — AI triage (phase 2, opt-in)

- **Given** `--triage claude` and `ANTHROPIC_API_KEY` set, **then** each difference gets a label
  (`expected` / `likely_bug` / `unsure`) and a one-line reason.
- **Given** `--triage` is not passed, **then** no network call is made to any LLM provider.
- **Given** the LLM call fails or returns an invalid label, **then** the difference keeps the
  label `untriaged`, and the run result and exit code do not change.
- Triage **never** turns a difference into a match. It only annotates it.

## 6. Data integrity and transaction boundaries

- **Seed before each case, always.** Each case starts from a known state on both targets. A crash
  in the middle of a case can't contaminate the next one.
- **Baseline and candidate never share a database.** The config is rejected if both `dsn_env`
  values resolve to the same DSN.
- **Order of operations per case:** seed both → run baseline → probe baseline → run candidate →
  probe candidate → compare. A failure at any step marks the case `error` and skips the remaining
  steps for that case.
- **Probes are read-only.** The state probe opens its own connection and only runs `SELECT`
  statements. It never writes.
- **Partial results are not reported as matches.** If the response compared but the state probe
  failed, the case is `error`, not `match`.
- **Seed scripts run inside a transaction** where the engine supports it. If the script fails, it
  is rolled back and the case is `error: seed failed`.

## 7. Security and privacy

- **Loopback only by default.** `http` targets outside `127.0.0.1`, `::1` and `localhost` require
  `--allow-remote`. The tool is meant for test environments, not production.
- **Mutating requests are opt-in.** `POST`, `PUT`, `PATCH` and `DELETE` cases require
  `allow_mutating: true` in the config.
- **No secrets in config files.** DSNs and API keys come from environment variables (`dsn_env`).
  The config loader rejects inline DSNs that contain a password.
- **Redaction in reports.** `Authorization`, `Cookie`, `Set-Cookie` and `X-Api-Key` headers, plus
  any field matching configurable patterns (`password`, `token`, `secret`, card numbers), are
  replaced by `[REDACTED]` in every report and log.
- **Reports may contain test data.** `migrate-guard init` adds the report directory to
  `.gitignore`, and the docs warn against running with production data.
- **AI triage sends data out.** Only with an explicit flag. The differences are redacted before
  being sent, the prompt is logged locally (also redacted), and there is a cost limit
  (`--triage-max-calls`, default 50).
- **Seed scripts are trusted input.** They run with the DSN's privileges. The docs recommend a
  dedicated test user.
- **Dependency hygiene.** `pip-audit` and `ruff` run in CI. PyPI releases use Trusted Publishing.

## 8. Architecture

Hexagonal. The domain has no I/O, so it can be unit-tested with fakes.

```
src/migrate_guard/
  domain/
    entities/        # Case, CaseResult, Difference, Snapshot
    value_objects/   # JsonPointer, RowKey, Rule variants (frozen Pydantic)
    ports/           # TargetRunner, StateProbe, Seeder, Reporter, Triage (ABCs)
    services/        # response_diff.py, state_diff.py, rules.py (pure functions)
  application/
    use_cases/       # RunCorpus, TriageDifferences
  infrastructure/
    adapters/
      targets/       # HttpTarget (httpx), AsgiTarget, WsgiTarget
      state/         # SqlAlchemyStateProbe, ScriptSeeder, TransactionSeeder
      reporters/     # TerminalReporter (rich), JsonReporter, JunitReporter
      triage/        # ClaudeTriage (anthropic SDK)
    config/          # YAML loader + pydantic-settings validation
  cli/               # typer app
  pytest_plugin.py
tests/
  unit/              # domain services and use cases with fakes
  integration/       # adapters vs real Postgres (Testcontainers) and SQLite
examples/
  retail-pricing/    # legacy Flask + SQLite vs rewritten FastAPI + Postgres
```

**Stack:** Python 3.12+, uv, httpx, Pydantic v2, SQLAlchemy 2, PyYAML, typer, rich, pytest,
Testcontainers, ruff, mypy (strict), anthropic SDK (optional extra `[triage]`).

## 9. The demo: retail pricing

A legacy pricing service (Flask + SQLite) and a rewrite (FastAPI + Postgres) produced with an AI
assistant. The rewrite passes its own tests and still has five planted behavior changes:

1. **Rounding:** legacy rounds half-up on `Decimal`; the rewrite calls `round()` on a float. The
   float `2.675` is really `2.67499999…`, so it becomes `2.67` instead of `2.68`.
2. **Stacked discounts:** legacy applies the percentage first, then the fixed amount; the rewrite
   does it the other way.
3. **Null end date:** legacy treats a promotion with no `ends_at` as active; the rewrite treats it
   as expired.
4. **Time zone:** the promotion window is checked in local time in legacy and in UTC in the
   rewrite.
5. **Silent state change:** the rewrite returns the same response but also writes
   `last_priced_at` on every read. Only the state diff catches it.

The demo's README shows the run that catches all five, and which ones a response-only tool misses
(number 5, and number 2 when the totals happen to match).

## 10. Open questions

- Corpus capture: a recording proxy (like Diffy) or a middleware for Flask and FastAPI? (after v0.3)
- SQL Server in CI: the Testcontainers image is heavy. Run it only in a nightly job?
- Do we support comparing responses from **three** instances (two baselines) to filter
  non-deterministic noise, like Diffy did?
