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
(a [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901)), after applying the tolerance rules
you expect. There is no CLI yet.

```python
import json
from decimal import Decimal

from migrate_guard.domain.services.response_diff import diff_responses
from migrate_guard.domain.value_objects.rules import (
    DatetimeRule,
    IgnoreRule,
    NumericRule,
    UnorderedRule,
)

# Parse numbers as Decimal: the comparison must not add its own rounding errors.
baseline = json.loads(
    """{"request_id": "a1",
        "items": [{"sku": "A1", "price": 2.675}, {"sku": "B2", "price": 10.005}],
        "promo": {"ends_at": "2026-11-30T23:59:00-03:00"}}""",
    parse_float=Decimal,
)
candidate = json.loads(
    """{"request_id": "b2",
        "items": [{"sku": "B2", "price": 10.01}, {"sku": "A1", "price": 2.67}],
        "promo": {"ends_at": "2026-12-01T02:59:00"}}""",
    parse_float=Decimal,
)

rules = (
    IgnoreRule(path="/request_id"),
    UnorderedRule(path="/items", key="sku"),
    NumericRule(path="/items/*/price", decimals=2),
    DatetimeRule(path="/promo/ends_at"),
)
for d in diff_responses(baseline, candidate, rules).differences:
    print(f"{d.kind:<20} {d.location}  {d.detail}")
```

```text
value_changed        /items/0/price  rounded to 2 decimals (half_up): 2.68 vs 2.67
rule_not_applicable  /promo/ends_at  candidate datetime has no timezone: '2026-12-01T02:59:00'
```

The different `request_id`, the reordered items and `10.005` vs `10.01` are all tolerated. Two
things are not: `2.675` rounded the wrong way, and a timestamp that lost its time zone.

The rules:

- `ignore`: skip a path (and everything under it).
- `numeric`: round both sides to N decimals (`half_up`, `half_even` or `down`) before comparing.
- `unordered`: compare an array ignoring order, pairing items by a key. A duplicated or missing
  key is reported, never matched silently.
- `datetime`: compare ISO 8601 strings as instants, so the same moment in another offset matches.
  A datetime without a time zone is reported, never assumed to be UTC.

Paths accept `*` (`/items/*/price`). A rule that never matches anything is returned as unused, so
stale rules are visible.

A field that is `null` and a field that doesn't exist are different things, so they are reported
differently. The kinds of difference are `value_changed`, `type_changed`, `missing_in_candidate`,
`added_in_candidate`, `duplicate_key` and `rule_not_applicable`.

## Roadmap

- [x] **M0:** project skeleton, response diff with `ignore` rules.
- [ ] **M1:** ~~numeric, unordered and datetime rules~~ (done); HTTP target; `migrate-guard run`
      CLI; the retail-pricing demo.
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
