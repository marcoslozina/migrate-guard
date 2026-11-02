from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind
from migrate_guard.domain.services.response_diff import diff_responses
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.rules import (
    DatetimeRule,
    IgnoreRule,
    NumericRule,
    Rounding,
    UnorderedRule,
)


def at(text: str) -> JsonPointer:
    return JsonPointer.parse(text)


def kinds(baseline: JsonValue, candidate: JsonValue, *rules: object) -> list[DiffKind]:
    result = diff_responses(baseline, candidate, rules)  # type: ignore[arg-type]
    return [d.kind for d in result.differences]


# --- numeric ---------------------------------------------------------------------------


def test_numeric_when_both_round_to_same_value_then_matches() -> None:
    # SPEC R2: 10.005 rounds half_up to 10.01.
    rule = NumericRule(path="/price", decimals=2)

    assert diff_responses(
        {"price": Decimal("10.005")}, {"price": Decimal("10.01")}, (rule,)
    ).is_match


def test_numeric_when_rounded_values_differ_then_reports_value_changed() -> None:
    # SPEC R2: 2.675 -> 2.68 vs 2.67.
    rule = NumericRule(path="/price", decimals=2)

    result = diff_responses({"price": Decimal("2.675")}, {"price": Decimal("2.67")}, (rule,))

    assert result.differences == (
        Difference(
            DiffKind.VALUE_CHANGED,
            at("/price"),
            Decimal("2.675"),
            Decimal("2.67"),
            "rounded to 2 decimals (half_up): 2.68 vs 2.67",
        ),
    )


def test_numeric_when_half_even_then_uses_bankers_rounding() -> None:
    rule = NumericRule(path="/price", decimals=2, rounding=Rounding.HALF_EVEN)

    # half_even: 2.665 -> 2.66, so it matches a candidate that already rounded that way.
    assert diff_responses({"price": Decimal("2.665")}, {"price": Decimal("2.66")}, (rule,)).is_match


def test_numeric_when_wildcard_then_applies_to_every_item() -> None:
    rule = NumericRule(path="/items/*/price", decimals=2)
    baseline: JsonValue = {"items": [{"price": Decimal("1.005")}, {"price": Decimal("3.10")}]}
    candidate: JsonValue = {"items": [{"price": Decimal("1.01")}, {"price": Decimal("3.20")}]}

    result = diff_responses(baseline, candidate, (rule,))

    assert [str(d.location) for d in result.differences] == ["/items/1/price"]


def test_numeric_when_int_and_decimal_then_compares_by_value() -> None:
    rule = NumericRule(path="/total", decimals=2)

    assert diff_responses({"total": 10}, {"total": Decimal("10.001")}, (rule,)).is_match


def test_numeric_when_candidate_is_string_then_reports_type_changed_not_tolerated() -> None:
    # The rule tolerates rounding, never a change in the contract.
    rule = NumericRule(path="/price", decimals=2)

    assert kinds({"price": Decimal("10.00")}, {"price": "10.00"}, rule) == [DiffKind.TYPE_CHANGED]


def test_numeric_when_both_sides_are_strings_then_rule_not_applicable() -> None:
    rule = NumericRule(path="/price", decimals=2)

    assert kinds({"price": "10.00"}, {"price": "10.00"}, rule) == [DiffKind.RULE_NOT_APPLICABLE]


def test_numeric_when_value_too_large_to_round_then_rule_not_applicable() -> None:
    rule = NumericRule(path="/n", decimals=2)
    huge = Decimal("1e40")

    assert kinds({"n": huge}, {"n": huge}, rule) == [DiffKind.RULE_NOT_APPLICABLE]


def test_numeric_rule_when_decimals_negative_then_rejected() -> None:
    with pytest.raises(ValidationError):
        NumericRule(path="/price", decimals=-1)


# --- datetime --------------------------------------------------------------------------


def test_datetime_when_same_instant_in_other_offset_then_matches() -> None:
    rule = DatetimeRule(path="/promo/ends_at")
    baseline: JsonValue = {"promo": {"ends_at": "2026-11-02T10:00:00-03:00"}}
    candidate: JsonValue = {"promo": {"ends_at": "2026-11-02T13:00:00Z"}}

    assert diff_responses(baseline, candidate, (rule,)).is_match


def test_datetime_when_instants_differ_then_reports_value_changed_in_utc() -> None:
    rule = DatetimeRule(path="/ends_at")

    result = diff_responses(
        {"ends_at": "2026-11-02T10:00:00-03:00"}, {"ends_at": "2026-11-02T10:00:00Z"}, (rule,)
    )

    (difference,) = result.differences
    assert difference.kind is DiffKind.VALUE_CHANGED
    assert difference.detail == "in UTC: 2026-11-02T13:00:00+00:00 vs 2026-11-02T10:00:00+00:00"


def test_datetime_when_candidate_has_no_timezone_then_rule_not_applicable() -> None:
    # A naive datetime is never assumed UTC: that would hide "local time saved as UTC".
    rule = DatetimeRule(path="/ends_at")

    result = diff_responses(
        {"ends_at": "2026-11-02T13:00:00Z"}, {"ends_at": "2026-11-02T13:00:00"}, (rule,)
    )

    (difference,) = result.differences
    assert difference.kind is DiffKind.RULE_NOT_APPLICABLE
    assert "candidate datetime has no timezone" in difference.detail


def test_datetime_when_value_is_not_iso_8601_then_rule_not_applicable() -> None:
    rule = DatetimeRule(path="/ends_at")

    result = diff_responses({"ends_at": "tomorrow"}, {"ends_at": "2026-11-02T13:00:00Z"}, (rule,))

    (difference,) = result.differences
    assert difference.kind is DiffKind.RULE_NOT_APPLICABLE
    assert "baseline is not an ISO 8601 datetime" in difference.detail


# --- unordered -------------------------------------------------------------------------


def test_unordered_when_same_items_in_other_order_then_matches() -> None:
    rule = UnorderedRule(path="/items", key="sku")
    baseline: JsonValue = {"items": [{"sku": "A1", "qty": 1}, {"sku": "B2", "qty": 2}]}
    candidate: JsonValue = {"items": [{"sku": "B2", "qty": 2}, {"sku": "A1", "qty": 1}]}

    assert diff_responses(baseline, candidate, (rule,)).is_match


def test_unordered_when_paired_item_differs_then_location_is_baseline_index() -> None:
    rule = UnorderedRule(path="/items", key="sku")
    baseline: JsonValue = {"items": [{"sku": "A1", "qty": 1}, {"sku": "B2", "qty": 2}]}
    candidate: JsonValue = {"items": [{"sku": "B2", "qty": 5}, {"sku": "A1", "qty": 1}]}

    result = diff_responses(baseline, candidate, (rule,))

    assert result.differences == (Difference(DiffKind.VALUE_CHANGED, at("/items/1/qty"), 2, 5),)


def test_unordered_when_item_dropped_and_added_then_reports_both() -> None:
    rule = UnorderedRule(path="/items", key="sku")
    baseline: JsonValue = {"items": [{"sku": "A1"}, {"sku": "B2"}]}
    candidate: JsonValue = {"items": [{"sku": "C3"}, {"sku": "A1"}]}

    result = diff_responses(baseline, candidate, (rule,))

    assert result.differences == (
        Difference(DiffKind.MISSING_IN_CANDIDATE, at("/items/1"), {"sku": "B2"}, MISSING),
        Difference(DiffKind.ADDED_IN_CANDIDATE, at("/items/0"), MISSING, {"sku": "C3"}),
    )


def test_unordered_when_duplicate_key_then_reports_it_never_silent_match() -> None:
    # SPEC R2: "duplicate key A1 in candidate".
    rule = UnorderedRule(path="/items", key="sku")
    baseline: JsonValue = {"items": [{"sku": "A1"}]}
    candidate: JsonValue = {"items": [{"sku": "A1"}, {"sku": "A1"}]}

    result = diff_responses(baseline, candidate, (rule,))

    assert result.differences == (
        Difference(
            DiffKind.DUPLICATE_KEY, at("/items"), MISSING, "A1", "duplicate key A1 in candidate"
        ),
    )


def test_unordered_when_item_has_no_key_then_rule_not_applicable() -> None:
    rule = UnorderedRule(path="/items", key="sku")

    result = diff_responses({"items": [{"sku": "A1"}]}, {"items": [{"id": 1}]}, (rule,))

    (difference,) = result.differences
    assert difference.kind is DiffKind.RULE_NOT_APPLICABLE
    assert difference.location == at("/items/0")
    assert difference.detail == "candidate item has no key 'sku'"


def test_unordered_when_keys_are_true_and_1_then_they_are_different_items() -> None:
    rule = UnorderedRule(path="/items", key="id")

    assert kinds({"items": [{"id": True}]}, {"items": [{"id": 1}]}, rule) == [
        DiffKind.MISSING_IN_CANDIDATE,
        DiffKind.ADDED_IN_CANDIDATE,
    ]


def test_unordered_combined_with_numeric_on_items_then_both_apply() -> None:
    rules = (
        UnorderedRule(path="/items", key="sku"),
        NumericRule(path="/items/*/price", decimals=2),
    )
    baseline: JsonValue = {"items": [{"sku": "A1", "price": Decimal("1.005")}, {"sku": "B2"}]}
    candidate: JsonValue = {"items": [{"sku": "B2"}, {"sku": "A1", "price": Decimal("1.01")}]}

    result = diff_responses(baseline, candidate, rules)

    assert result.is_match
    assert result.unused_rules == ()


# --- rules together --------------------------------------------------------------------


def test_rules_when_two_value_rules_match_same_path_then_reports_conflict() -> None:
    rules = (NumericRule(path="/x", decimals=2), DatetimeRule(path="/x"))

    result = diff_responses({"x": 1}, {"x": 1}, rules)

    (difference,) = result.differences
    assert difference.kind is DiffKind.RULE_NOT_APPLICABLE
    assert difference.detail == "conflicting rules: NumericRule, DatetimeRule"


def test_rules_when_ignore_and_numeric_match_then_ignore_wins() -> None:
    rules = (IgnoreRule(path="/price"), NumericRule(path="/price", decimals=2))

    result = diff_responses({"price": "x"}, {"price": 1}, rules)

    assert result.is_match
    assert result.unused_rules == ()


def test_rules_when_numeric_path_never_matches_then_reported_unused() -> None:
    stale = NumericRule(path="/old_price", decimals=2)

    assert diff_responses({"a": 1}, {"a": 1}, (stale,)).unused_rules == (stale,)
