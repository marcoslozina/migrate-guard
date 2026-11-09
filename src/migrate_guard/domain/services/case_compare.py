"""Compare the two responses of one case: the status code, then the body with the rules."""

from __future__ import annotations

from migrate_guard.domain.entities.case import HttpResponse
from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, StatusMismatch
from migrate_guard.domain.services.response_diff import ResponseDiff, diff_responses
from migrate_guard.domain.value_objects.rules import Rule


def compare_case(
    name: str, baseline: HttpResponse, candidate: HttpResponse, rules: tuple[Rule, ...]
) -> tuple[CaseResult, ResponseDiff]:
    """The result, plus the raw body diff so the caller can track which rules were used."""
    mismatch = (
        StatusMismatch(baseline.status, candidate.status)
        if baseline.status != candidate.status
        else None
    )
    # The body is compared even when the status differs: both facts help to find the cause.
    body = diff_responses(baseline.body, candidate.body, rules)
    status = CaseStatus.MATCH if mismatch is None and body.is_match else CaseStatus.DIFFERENCE
    return CaseResult(name, status, mismatch, body.differences), body
