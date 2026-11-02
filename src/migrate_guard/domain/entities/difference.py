"""One mismatch between the baseline and the candidate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Literal

from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.json_value import JsonValue


class _Missing(Enum):
    MISSING = "missing"

    def __repr__(self) -> str:
        return "MISSING"


# A key that does not exist, which is not the same as a key whose value is null.
MISSING = _Missing.MISSING
type Side = JsonValue | Literal[_Missing.MISSING]


class DiffKind(StrEnum):
    VALUE_CHANGED = "value_changed"  # same type, different value: 2.68 vs 2.67
    TYPE_CHANGED = "type_changed"  # "10" vs 10, null vs 0
    MISSING_IN_CANDIDATE = "missing_in_candidate"  # the rewrite dropped a field
    ADDED_IN_CANDIDATE = "added_in_candidate"  # the rewrite added a field
    DUPLICATE_KEY = "duplicate_key"  # an `unordered` array has two items with the same key
    # A rule matched but can't read the value (bad datetime, no timezone, missing key...).
    # Never a silent match: the rule's assumption is broken, so a human has to look.
    RULE_NOT_APPLICABLE = "rule_not_applicable"


@dataclass(frozen=True)
class Difference:
    kind: DiffKind
    location: JsonPointer
    baseline: Side
    candidate: Side
    detail: str = ""  # why, in words, when the kind alone doesn't say it
