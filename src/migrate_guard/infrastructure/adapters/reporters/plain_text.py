"""Plain-text pieces shared by every reporter: one line per difference, one name per rule.

Everything that leaves this module is already redacted (SPEC §7).
"""

from __future__ import annotations

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.difference import MISSING, Difference, Side
from migrate_guard.domain.services.redaction import (
    RedactionPolicy,
    redact_difference,
    redact_text,
)
from migrate_guard.domain.value_objects.json_value import to_json_text
from migrate_guard.domain.value_objects.rules import Rule
from migrate_guard.domain.value_objects.state_rules import (
    IgnoreColumnRule,
    StateNumericRule,
    StateRule,
)

STATE_LABEL = "state "


def show_side(side: Side) -> str:
    if side is MISSING:
        return "(missing)"
    return to_json_text(side)


def difference_line(label: str, diff: Difference) -> str:
    """`diff` must be redacted already: see `labelled_differences`."""
    line = f"{label}{str(diff.location) or '/'}  {show_side(diff.baseline)} → "
    line += f"{show_side(diff.candidate)}  {diff.kind}"
    return line + (f"  ({diff.detail})" if diff.detail else "")


def labelled_differences(case: CaseResult, policy: RedactionPolicy) -> list[tuple[str, Difference]]:
    """Response differences first, then state ones, each redacted and tagged with its label."""
    return [
        (label, redact_difference(diff, policy))
        for label, diffs in (("", case.differences), (STATE_LABEL, case.state_differences))
        for diff in diffs
    ]


def case_lines(case: CaseResult, policy: RedactionPolicy) -> list[str]:
    """Every line that explains why a case did not match. Empty for a match."""
    if case.status is CaseStatus.ERROR:
        return [f"error: {redact_text(case.error)}"]
    lines = []
    if case.status_mismatch:
        lines.append(f"status  {case.status_mismatch.baseline} → {case.status_mismatch.candidate}")
    lines.extend(difference_line(label, d) for label, d in labelled_differences(case, policy))
    return lines


def summary_text(run: RunResult) -> str:
    total = len(run.cases)
    return (
        f"{total} case{'s' if total != 1 else ''}: {run.count(CaseStatus.MATCH)} match · "
        f"{run.count(CaseStatus.DIFFERENCE)} with differences · "
        f"{run.count(CaseStatus.ERROR)} errors"
    )


def describe_rule(rule: Rule | StateRule) -> str:
    if isinstance(rule, IgnoreColumnRule):
        return f"state ignore_column {rule.table}.{rule.column}"
    if isinstance(rule, StateNumericRule):
        extra = rule.model_dump(exclude={"table", "column"}, mode="json")
        args = " ".join(f"{k}={v}" for k, v in extra.items())
        return f"state numeric {rule.table}.{rule.column} ({args})"
    name = type(rule).__name__.removesuffix("Rule").lower()
    fields = rule.model_dump(exclude={"path"}, mode="json")
    extra_text = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"{name} {rule.path}" + (f" ({extra_text})" if extra_text else "")
