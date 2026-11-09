"""The `migrate-guard` command line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from migrate_guard.application.use_cases.run_corpus import RunCorpus, RunRefused
from migrate_guard.domain.entities.case_result import ExitCode
from migrate_guard.domain.services.redaction import RedactionPolicy
from migrate_guard.infrastructure.adapters.reporters.terminal_reporter import TerminalReporter
from migrate_guard.infrastructure.adapters.targets.http_target import (
    HttpTarget,
    UnsafeTargetError,
)
from migrate_guard.infrastructure.config.loader import ConfigError, TargetConfig, load_config

app = typer.Typer(
    help="Catch behavior changes when you rewrite a legacy service.",
    no_args_is_help=True,
    add_completion=False,
)


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
) -> None:
    """Replay the corpus against the baseline and the candidate, and compare the responses.

    Exit codes: 0 all match, 1 differences or case errors, 2 refused (config, target, safety).
    """
    console = Console(highlight=False)
    reporter = TerminalReporter(console, RedactionPolicy())
    try:
        loaded = load_config(config)
        reporter = TerminalReporter(console, loaded.redaction)
        cases = loaded.cases
        if case:
            unknown = sorted(set(case) - {c.name for c in cases})
            if unknown:
                raise ConfigError(f"unknown case: {', '.join(unknown)}")
            cases = tuple(c for c in cases if c.name in case)
        baseline = _target(loaded.baseline, allow_remote)
        candidate = _target(loaded.candidate, allow_remote)
        try:
            result = RunCorpus(baseline, candidate, loaded.rules, loaded.allow_mutating).execute(
                cases
            )
        finally:
            baseline.close()
            candidate.close()
    except (ConfigError, UnsafeTargetError, RunRefused) as e:
        reporter.refused(str(e))
        raise typer.Exit(ExitCode.REFUSED) from e

    reporter.report(result)
    raise typer.Exit(result.exit_code)


def _target(config: TargetConfig, allow_remote: bool) -> HttpTarget:
    return HttpTarget(
        config.url,
        allow_remote=allow_remote,
        timeout_seconds=config.timeout_seconds,
        health_path=config.health_path,
    )


def main() -> None:
    app()
