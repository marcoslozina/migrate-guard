# migrate-guard

> **Catch behavior changes when you rewrite a legacy service: in the response AND in the database.**

Behavioral parity testing for service migrations. Not a schema migration linter.

**Status:** pre-alpha, built in public. Nothing to install yet.

## The problem

Teams are rewriting legacy services with AI assistants: C# to Python, Flask to FastAPI, SQL Server
to Postgres. The new code compiles, the unit tests pass, and the behavior still changes in the
edge cases nobody wrote a test for:

- rounding (`2.675` becomes `2.67` instead of `2.68`),
- nulls (a promotion with no end date is suddenly expired),
- time zones, list ordering, discounts applied in a different order.

Worse, two services can return the **same HTTP response** and still write **different rows** to
the database.

## The idea

Replay the same requests against the **baseline** (legacy) and the **candidate** (rewrite), then
compare two things:

1. **The response:** status, selected headers and JSON body, with tolerance rules for the
   differences you expect (ignore a field, numeric precision, unordered arrays, time zones).
2. **The database state** each one leaves behind, row by row, even across different schemas and
   engines.

Every difference is reported with its exact location, so you can decide: expected, or a bug.

## How it compares

- **Response diffing tools** (Twitter Diffy, `apiparity-check`) compare HTTP responses only.
- **Schema migration linters** (squawk and similar) check DDL, not behavior.
- **Record and replay tools** generate tests; they don't compare two implementations.

migrate-guard compares the response **and** the resulting database state, with tolerance rules
built for migrations.

## What works today

The core diff engine: compare two JSON bodies and get every difference with its exact location
(a [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901)). There is no CLI yet.

```python
import json
from decimal import Decimal

from migrate_guard.domain.services.response_diff import diff_responses
from migrate_guard.domain.value_objects.rules import IgnoreRule

# Parse numbers as Decimal: the comparison must not add its own rounding errors.
baseline = json.loads('{"request_id": "a1", "total": 14.93, "promo": {"ends_at": null}}',
                      parse_float=Decimal)
candidate = json.loads('{"request_id": "b2", "total": 14.92, "promo": {}}',
                       parse_float=Decimal)

result = diff_responses(baseline, candidate, (IgnoreRule(path="/request_id"),))
for d in result.differences:
    print(f"{d.kind:<22} {d.location}  {d.baseline!r} -> {d.candidate!r}")
```

```text
value_changed          /total  Decimal('14.93') -> Decimal('14.92')
missing_in_candidate   /promo/ends_at  None -> MISSING
```

A field that is `null` and a field that doesn't exist are different things, so they are reported
differently. The four kinds of difference are `value_changed`, `type_changed`,
`missing_in_candidate` and `added_in_candidate`.

## Roadmap

- [x] **M0:** project skeleton, response diff with `ignore` rules.
- [ ] **M1:** numeric, unordered and datetime rules; HTTP target; `migrate-guard run` CLI; the
      retail-pricing demo.
- [ ] **M2:** database state diff (SQLAlchemy: Postgres, SQL Server, SQLite), seeding and isolation.
- [ ] **M3:** pytest plugin, JSON and JUnit reports, first PyPI release.
- [ ] **M4:** opt-in AI triage of differences (expected vs likely bug).

The full specification is in [`docs/SPEC.md`](docs/SPEC.md).

## Safety by default

- Loopback targets only, unless you pass `--allow-remote`.
- Mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) are opt-in.
- Secrets come from environment variables, never from config files.
- Sensitive headers and fields are redacted in every report.
- AI triage never runs unless you ask for it, and never turns a difference into a match.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

## License

MIT. See [LICENSE](LICENSE).
