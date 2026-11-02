"""Tolerance rules: differences that are expected after a migration."""

from __future__ import annotations

import decimal
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from migrate_guard.domain.value_objects.json_pointer import JsonPointer


class _PathRule(BaseModel):
    """A rule that applies at every location matching `path` (a JSON Pointer, `*` allowed)."""

    model_config = ConfigDict(frozen=True)

    path: str

    @field_validator("path")
    @classmethod
    def _valid_pointer(cls, value: str) -> str:
        JsonPointer.parse(value)
        return value

    @property
    def pointer(self) -> JsonPointer:
        return JsonPointer.parse(self.path)


class IgnoreRule(_PathRule):
    """Skip the value at `path` (and everything under it) on both sides."""


class Rounding(StrEnum):
    HALF_UP = "half_up"  # 2.675 -> 2.68, 2.665 -> 2.67: what most legacy money code does
    HALF_EVEN = "half_even"  # 2.675 -> 2.68, 2.665 -> 2.66: banker's rounding
    DOWN = "down"  # truncate toward zero: 2.679 -> 2.67


_DECIMAL_ROUNDING = {
    Rounding.HALF_UP: decimal.ROUND_HALF_UP,
    Rounding.HALF_EVEN: decimal.ROUND_HALF_EVEN,
    Rounding.DOWN: decimal.ROUND_DOWN,
}


class NumericRule(_PathRule):
    """Compare numbers after rounding both sides to `decimals` places."""

    decimals: int = Field(ge=0, le=18)
    rounding: Rounding = Rounding.HALF_UP

    def normalize(self, value: int | Decimal) -> Decimal:
        """Raises decimal.InvalidOperation if the value has too many digits to round."""
        step = Decimal(1).scaleb(-self.decimals)
        return Decimal(value).quantize(step, rounding=_DECIMAL_ROUNDING[self.rounding])


class UnorderedRule(_PathRule):
    """Compare an array of objects ignoring order, pairing items by the value of `key`."""

    key: str = Field(min_length=1)


class DatetimeRule(_PathRule):
    """Compare ISO 8601 strings as instants: the same moment in another offset is a match."""

    normalize: Literal["utc"] = "utc"


type Rule = IgnoreRule | NumericRule | UnorderedRule | DatetimeRule
