from __future__ import annotations

from decimal import Decimal

import pytest

from migrate_guard.application.use_cases.run_corpus import RunCorpus, RunRefused
from migrate_guard.domain.entities.case import Case, HttpRequest, HttpResponse
from migrate_guard.domain.entities.case_result import CaseStatus, ExitCode, StatusMismatch
from migrate_guard.domain.ports.target_runner import TargetError, TargetRunner
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.rules import IgnoreRule, NumericRule


class FakeTarget(TargetRunner):
    """Answers by request path. A path mapped to an exception raises it."""

    def __init__(
        self,
        answers: dict[str, HttpResponse | TargetError],
        *,
        reachable: bool = True,
    ) -> None:
        self.answers = answers
        self.reachable = reachable
        self.sent: list[HttpRequest] = []

    def check_reachable(self) -> None:
        if not self.reachable:
            raise TargetError("connection refused")

    def execute(self, request: HttpRequest) -> HttpResponse:
        self.sent.append(request)
        answer = self.answers[request.path]
        if isinstance(answer, TargetError):
            raise answer
        return answer


def ok(body: JsonValue, status: int = 200) -> HttpResponse:
    return HttpResponse(status, (), body)


def get(name: str, path: str | None = None) -> Case:
    return Case(name, HttpRequest("GET", path or f"/{name}"))


def test_run_when_every_response_matches_then_exit_code_is_0() -> None:
    baseline = FakeTarget({"/a": ok({"price": Decimal("2.68")})})
    candidate = FakeTarget({"/a": ok({"price": Decimal("2.68")})})

    result = RunCorpus(baseline, candidate).execute([get("a")])

    assert [c.status for c in result.cases] == [CaseStatus.MATCH]
    assert result.exit_code == ExitCode.OK


def test_run_when_a_body_differs_then_exit_code_is_1() -> None:
    baseline = FakeTarget({"/a": ok({"price": Decimal("2.68")})})
    candidate = FakeTarget({"/a": ok({"price": Decimal("2.67")})})

    result = RunCorpus(baseline, candidate).execute([get("a")])

    assert result.cases[0].status is CaseStatus.DIFFERENCE
    assert result.exit_code == ExitCode.DIFFERENCES


def test_run_when_only_the_status_differs_then_it_is_a_difference() -> None:
    baseline = FakeTarget({"/a": ok({}, status=200)})
    candidate = FakeTarget({"/a": ok({}, status=404)})

    result = RunCorpus(baseline, candidate).execute([get("a")])

    assert result.cases[0].status is CaseStatus.DIFFERENCE
    assert result.cases[0].status_mismatch == StatusMismatch(200, 404)


def test_run_when_candidate_is_unreachable_then_refuses_before_any_case() -> None:
    baseline = FakeTarget({"/a": ok({})})
    candidate = FakeTarget({"/a": ok({})}, reachable=False)

    with pytest.raises(RunRefused, match="target unreachable: candidate"):
        RunCorpus(baseline, candidate).execute([get("a")])

    assert baseline.sent == []


def test_run_when_a_case_times_out_then_it_is_an_error_and_the_run_continues() -> None:
    baseline = FakeTarget({"/slow": TargetError("timeout"), "/b": ok({})})
    candidate = FakeTarget({"/slow": ok({}), "/b": ok({})})

    result = RunCorpus(baseline, candidate).execute([get("slow"), get("b")])

    assert [c.status for c in result.cases] == [CaseStatus.ERROR, CaseStatus.MATCH]
    assert result.cases[0].error == "baseline: timeout"
    assert result.exit_code == ExitCode.DIFFERENCES


def test_run_when_baseline_fails_then_candidate_is_not_called() -> None:
    baseline = FakeTarget({"/a": TargetError("timeout")})
    candidate = FakeTarget({"/a": ok({})})

    RunCorpus(baseline, candidate).execute([get("a")])

    assert candidate.sent == []


def test_run_when_mutating_case_is_not_allowed_then_refuses_and_sends_nothing() -> None:
    baseline = FakeTarget({"/cart": ok({})})
    candidate = FakeTarget({"/cart": ok({})})
    post = Case("price cart", HttpRequest("POST", "/cart"))

    with pytest.raises(RunRefused, match="allow_mutating"):
        RunCorpus(baseline, candidate).execute([post])

    assert baseline.sent == candidate.sent == []


def test_run_when_a_get_is_flagged_mutating_then_it_also_needs_permission() -> None:
    target = FakeTarget({"/a": ok({})})
    flagged = Case("writes on read", HttpRequest("GET", "/a"), mutating=True)

    with pytest.raises(RunRefused):
        RunCorpus(target, target).execute([flagged])


def test_run_when_mutating_is_allowed_then_runs_the_case() -> None:
    target = FakeTarget({"/cart": ok({})})
    post = Case("price cart", HttpRequest("POST", "/cart"))

    result = RunCorpus(target, target, allow_mutating=True).execute([post])

    assert result.exit_code == ExitCode.OK


def test_run_when_the_corpus_is_empty_then_refuses() -> None:
    target = FakeTarget({})

    with pytest.raises(RunRefused, match="no cases"):
        RunCorpus(target, target).execute([])


def test_run_reports_a_rule_as_unused_only_if_no_case_used_it() -> None:
    target_b = FakeTarget({"/a": ok({"id": "1"}), "/b": ok({"price": Decimal("1.00")})})
    target_c = FakeTarget({"/a": ok({"id": "2"}), "/b": ok({"price": Decimal("1.001")})})
    used_in_a = IgnoreRule(path="/id")
    used_in_b = NumericRule(path="/price", decimals=2)
    stale = IgnoreRule(path="/old_field")

    result = RunCorpus(target_b, target_c, (used_in_a, used_in_b, stale)).execute(
        [get("a"), get("b")]
    )

    assert result.exit_code == ExitCode.OK
    assert result.unused_rules == (stale,)


def test_run_when_no_case_was_compared_then_no_rule_is_reported_unused() -> None:
    failing = FakeTarget({"/a": TargetError("timeout")})

    result = RunCorpus(failing, failing, (IgnoreRule(path="/id"),)).execute([get("a")])

    assert result.unused_rules == ()
