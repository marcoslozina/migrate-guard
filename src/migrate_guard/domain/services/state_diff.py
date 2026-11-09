"""Compare the database state two targets left behind, with the state rules."""

from __future__ import annotations

from dataclasses import dataclass

from migrate_guard.domain.entities.difference import Difference
from migrate_guard.domain.entities.state import StateSnapshot
from migrate_guard.domain.services.response_diff import diff_responses
from migrate_guard.domain.value_objects.state_rules import StateRule


@dataclass(frozen=True)
class StateDiff:
    # Located at /<table>/<row key>/<column>. A row only on one side is reported whole:
    # missing_in_candidate (the rewrite didn't write it) or added_in_candidate.
    differences: tuple[Difference, ...]
    unused_rules: tuple[StateRule, ...]


def diff_states(
    baseline: StateSnapshot, candidate: StateSnapshot, rules: tuple[StateRule, ...] = ()
) -> StateDiff:
    path_rules = {rule: rule.to_path_rule() for rule in rules}
    result = diff_responses(baseline.as_json(), candidate.as_json(), tuple(path_rules.values()))
    unused = tuple(rule for rule, path in path_rules.items() if path in result.unused_rules)
    return StateDiff(result.differences, unused)
