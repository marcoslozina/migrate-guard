"""Reporter: a readable summary in the terminal, with every value redacted first."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.difference import MISSING, Side
from migrate_guard.domain.services.redaction import RedactionPolicy, redact_difference
from migrate_guard.domain.value_objects.json_value import to_json_text
from migrate_guard.domain.value_objects.rules import Rule

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
            self._console.print(Text(f"⚠ rule never applied: {_describe(rule)}", style="yellow"))
        self._console.print()
        self._console.print(self._summary(run))

    def refused(self, reason: str) -> None:
        self._console.print(Text(f"refused: {reason}", style="bold red"))

    def _case(self, case: CaseResult) -> None:
        mark, color = _MARK[case.status]
        self._console.print(Text.assemble((f"{mark} ", color), (case.name, "bold")))
        if case.status is CaseStatus.ERROR:
            self._console.print(Text(f"    error: {case.error}", style="yellow"))
            return
        if case.status_mismatch:
            m = case.status_mismatch
            self._console.print(Text(f"    status  {m.baseline} → {m.candidate}", style="red"))
        for diff in case.differences[: self._max]:
            d = redact_difference(diff, self._policy)
            line = Text("    ")
            line.append(str(d.location) or "/", style="cyan")
            line.append(f"  {_show(d.baseline)} → {_show(d.candidate)}")
            line.append(f"  {d.kind}", style="dim")
            if d.detail:
                line.append(f"  ({d.detail})", style="dim")
            self._console.print(line)
        hidden = len(case.differences) - self._max
        if hidden > 0:
            self._console.print(Text(f"    … and {hidden} more", style="dim"))

    def _summary(self, run: RunResult) -> Text:
        total = len(run.cases)
        return Text.assemble(
            (f"{total} case{'s' if total != 1 else ''}: ", "bold"),
            (f"{run.count(CaseStatus.MATCH)} match", "green"),
            " · ",
            (f"{run.count(CaseStatus.DIFFERENCE)} with differences", "red"),
            " · ",
            (f"{run.count(CaseStatus.ERROR)} errors", "yellow"),
        )


def _show(side: Side) -> str:
    if side is MISSING:
        return "(missing)"
    return to_json_text(side)


def _describe(rule: Rule) -> str:
    fields = rule.model_dump(exclude={"path"}, mode="json")
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    name = type(rule).__name__.removesuffix("Rule").lower()
    return f"{name} {rule.path}" + (f" ({extra})" if extra else "")
