"""Tolerance rules for the database state. Each one becomes a path rule on the state document."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from migrate_guard.domain.value_objects.json_pointer import WILDCARD, JsonPointer
from migrate_guard.domain.value_objects.rules import IgnoreRule, NumericRule, Rounding, Rule


class _ColumnRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str = Field(min_length=1)  # logical table name
    column: str = Field(min_length=1)  # logical column name

    def _path(self) -> str:
        # Every row of the table: /<table>/*/<column>, escaped as a JSON Pointer.
        return str(JsonPointer((self.table, WILDCARD, self.column)))


class IgnoreColumnRule(_ColumnRule):
    """Skip a column in every row (an `updated_at`, a surrogate id...)."""

    def to_path_rule(self) -> Rule:
        return IgnoreRule(path=self._path())


class StateNumericRule(_ColumnRule):
    """Compare a numeric column after rounding both sides to `decimals` places."""

    decimals: int = Field(ge=0, le=18)
    rounding: Rounding = Rounding.HALF_UP

    def to_path_rule(self) -> Rule:
        return NumericRule(path=self._path(), decimals=self.decimals, rounding=self.rounding)


type StateRule = IgnoreColumnRule | StateNumericRule
