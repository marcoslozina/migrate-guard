"""Use case: replay every case against the baseline and the candidate, and compare."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from migrate_guard.domain.entities.case import Case
from migrate_guard.domain.entities.case_result import CaseResult, RunResult
from migrate_guard.domain.ports.target_runner import TargetError, TargetRunner
from migrate_guard.domain.services.case_compare import compare_case
from migrate_guard.domain.value_objects.rules import Rule


class RunRefused(Exception):
    """The run did not start: nothing was sent to any target. Maps to exit code 2."""


@dataclass(frozen=True)
class RunCorpus:
    baseline: TargetRunner
    candidate: TargetRunner
    rules: tuple[Rule, ...] = ()
    allow_mutating: bool = False

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
        used: set[Rule] = set()
        compared_any = False
        for case in cases:
            # Baseline first, candidate second. If one step fails, the case stops there (SPEC §6).
            try:
                b = self.baseline.execute(case.request)
            except TargetError as e:
                results.append(CaseResult.failed(case.name, f"baseline: {e}"))
                continue
            try:
                c = self.candidate.execute(case.request)
            except TargetError as e:
                results.append(CaseResult.failed(case.name, f"candidate: {e}"))
                continue
            result, body = compare_case(case.name, b, c, self.rules)
            results.append(result)
            used.update(r for r in self.rules if r not in body.unused_rules)
            compared_any = True

        # With no case compared there is no evidence that a rule is stale: don't warn.
        unused = tuple(r for r in self.rules if r not in used) if compared_any else ()
        return RunResult(tuple(results), unused)
