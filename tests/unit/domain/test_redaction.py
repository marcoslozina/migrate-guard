from __future__ import annotations

from decimal import Decimal

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind
from migrate_guard.domain.services.redaction import (
    REDACTED,
    RedactionPolicy,
    redact_difference,
    redact_headers,
    redact_text,
    redact_value,
)
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.json_value import JsonValue

POLICY = RedactionPolicy()
VISA_TEST_CARD = "4111111111111111"  # a public test number, Luhn-valid


def test_redact_headers_hides_auth_and_cookies_whatever_the_case() -> None:
    headers = (("Authorization", "Bearer abc"), ("set-cookie", "s=1"), ("Accept", "json"))

    assert redact_headers(headers, POLICY) == (
        ("Authorization", REDACTED),
        ("set-cookie", REDACTED),
        ("Accept", "json"),
    )


def test_redact_value_hides_sensitive_fields_at_any_depth() -> None:
    body: JsonValue = {
        "user": {"name": "Ana", "Password": "hunter2", "access_token": "t"},
        "items": [],
    }

    assert redact_value(body, POLICY) == {
        "user": {"name": "Ana", "Password": REDACTED, "access_token": REDACTED},
        "items": [],
    }


def test_redact_value_hides_card_numbers_in_strings_and_numbers() -> None:
    assert redact_text("paid with 4111 1111 1111 1111 ok") == f"paid with {REDACTED} ok"
    assert redact_value(int(VISA_TEST_CARD), POLICY) == REDACTED


def test_redact_value_keeps_long_numbers_that_are_not_cards() -> None:
    not_luhn = "4111111111111112"

    assert redact_text(f"order {not_luhn}") == f"order {not_luhn}"
    assert redact_value(Decimal("2.675"), POLICY) == Decimal("2.675")


def test_redact_value_uses_extra_patterns_from_the_policy() -> None:
    policy = RedactionPolicy(("cuit",))

    assert redact_value({"cuit": "20-1234", "name": "Ana"}, policy) == {
        "cuit": REDACTED,
        "name": "Ana",
    }


def test_redact_difference_under_a_sensitive_field_hides_values_and_detail() -> None:
    diff = Difference(
        DiffKind.VALUE_CHANGED,
        JsonPointer.parse("/auth/token"),
        "old-secret",
        MISSING,
        "detail quoting old-secret",
    )

    redacted = redact_difference(diff, POLICY)

    assert (redacted.baseline, redacted.candidate, redacted.detail) == (
        REDACTED,
        MISSING,
        REDACTED,
    )


def test_redact_difference_elsewhere_keeps_the_values() -> None:
    diff = Difference(
        DiffKind.VALUE_CHANGED, JsonPointer.parse("/total"), Decimal("85"), Decimal("85.5")
    )

    assert redact_difference(diff, POLICY) == diff
