"""Reporter: the full result as JSON, for tools (SPEC R7). The schema is versioned.

`report_version` changes only when a field is renamed or removed; new fields can appear.
A side that does not exist (`MISSING`) has no `baseline`/`candidate` key, which is not the same
as a key whose value is `null`. Numbers are written exactly: `2.675` stays `2.675`.
"""

from __future__ import annotations

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.difference import MISSING, Difference
from migrate_guard.domain.services.redaction import RedactionPolicy, redact_text
from migrate_guard.domain.value_objects.json_value import JsonValue, to_json_text
from migrate_guard.infrastructure.adapters.reporters.plain_text import (
    STATE_LABEL,
    describe_rule,
    labelled_differences,
)

REPORT_VERSION = 1


def build_json_report(run: RunResult, policy: RedactionPolicy) -> dict[str, JsonValue]:
    return {
        "report_version": REPORT_VERSION,
        "summary": {
            "cases": len(run.cases),
            **{status.value: run.count(status) for status in CaseStatus},
            "exit_code": run.exit_code,
        },
        "cases": [_case(case, policy) for case in run.cases],
        "unused_rules": [describe_rule(rule) for rule in run.unused_rules],
    }


def render_json_report(run: RunResult, policy: RedactionPolicy) -> str:
    return to_json_text(build_json_report(run, policy)) + "\n"


def _case(case: CaseResult, policy: RedactionPolicy) -> dict[str, JsonValue]:
    labelled = labelled_differences(case, policy)
    mismatch = case.status_mismatch
    return {
        "name": case.name,
        "status": case.status.value,
        "error": redact_text(case.error) if case.status is CaseStatus.ERROR else None,
        "status_mismatch": (
            {"baseline": mismatch.baseline, "candidate": mismatch.candidate} if mismatch else None
        ),
        "differences": [_difference(d) for label, d in labelled if label != STATE_LABEL],
        "state_differences": [_difference(d) for label, d in labelled if label == STATE_LABEL],
    }


def _difference(diff: Difference) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {"kind": diff.kind.value, "path": str(diff.location)}
    if diff.baseline is not MISSING:
        out["baseline"] = diff.baseline
    if diff.candidate is not MISSING:
        out["candidate"] = diff.candidate
    if diff.detail:
        out["detail"] = diff.detail
    return out
