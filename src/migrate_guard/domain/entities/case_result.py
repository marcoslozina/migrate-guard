"""The result of one case, and of a whole run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from migrate_guard.domain.entities.difference import Difference
from migrate_guard.domain.value_objects.rules import Rule


class CaseStatus(StrEnum):
    MATCH = "match"
    DIFFERENCE = "difference"
    # The case could not be compared (timeout, connection error...). Never counted as a match.
    ERROR = "error"


@dataclass(frozen=True)
class StatusMismatch:
    baseline: int
    candidate: int


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: CaseStatus
    status_mismatch: StatusMismatch | None = None
    differences: tuple[Difference, ...] = ()
    error: str = ""

    @classmethod
    def failed(cls, name: str, error: str) -> CaseResult:
        return cls(name, CaseStatus.ERROR, error=error)


class ExitCode:
    OK = 0  # every case matched
    DIFFERENCES = 1  # at least one difference or case error
    REFUSED = 2  # invalid config, unreachable target or safety refusal


@dataclass(frozen=True)
class RunResult:
    cases: tuple[CaseResult, ...]
    # Rules that matched nothing in any case of the run: probably stale (SPEC R2).
    unused_rules: tuple[Rule, ...] = ()

    @property
    def exit_code(self) -> int:
        if all(c.status is CaseStatus.MATCH for c in self.cases):
            return ExitCode.OK
        return ExitCode.DIFFERENCES

    def count(self, status: CaseStatus) -> int:
        return sum(1 for c in self.cases if c.status is status)
