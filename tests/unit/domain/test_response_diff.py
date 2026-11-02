from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind
from migrate_guard.domain.services.response_diff import diff_responses
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.rules import IgnoreRule


def at(text: str) -> JsonPointer:
    return JsonPointer.parse(text)


def test_diff_when_bodies_are_equal_then_matches() -> None:
    body: JsonValue = {"sku": "A1", "items": [{"price": Decimal("14.93")}], "promo": None}

    assert diff_responses(body, body).is_match


def test_diff_when_rounding_differs_then_reports_value_changed() -> None:
    result = diff_responses({"price": Decimal("14.93")}, {"price": Decimal("14.92")})

    assert result.differences == (
        Difference(DiffKind.VALUE_CHANGED, at("/price"), Decimal("14.93"), Decimal("14.92")),
    )


def test_diff_when_number_becomes_string_then_reports_type_changed() -> None:
    result = diff_responses({"qty": 10}, {"qty": "10"})

    assert [d.kind for d in result.differences] == [DiffKind.TYPE_CHANGED]


def test_diff_when_bool_vs_int_then_reports_type_changed() -> None:
    # In Python True == 1; in JSON they are different types.
    result = diff_responses({"active": True}, {"active": 1})

    assert [d.kind for d in result.differences] == [DiffKind.TYPE_CHANGED]


def test_diff_when_null_vs_missing_then_reports_missing_not_type_change() -> None:
    result = diff_responses({"ends_at": None}, {})

    assert result.differences == (
        Difference(DiffKind.MISSING_IN_CANDIDATE, at("/ends_at"), None, MISSING),
    )


def test_diff_when_candidate_adds_field_then_reports_added() -> None:
    result = diff_responses({}, {"last_priced_at": "2026-11-01"})

    assert [d.kind for d in result.differences] == [DiffKind.ADDED_IN_CANDIDATE]


def test_diff_when_int_vs_equal_decimal_then_matches() -> None:
    assert diff_responses({"total": 10}, {"total": Decimal("10.0")}).is_match


def test_diff_when_list_is_shorter_then_reports_missing_item_by_index() -> None:
    result = diff_responses({"items": ["A1", "B2"]}, {"items": ["A1"]})

    assert result.differences == (
        Difference(DiffKind.MISSING_IN_CANDIDATE, at("/items/1"), "B2", MISSING),
    )


def test_diff_when_list_is_reordered_then_reports_differences() -> None:
    result = diff_responses(["A1", "B2"], ["B2", "A1"])

    assert [str(d.location) for d in result.differences] == ["/0", "/1"]


def test_diff_when_nested_then_location_is_full_pointer() -> None:
    result = diff_responses(
        {"items": [{"price": Decimal("1.00")}]}, {"items": [{"price": Decimal("2.00")}]}
    )

    assert str(result.differences[0].location) == "/items/0/price"


def test_diff_when_several_fields_differ_then_order_is_stable() -> None:
    result = diff_responses({"a": 1, "b": 1}, {"c": 1, "b": 2, "a": 2})

    assert [str(d.location) for d in result.differences] == ["/a", "/b", "/c"]


def test_diff_when_ignored_field_differs_then_matches() -> None:
    rule = IgnoreRule(path="/request_id")

    result = diff_responses({"request_id": "x1"}, {"request_id": "y2"}, (rule,))

    assert result.is_match
    assert result.unused_rules == ()


def test_diff_when_ignored_field_exists_on_one_side_then_matches() -> None:
    rule = IgnoreRule(path="/generated_at")

    assert diff_responses({"generated_at": "now"}, {}, (rule,)).is_match


def test_diff_when_ignore_has_wildcard_then_skips_every_item() -> None:
    rule = IgnoreRule(path="/items/*/id")
    baseline: JsonValue = {"items": [{"id": 1, "sku": "A1"}, {"id": 2, "sku": "B2"}]}
    candidate: JsonValue = {"items": [{"id": 9, "sku": "A1"}, {"id": 8, "sku": "XX"}]}

    result = diff_responses(baseline, candidate, (rule,))

    assert [str(d.location) for d in result.differences] == ["/items/1/sku"]


def test_diff_when_rule_matches_nothing_then_reports_it_as_unused() -> None:
    stale = IgnoreRule(path="/old_field")

    result = diff_responses({"a": 1}, {"a": 1}, (stale,))

    assert result.is_match
    assert result.unused_rules == (stale,)


def test_diff_when_value_is_float_then_raises() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        diff_responses({"price": 2.675}, {"price": 2.675})  # type: ignore[dict-item]


def test_ignore_rule_when_path_is_invalid_then_rejected() -> None:
    with pytest.raises(ValidationError):
        IgnoreRule(path="request_id")
