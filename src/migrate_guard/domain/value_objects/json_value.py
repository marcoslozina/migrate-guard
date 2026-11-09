"""The JSON values the domain compares."""

from __future__ import annotations

import json
from decimal import Decimal

# Numbers are int or Decimal, never float: the comparison itself must not add rounding errors.
type JsonValue = bool | int | Decimal | str | list[JsonValue] | dict[str, JsonValue] | None


def to_json_text(value: JsonValue) -> str:
    """Compact JSON with Decimal numbers written exactly: 2.675 stays 2.675, never a float."""
    if value is None or isinstance(value, bool | str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{value} is not valid JSON")
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(to_json_text(v) for v in value) + "]"
    return "{" + ",".join(f"{json.dumps(k)}:{to_json_text(v)}" for k, v in value.items()) + "}"
