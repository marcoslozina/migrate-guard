"""Tolerance rules: differences that are expected after a migration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from migrate_guard.domain.value_objects.json_pointer import JsonPointer


class IgnoreRule(BaseModel):
    """Skip the value at `path` (and everything under it) on both sides."""

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
