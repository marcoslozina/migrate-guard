from __future__ import annotations

import pytest

from migrate_guard.domain.value_objects.json_pointer import JsonPointer


def test_parse_when_tokens_are_escaped_then_round_trips() -> None:
    pointer = JsonPointer.parse("/a~1b/m~0n/~01")

    assert pointer.tokens == ("a/b", "m~n", "~1")
    assert str(pointer) == "/a~1b/m~0n/~01"


def test_parse_when_empty_then_points_to_root() -> None:
    assert JsonPointer.parse("") == JsonPointer()
    assert str(JsonPointer()) == ""


def test_parse_when_missing_leading_slash_then_raises() -> None:
    with pytest.raises(ValueError, match="start with '/'"):
        JsonPointer.parse("items/0")


def test_matches_when_pattern_has_wildcard_then_matches_any_token() -> None:
    pattern = JsonPointer.parse("/items/*/price")

    assert JsonPointer.parse("/items/3/price").matches(pattern)
    assert not JsonPointer.parse("/items/3/sku").matches(pattern)
    assert not JsonPointer.parse("/items/3").matches(pattern)
