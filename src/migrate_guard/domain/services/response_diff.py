"""Compare two JSON bodies and list every difference that no rule tolerates."""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind, Side
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.rules import (
    DatetimeRule,
    IgnoreRule,
    NumericRule,
    Rule,
    UnorderedRule,
)

type _SideName = Literal["baseline", "candidate"]
type _Scalar = bool | int | Decimal | str | None
type _ItemKey = tuple[str, _Scalar]  # (JSON type, value): keeps True and 1 apart


@dataclass(frozen=True)
class ResponseDiff:
    differences: tuple[Difference, ...]
    unused_rules: tuple[Rule, ...]  # stale rules: shown as a warning (SPEC R2)

    @property
    def is_match(self) -> bool:
        return not self.differences


def diff_responses(baseline: Side, candidate: Side, rules: tuple[Rule, ...] = ()) -> ResponseDiff:
    walker = _Walker(rules)
    walker.walk(baseline, candidate, JsonPointer())
    unused = tuple(r for r in rules if r not in walker.used)
    return ResponseDiff(tuple(walker.out), unused)


@dataclass
class _Walker:
    rules: tuple[Rule, ...]
    used: set[Rule] = field(default_factory=set)
    out: list[Difference] = field(default_factory=list)

    def walk(self, baseline: Side, candidate: Side, at: JsonPointer) -> None:
        # A rule counts as used when its path exists on at least one side, even if it then
        # finds a problem: "never applied" must mean the path matched nothing.
        matching = [r for r in self.rules if at.matches(r.pointer)]
        self.used.update(matching)

        # Ignore goes first, so it also covers a field present on one side only.
        if any(isinstance(r, IgnoreRule) for r in matching):
            return

        if baseline is MISSING or candidate is MISSING:
            kind = (
                DiffKind.MISSING_IN_CANDIDATE
                if candidate is MISSING
                else DiffKind.ADDED_IN_CANDIDATE
            )
            self.out.append(Difference(kind, at, baseline, candidate))
            return

        # Before any rule: a rule tolerates values, never a change in the contract.
        if _json_type(baseline) != _json_type(candidate):
            self.out.append(Difference(DiffKind.TYPE_CHANGED, at, baseline, candidate))
            return

        value_rules = list(dict.fromkeys(matching))  # same rule written twice is fine
        if len(value_rules) > 1:
            names = ", ".join(type(r).__name__ for r in value_rules)
            self._not_applicable(at, baseline, candidate, f"conflicting rules: {names}")
            return
        if value_rules:
            self._apply(value_rules[0], baseline, candidate, at)
            return

        self._compare(baseline, candidate, at)

    def _compare(self, baseline: JsonValue, candidate: JsonValue, at: JsonPointer) -> None:
        if isinstance(baseline, dict) and isinstance(candidate, dict):
            # Baseline order first, then the new keys: the report reads the same on every run.
            keys = [*baseline, *(k for k in candidate if k not in baseline)]
            for key in keys:
                self.walk(baseline.get(key, MISSING), candidate.get(key, MISSING), at.child(key))
            return

        if isinstance(baseline, list) and isinstance(candidate, list):
            # Positional: reordering is a difference unless an `unordered` rule says otherwise.
            for i in range(max(len(baseline), len(candidate))):
                b = baseline[i] if i < len(baseline) else MISSING
                c = candidate[i] if i < len(candidate) else MISSING
                self.walk(b, c, at.child(i))
            return

        # Scalars of the same JSON type. int and Decimal compare by value: 10 == Decimal("10.0").
        if baseline != candidate:
            self.out.append(Difference(DiffKind.VALUE_CHANGED, at, baseline, candidate))

    def _apply(
        self, rule: Rule, baseline: JsonValue, candidate: JsonValue, at: JsonPointer
    ) -> None:
        match rule:
            case NumericRule():
                self._numeric(rule, baseline, candidate, at)
            case DatetimeRule():
                self._datetime(baseline, candidate, at)
            case UnorderedRule():
                self._unordered(rule, baseline, candidate, at)
            case IgnoreRule():  # handled in walk(); here only to keep the match exhaustive
                return

    # --- numeric -------------------------------------------------------------------------

    def _numeric(
        self, rule: NumericRule, baseline: JsonValue, candidate: JsonValue, at: JsonPointer
    ) -> None:
        b, c = _as_number(baseline), _as_number(candidate)
        if b is None or c is None:
            found = _json_type(baseline)
            self._not_applicable(
                at, baseline, candidate, f"numeric rule needs numbers, got {found}"
            )
            return
        try:
            b_rounded, c_rounded = rule.normalize(b), rule.normalize(c)
        except decimal.InvalidOperation:
            detail = f"too many digits to round to {rule.decimals} decimals"
            self._not_applicable(at, baseline, candidate, detail)
            return
        if b_rounded != c_rounded:
            detail = (
                f"rounded to {rule.decimals} decimals ({rule.rounding}): {b_rounded} vs {c_rounded}"
            )
            self.out.append(Difference(DiffKind.VALUE_CHANGED, at, baseline, candidate, detail))

    # --- datetime ------------------------------------------------------------------------

    def _datetime(self, baseline: JsonValue, candidate: JsonValue, at: JsonPointer) -> None:
        if not isinstance(baseline, str) or not isinstance(candidate, str):
            found = _json_type(baseline)
            self._not_applicable(
                at, baseline, candidate, f"datetime rule needs strings, got {found}"
            )
            return
        b, b_problem = _parse_instant(baseline, "baseline")
        c, c_problem = _parse_instant(candidate, "candidate")
        if b is None or c is None:
            detail = "; ".join(p for p in (b_problem, c_problem) if p)
            self._not_applicable(at, baseline, candidate, detail)
            return
        if b != c:  # aware datetimes compare as instants, whatever their offset
            detail = f"in UTC: {b.astimezone(UTC).isoformat()} vs {c.astimezone(UTC).isoformat()}"
            self.out.append(Difference(DiffKind.VALUE_CHANGED, at, baseline, candidate, detail))

    # --- unordered -----------------------------------------------------------------------

    def _unordered(
        self, rule: UnorderedRule, baseline: JsonValue, candidate: JsonValue, at: JsonPointer
    ) -> None:
        if not isinstance(baseline, list) or not isinstance(candidate, list):
            found = _json_type(baseline)
            self._not_applicable(
                at, baseline, candidate, f"unordered rule needs arrays, got {found}"
            )
            return
        b_index = self._index(baseline, rule.key, at, "baseline")
        c_index = self._index(candidate, rule.key, at, "candidate")
        if b_index is None or c_index is None:
            return  # pairing by a broken key would hide differences: stop here

        # Items are located by their own index on each side, so `/items/*/price` still matches.
        for key, (i, b_item) in b_index.items():
            if key in c_index:
                _, c_item = c_index[key]
                self.walk(b_item, c_item, at.child(i))
            else:
                self.walk(b_item, MISSING, at.child(i))
        for key, (j, c_item) in c_index.items():
            if key not in b_index:
                self.walk(MISSING, c_item, at.child(j))

    def _index(
        self, items: list[JsonValue], key: str, at: JsonPointer, side: _SideName
    ) -> dict[_ItemKey, tuple[int, JsonValue]] | None:
        """Items by key value, or None (with the problems reported) if the key is unusable."""
        index: dict[_ItemKey, tuple[int, JsonValue]] = {}
        ok = True
        for i, item in enumerate(items):
            if not isinstance(item, dict) or key not in item:
                self._report_one_side(at.child(i), side, item, f"{side} item has no key {key!r}")
                ok = False
                continue
            value = item[key]
            if isinstance(value, dict | list):
                self._report_one_side(at.child(i), side, item, f"key {key!r} is not a scalar")
                ok = False
                continue
            item_key = (_json_type(value), value)
            if item_key in index:
                self._report_one_side(
                    at, side, value, f"duplicate key {value} in {side}", DiffKind.DUPLICATE_KEY
                )
                ok = False
                continue
            index[item_key] = (i, item)
        return index if ok else None

    # --- reporting -----------------------------------------------------------------------

    def _not_applicable(
        self, at: JsonPointer, baseline: Side, candidate: Side, detail: str
    ) -> None:
        self.out.append(Difference(DiffKind.RULE_NOT_APPLICABLE, at, baseline, candidate, detail))

    def _report_one_side(
        self,
        at: JsonPointer,
        side: _SideName,
        value: JsonValue,
        detail: str,
        kind: DiffKind = DiffKind.RULE_NOT_APPLICABLE,
    ) -> None:
        b, c = (value, MISSING) if side == "baseline" else (MISSING, value)
        self.out.append(Difference(kind, at, b, c, detail))


def _as_number(value: JsonValue) -> int | Decimal | None:
    if isinstance(value, bool):  # bool is an int in Python, not a number in JSON
        return None
    return value if isinstance(value, int | Decimal) else None


def _parse_instant(text: str, side: _SideName) -> tuple[datetime | None, str]:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, f"{side} is not an ISO 8601 datetime: {text!r}"
    if parsed.utcoffset() is None:
        # Assuming UTC here could hide the exact bug we look for (local time saved as UTC).
        return None, f"{side} datetime has no timezone: {text!r}"
    return parsed, ""


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
