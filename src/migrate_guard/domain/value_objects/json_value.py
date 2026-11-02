"""The JSON values the domain compares."""

from __future__ import annotations

from decimal import Decimal

# Numbers are int or Decimal, never float: the comparison itself must not add rounding errors.
type JsonValue = bool | int | Decimal | str | list[JsonValue] | dict[str, JsonValue] | None
