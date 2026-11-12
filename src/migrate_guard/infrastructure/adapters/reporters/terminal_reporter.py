"""Reporter: a readable summary in the terminal, with every value redacted first."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.difference import Difference
from migrate_guard.domain.services.redaction import RedactionPolicy, redact_text
from migrate_guard.infrastructure.adapters.reporters.plain_text import (
    describe_rule,
    labelled_differences,
    show_side,
)

DEFAULT_MAX_DIFFERENCES = 10

_MARK = {
    CaseStatus.MATCH: ("✓", "green"),
    CaseStatus.DIFFERENCE: ("✗", "red"),
    CaseStatus.ERROR: ("!", "yellow"),
}


class TerminalReporter:
    def __init__(
        self,
        console: Console,
        policy: RedactionPolicy,
        max_differences: int = DEFAULT_MAX_DIFFERENCES,
    ) -> None:
        self._console = console
        self._policy = policy
        self._max = max_differences

    def report(self, run: RunResult) -> None:
        for case in run.cases:
            self._case(case)
        for rule in run.unused_rules:
            self._console.print(
                Text(f"⚠ rule never applied: {describe_rule(rule)}", style="yellow")
            )
        self._console.print()
        self._console.print(self._summary(run))

    def refused(self, reason: str) -> None:
        self._console.print(Text(f"refused: {reason}", style="bold red"))

    def _case(self, case: CaseResult) -> None:
        mark, color = _MARK[case.status]
        self._console.print(Text.assemble((f"{mark} ", color), (case.name, "bold")))
        if case.status is CaseStatus.ERROR:
            self._console.print(Text(f"    error: {redact_text(case.error)}", style="yellow"))
            return
        if case.status_mismatch:
            m = case.status_mismatch
            self._console.print(Text(f"    status  {m.baseline} → {m.candidate}", style="red"))
        diffs = labelled_differences(case, self._policy)
        for label, diff in diffs[: self._max]:
            self._console.print(_line(label, diff))
        if len(diffs) > self._max:
            self._console.print(Text(f"    … and {len(diffs) - self._max} more", style="dim"))

    @staticmethod
    def _summary(run: RunResult) -> Text:
        total = len(run.cases)
        return Text.assemble(
            (f"{total} case{'s' if total != 1 else ''}: ", "bold"),
            (f"{run.count(CaseStatus.MATCH)} match", "green"),
            " · ",
            (f"{run.count(CaseStatus.DIFFERENCE)} with differences", "red"),
            " · ",
            (f"{run.count(CaseStatus.ERROR)} errors", "yellow"),
        )


def _line(label: str, diff: Difference) -> Text:
    line = Text("    ")
    if label:
        line.append(label, style="magenta")
    line.append(str(diff.location) or "/", style="cyan")
    line.append(f"  {show_side(diff.baseline)} → {show_side(diff.candidate)}")
    line.append(f"  {diff.kind}", style="dim")
    if diff.detail:
        line.append(f"  ({diff.detail})", style="dim")
    return line
