from __future__ import annotations

import pytest

from migrate_guard.application.use_cases.run_corpus import RunCorpus, StateCheck
from migrate_guard.domain.entities.case import Case, HttpRequest, HttpResponse
from migrate_guard.domain.entities.case_result import CaseStatus, ExitCode
from migrate_guard.domain.entities.state import StateSnapshot, TableSnapshot
from migrate_guard.domain.ports.state import Seeder, StateError, StateProbe
from migrate_guard.domain.ports.target_runner import TargetRunner
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.state_rules import IgnoreColumnRule


class Log:
    def __init__(self) -> None:
        self.steps: list[str] = []


class FakeTarget(TargetRunner):
    def __init__(self, side: str, log: Log) -> None:
        self.side, self.log = side, log

    def check_reachable(self) -> None:
        pass

    def execute(self, request: HttpRequest) -> HttpResponse:
        self.log.steps.append(f"run {self.side}")
        return HttpResponse(200, (), {"ok": True})


class FakeSeeder(Seeder):
    def __init__(self, side: str, log: Log, *, fails: bool = False) -> None:
        self.side, self.log, self.fails = side, log, fails

    def seed(self) -> None:
        self.log.steps.append(f"seed {self.side}")
        if self.fails:
            raise StateError("syntax error near DROP")


class FakeProbe(StateProbe):
    def __init__(self, side: str, log: Log, row: dict[str, JsonValue], *, fails: bool = False):
        self.side, self.log, self.row, self.fails = side, log, row, fails

    def snapshot(self) -> StateSnapshot:
        self.log.steps.append(f"probe {self.side}")
        if self.fails:
            raise StateError("state too large: prices")
        return StateSnapshot({"prices": TableSnapshot({"sku=A1": self.row})})


def run(
    *,
    b_row: dict[str, JsonValue] | None = None,
    c_row: dict[str, JsonValue] | None = None,
    seed_fails: str = "",
    probe_fails: str = "",
    rules: tuple[IgnoreColumnRule, ...] = (),
) -> tuple[Log, object]:
    log = Log()
    state = StateCheck(
        baseline_seeder=FakeSeeder("baseline", log, fails=seed_fails == "baseline"),
        candidate_seeder=FakeSeeder("candidate", log, fails=seed_fails == "candidate"),
        baseline_probe=FakeProbe(
            "baseline", log, b_row or {"p": 1}, fails=probe_fails == "baseline"
        ),
        candidate_probe=FakeProbe(
            "candidate", log, c_row or {"p": 1}, fails=probe_fails == "candidate"
        ),
        rules=rules,
    )
    use_case = RunCorpus(FakeTarget("baseline", log), FakeTarget("candidate", log), state=state)
    return log, use_case.execute([Case("a", HttpRequest("GET", "/a"))])


def test_run_follows_the_order_of_operations_of_the_spec() -> None:
    log, _ = run()

    assert log.steps == [
        "seed baseline",
        "seed candidate",
        "run baseline",
        "probe baseline",
        "run candidate",
        "probe candidate",
    ]


def test_run_when_only_the_state_differs_then_the_case_is_a_difference() -> None:
    _, result = run(b_row={"last_priced_at": None}, c_row={"last_priced_at": "2026-11-09"})

    case = result.cases[0]  # type: ignore[attr-defined]
    assert case.status is CaseStatus.DIFFERENCE
    assert case.differences == ()
    assert [str(d.location) for d in case.state_differences] == ["/prices/sku=A1/last_priced_at"]
    assert result.exit_code == ExitCode.DIFFERENCES  # type: ignore[attr-defined]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_run_when_a_seed_fails_then_no_request_is_sent(side: str) -> None:
    log, result = run(seed_fails=side)

    assert not any(step.startswith("run") for step in log.steps)
    case = result.cases[0]  # type: ignore[attr-defined]
    assert case.status is CaseStatus.ERROR
    assert case.error == f"seed failed: {side}: syntax error near DROP"


def test_run_when_the_baseline_probe_fails_then_the_candidate_is_not_run() -> None:
    log, result = run(probe_fails="baseline")

    assert "run candidate" not in log.steps
    assert result.cases[0].status is CaseStatus.ERROR  # type: ignore[attr-defined]


def test_run_when_responses_match_but_a_probe_fails_then_it_is_an_error_not_a_match() -> None:
    _, result = run(probe_fails="candidate")

    case = result.cases[0]  # type: ignore[attr-defined]
    assert case.status is CaseStatus.ERROR
    assert case.error == "state probe failed: candidate: state too large: prices"


def test_run_tracks_state_rules_that_never_applied() -> None:
    stale = IgnoreColumnRule(table="prices", column="gone")
    used = IgnoreColumnRule(table="prices", column="p")

    _, result = run(rules=(stale, used))

    assert result.unused_rules == (stale,)  # type: ignore[attr-defined]
