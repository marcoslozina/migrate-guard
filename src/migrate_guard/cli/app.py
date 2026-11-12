"""The `migrate-guard` command line."""

from __future__ import annotations

import os
import sys
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from migrate_guard.application.use_cases.run_corpus import RunRefused
from migrate_guard.domain.entities.case_result import ExitCode
from migrate_guard.domain.services.redaction import RedactionPolicy
from migrate_guard.infrastructure.adapters.reporters.json_reporter import render_json_report
from migrate_guard.infrastructure.adapters.reporters.junit_reporter import render_junit_report
from migrate_guard.infrastructure.adapters.reporters.terminal_reporter import TerminalReporter
from migrate_guard.infrastructure.adapters.targets.http_target import UnsafeTargetError
from migrate_guard.infrastructure.config.loader import ConfigError, load_config
from migrate_guard.infrastructure.config.state_config import StateConfigError
from migrate_guard.infrastructure.wiring import select_cases, wire

app = typer.Typer(
    help="Catch behavior changes when you rewrite a legacy service.",
    no_args_is_help=True,
    add_completion=False,
)


class ReportFormat(StrEnum):
    TERMINAL = "terminal"
    JSON = "json"
    JUNIT = "junit"


_RENDER = {ReportFormat.JSON: render_json_report, ReportFormat.JUNIT: render_junit_report}


@app.callback()
def _root() -> None:
    """Keeps `run` as a subcommand even while it is the only one."""


@app.command()
def run(
    config: Annotated[
        Path, typer.Option("--config", "-c", help="Path to migrate-guard.yaml.")
    ] = Path("migrate-guard.yaml"),
    case: Annotated[
        list[str] | None, typer.Option("--case", help="Run only this case (repeatable).")
    ] = None,
    allow_remote: Annotated[
        bool, typer.Option("--allow-remote", help="Allow targets that are not loopback.")
    ] = False,
    report: Annotated[
        ReportFormat,
        typer.Option(
            "--report",
            help="json or junit go to --output (plus the terminal summary), or alone to stdout.",
        ),
    ] = ReportFormat.TERMINAL,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="File for the json or junit report.")
    ] = None,
) -> None:
    """Replay the corpus against the baseline and the candidate, and compare responses and state.

    Exit codes: 0 all match, 1 differences or case errors, 2 refused (config, target, safety).
    """
    # Refusals go to stderr, so stdout stays a valid report when it is one.
    errors = TerminalReporter(Console(stderr=True, highlight=False), RedactionPolicy())
    try:
        if output and report is ReportFormat.TERMINAL:
            raise ConfigError("--output needs --report json or --report junit")
        loaded = load_config(config)
        cases = select_cases(loaded.cases, case or [])
        wiring = wire(loaded, allow_remote=allow_remote)
        try:
            result = wiring.run_corpus.execute(cases)
        finally:
            wiring.close()
    except (ConfigError, StateConfigError, UnsafeTargetError, RunRefused) as e:
        errors.refused(str(e))
        raise typer.Exit(ExitCode.REFUSED) from e

    if report is ReportFormat.TERMINAL or output:
        TerminalReporter(Console(highlight=False), loaded.redaction).report(result)
    if report is not ReportFormat.TERMINAL:
        text = _RENDER[report](result, loaded.redaction)
        try:
            _write(text, output)
        except OSError as e:
            errors.refused(f"can't write the report: {e.strerror or type(e).__name__}")
            raise typer.Exit(ExitCode.REFUSED) from e
    raise typer.Exit(result.exit_code)


def _write(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    # Write next to the target and rename: CI never picks up half a report.
    fd, tmp = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, output)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> None:
    app()
