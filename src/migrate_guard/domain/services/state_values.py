"""Turn database values into comparable JSON values, and build row keys.

Two engines store the same fact in different types (DECIMAL vs NUMERIC, REAL, DATETIME vs
TIMESTAMPTZ). These functions give both sides one representation before they are compared.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from migrate_guard.domain.value_objects.json_value import JsonValue


class UnsupportedValueError(TypeError):
    pass


def normalize_db_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return _finite(value)
    if isinstance(value, float):
        # repr() is the shortest text that round-trips: 2.675 stays "2.675", not 2.67499...
        return _finite(Decimal(repr(value)))
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            # TIMESTAMPTZ: compare as a UTC instant. A naive DATETIME is kept as written, so a
            # local time saved without offset still shows up as a different value.
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.isoformat()
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return "0x" + bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise UnsupportedValueError(f"unsupported database value type: {type(value).__name__}")


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise UnsupportedValueError(f"non-finite number in the database: {value}")
    return value


def format_row_key(columns: tuple[str, ...], values: tuple[JsonValue, ...]) -> str:
    """`sku=A1`, or `sku=A1,store=3` for a composite key. `%`, `,` and `=` are escaped."""
    parts = []
    for column, value in zip(columns, values, strict=True):
        if value is None:
            raise ValueError(f"key column {column!r} is null")
        parts.append(f"{_escape(column)}={_escape(str(value))}")
    return ",".join(parts)


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace(",", "%2C").replace("=", "%3D")
