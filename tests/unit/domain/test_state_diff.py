from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind
from migrate_guard.domain.entities.state import Row, StateSnapshot, TableSnapshot
from migrate_guard.domain.services.state_diff import diff_states
from migrate_guard.domain.services.state_values import (
    UnsupportedValueError,
    format_row_key,
    normalize_db_value,
)
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.state_rules import IgnoreColumnRule, StateNumericRule


def prices(**rows: Row) -> StateSnapshot:
    return StateSnapshot({"prices": TableSnapshot({f"sku={k}": v for k, v in rows.items()})})


def at(text: str) -> JsonPointer:
    return JsonPointer.parse(text)


# --- values -----------------------------------------------------------------------------


def test_normalize_float_keeps_the_shortest_exact_text() -> None:
    assert normalize_db_value(2.675) == Decimal("2.675")


def test_normalize_aware_datetime_becomes_naive_utc() -> None:
    buenos_aires = timezone(timedelta(hours=-3))

    value = datetime(2026, 11, 30, 22, 0, tzinfo=buenos_aires)

    assert normalize_db_value(value) == "2026-12-01T01:00:00"


def test_normalize_naive_datetime_is_kept_as_written() -> None:
    # A local time saved without offset must not be silently read as UTC.
    assert normalize_db_value(datetime(2026, 11, 30, 22, 0)) == "2026-11-30T22:00:00"


def test_normalize_timestamptz_and_utc_datetime_are_equal() -> None:
    aware = datetime(2026, 12, 1, 1, 0, tzinfo=UTC)

    assert normalize_db_value(aware) == normalize_db_value(datetime(2026, 12, 1, 1, 0))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 11, 9), "2026-11-09"),
        (b"\x00\xff", "0x00ff"),
        (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (True, True),
    ],
)
def test_normalize_other_types(value: object, expected: object) -> None:
    assert normalize_db_value(value) == expected


@pytest.mark.parametrize("value", [float("nan"), Decimal("Infinity"), object()])
def test_normalize_rejects_what_json_can_not_hold(value: object) -> None:
    with pytest.raises(UnsupportedValueError):
        normalize_db_value(value)


def test_format_row_key_escapes_separators() -> None:
    assert format_row_key(("sku", "store"), ("A,1", 3)) == "sku=A%2C1,store=3"


def test_format_row_key_rejects_a_null_key() -> None:
    with pytest.raises(ValueError, match="null"):
        format_row_key(("sku",), (None,))


# --- diff -------------------------------------------------------------------------------


def test_diff_states_when_a_read_writes_a_column_then_reports_it() -> None:
    # Bug 5 of the demo: same response, but the rewrite stamps last_priced_at on every read.
    baseline = prices(A1={"sku": "A1", "last_priced_at": None})
    candidate = prices(A1={"sku": "A1", "last_priced_at": "2026-11-09T12:00:00"})

    result = diff_states(baseline, candidate)

    assert result.differences == (
        Difference(
            DiffKind.TYPE_CHANGED,
            at("/prices/sku=A1/last_priced_at"),
            None,
            "2026-11-09T12:00:00",
        ),
    )


def test_diff_states_reports_rows_written_only_on_one_side() -> None:
    baseline = prices(A1={"sku": "A1"})
    candidate = prices(B2={"sku": "B2"})

    kinds = [(d.kind, str(d.location)) for d in diff_states(baseline, candidate).differences]

    assert kinds == [
        (DiffKind.MISSING_IN_CANDIDATE, "/prices/sku=A1"),
        (DiffKind.ADDED_IN_CANDIDATE, "/prices/sku=B2"),
    ]


def test_diff_states_ignore_column_skips_it_in_every_row() -> None:
    baseline = prices(A1={"updated_at": "1"}, B2={"updated_at": "1"})
    candidate = prices(A1={"updated_at": "2"}, B2={"updated_at": "3"})

    result = diff_states(
        baseline, candidate, (IgnoreColumnRule(table="prices", column="updated_at"),)
    )

    assert result.differences == ()
    assert result.unused_rules == ()


def test_diff_states_numeric_rule_tolerates_storage_precision() -> None:
    baseline = prices(A1={"price": Decimal("2.68")})
    candidate = prices(A1={"price": Decimal("2.6800")})
    rule = StateNumericRule(table="prices", column="price", decimals=2)

    assert diff_states(baseline, candidate, (rule,)).differences == ()


def test_diff_states_reports_a_rule_for_a_column_that_never_appears() -> None:
    rule = IgnoreColumnRule(table="prices", column="no_such_column")

    result = diff_states(prices(A1={"sku": "A1"}), prices(A1={"sku": "A1"}), (rule,))

    assert result.unused_rules == (rule,)


def test_diff_states_row_missing_in_candidate_keeps_the_whole_row() -> None:
    result = diff_states(prices(A1={"sku": "A1"}), prices())

    assert result.differences[0].baseline == {"sku": "A1"}
    assert result.differences[0].candidate is MISSING
