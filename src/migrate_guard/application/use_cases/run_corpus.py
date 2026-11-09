"""Use case: replay every case against the baseline and the candidate, and compare."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from migrate_guard.domain.entities.case import Case, HttpResponse
from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.state import StateSnapshot
from migrate_guard.domain.ports.state import Seeder, StateError, StateProbe
from migrate_guard.domain.ports.target_runner import TargetError, TargetRunner
from migrate_guard.domain.services.case_compare import compare_case
from migrate_guard.domain.services.state_diff import diff_states
from migrate_guard.domain.value_objects.rules import Rule
from migrate_guard.domain.value_objects.state_rules import StateRule


class RunRefused(Exception):
    """The run did not start: nothing was sent to any target. Maps to exit code 2."""


@dataclass(frozen=True)
class StateCheck:
    """The database side of a run. Seeding is not optional: every case starts from a known state."""

    baseline_seeder: Seeder
    candidate_seeder: Seeder
    baseline_probe: StateProbe
    candidate_probe: StateProbe
    rules: tuple[StateRule, ...] = ()


class _CaseFailed(Exception):
    """One step of a case failed: the case is an error and its remaining steps are skipped."""


@dataclass(frozen=True)
class RunCorpus:
    baseline: TargetRunner
    candidate: TargetRunner
    rules: tuple[Rule, ...] = ()
    allow_mutating: bool = False
    state: StateCheck | None = None

    def execute(self, cases: Sequence[Case]) -> RunResult:
        # Every refusal happens before the first request, so a refused run touches nothing.
        if not cases:
            raise RunRefused("the corpus has no cases")
        mutating = [c.name for c in cases if c.is_mutating]
        if mutating and not self.allow_mutating:
            raise RunRefused(
                "mutating cases need `allow_mutating: true` in the config: " + ", ".join(mutating)
            )
        for side, target in (("baseline", self.baseline), ("candidate", self.candidate)):
            try:
                target.check_reachable()
            except TargetError as e:
                raise RunRefused(f"target unreachable: {side} ({e})") from e

        results: list[CaseResult] = []
        used: set[Rule | StateRule] = set()
        compared_any = False
        for case in cases:
            try:
                result, case_used = self._run_case(case)
            except _CaseFailed as e:
                results.append(CaseResult.failed(case.name, str(e)))
                continue
            results.append(result)
            used.update(case_used)
            compared_any = True

        # With no case compared there is no evidence that a rule is stale: don't warn.
        all_rules: tuple[Rule | StateRule, ...] = (
            *self.rules,
            *(self.state.rules if self.state else ()),
        )
        unused = tuple(r for r in all_rules if r not in used) if compared_any else ()
        return RunResult(tuple(results), unused)

    def _run_case(self, case: Case) -> tuple[CaseResult, set[Rule | StateRule]]:
        # Order (SPEC §6): seed both -> baseline -> probe baseline -> candidate -> probe
        # candidate -> compare. Seeding both first means a failed seed sends no request at all.
        if self.state:
            _step("seed failed: baseline", self.state.baseline_seeder.seed)
            _step("seed failed: candidate", self.state.candidate_seeder.seed)
        b_response = _request("baseline", self.baseline, case)
        b_state = self._probe("baseline", self.state.baseline_probe if self.state else None)
        c_response = _request("candidate", self.candidate, case)
        c_state = self._probe("candidate", self.state.candidate_probe if self.state else None)

        result, body = compare_case(case.name, b_response, c_response, self.rules)
        used: set[Rule | StateRule] = {r for r in self.rules if r not in body.unused_rules}
        if self.state and b_state is not None and c_state is not None:
            state = diff_states(b_state, c_state, self.state.rules)
            used.update(r for r in self.state.rules if r not in state.unused_rules)
            if state.differences:
                result = replace(
                    result, status=CaseStatus.DIFFERENCE, state_differences=state.differences
                )
        return result, used

    @staticmethod
    def _probe(side: str, probe: StateProbe | None) -> StateSnapshot | None:
        if probe is None:
            return None
        return _step(f"state probe failed: {side}", probe.snapshot)


def _request(side: str, target: TargetRunner, case: Case) -> HttpResponse:
    try:
        return target.execute(case.request)
    except TargetError as e:
        raise _CaseFailed(f"{side}: {e}") from e


def _step[T](label: str, action: Callable[[], T]) -> T:
    try:
        return action()
    except StateError as e:
        raise _CaseFailed(f"{label}: {e}") from e
