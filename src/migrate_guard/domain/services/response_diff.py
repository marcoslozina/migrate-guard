"""Compare two JSON bodies and list every difference that no rule tolerates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind, Side
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.rules import IgnoreRule


@dataclass(frozen=True)
class ResponseDiff:
    differences: tuple[Difference, ...]
    unused_rules: tuple[IgnoreRule, ...]  # stale rules: shown as a warning (SPEC R2)

    @property
    def is_match(self) -> bool:
        return not self.differences


def diff_responses(
    baseline: Side, candidate: Side, rules: tuple[IgnoreRule, ...] = ()
) -> ResponseDiff:
    differences: list[Difference] = []
    used: set[IgnoreRule] = set()
    _walk(baseline, candidate, JsonPointer(), rules, used, differences)
    return ResponseDiff(tuple(differences), tuple(r for r in rules if r not in used))


def _walk(
    baseline: Side,
    candidate: Side,
    at: JsonPointer,
    rules: tuple[IgnoreRule, ...],
    used: set[IgnoreRule],
    out: list[Difference],
) -> None:
    # Rules go first, so ignoring a field also covers it being present on one side only.
    matching = [r for r in rules if at.matches(r.pointer)]
    if matching:
        used.update(matching)
        return

    if baseline is MISSING or candidate is MISSING:
        kind = (
            DiffKind.MISSING_IN_CANDIDATE if candidate is MISSING else DiffKind.ADDED_IN_CANDIDATE
        )
        out.append(Difference(kind, at, baseline, candidate))
        return

    if _json_type(baseline) != _json_type(candidate):
        out.append(Difference(DiffKind.TYPE_CHANGED, at, baseline, candidate))
        return

    if isinstance(baseline, dict) and isinstance(candidate, dict):
        # Baseline order first, then the new keys: the report reads the same on every run.
        keys = [*baseline, *(k for k in candidate if k not in baseline)]
        for key in keys:
            b, c = baseline.get(key, MISSING), candidate.get(key, MISSING)
            _walk(b, c, at.child(key), rules, used, out)
        return

    if isinstance(baseline, list) and isinstance(candidate, list):
        # Positional: reordering is a difference unless an `unordered` rule says otherwise (M1).
        for i in range(max(len(baseline), len(candidate))):
            b = baseline[i] if i < len(baseline) else MISSING
            c = candidate[i] if i < len(candidate) else MISSING
            _walk(b, c, at.child(i), rules, used, out)
        return

    # Scalars of the same JSON type. int and Decimal compare by value: 10 == Decimal("10.0").
    if baseline != candidate:
        out.append(Difference(DiffKind.VALUE_CHANGED, at, baseline, candidate))


def _json_type(value: Side) -> str:
    # bool before int: in Python, True == 1, but in JSON they are different types.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    # A float here means the JSON was parsed without parse_float=Decimal: fail loudly.
    raise TypeError(f"unsupported JSON value {type(value).__name__}: parse numbers as Decimal")
