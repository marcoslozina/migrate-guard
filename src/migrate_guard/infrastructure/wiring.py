"""Builds a ready-to-run `RunCorpus` from a loaded config: shared by the CLI and pytest plugin."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from migrate_guard.application.use_cases.run_corpus import RunCorpus
from migrate_guard.domain.entities.case import Case
from migrate_guard.infrastructure.adapters.targets.http_target import HttpTarget
from migrate_guard.infrastructure.config.loader import ConfigError, LoadedConfig, TargetConfig


@dataclass(frozen=True)
class Wiring:
    run_corpus: RunCorpus
    baseline: HttpTarget
    candidate: HttpTarget

    def close(self) -> None:
        self.baseline.close()
        self.candidate.close()


def wire(loaded: LoadedConfig, *, allow_remote: bool) -> Wiring:
    """Raises `UnsafeTargetError` for a remote target without `allow_remote`."""
    state = loaded.state.build() if loaded.state else None
    baseline = _target(loaded.baseline, allow_remote)
    try:
        candidate = _target(loaded.candidate, allow_remote)
    except Exception:
        baseline.close()
        raise
    run_corpus = RunCorpus(baseline, candidate, loaded.rules, loaded.allow_mutating, state)
    return Wiring(run_corpus, baseline, candidate)


def select_cases(cases: Sequence[Case], names: Sequence[str]) -> tuple[Case, ...]:
    """Only the named cases, in corpus order; all of them when no name is given."""
    if not names:
        return tuple(cases)
    unknown = sorted(set(names) - {c.name for c in cases})
    if unknown:
        raise ConfigError(f"unknown case: {', '.join(unknown)}")
    return tuple(c for c in cases if c.name in names)


def _target(config: TargetConfig, allow_remote: bool) -> HttpTarget:
    return HttpTarget(
        config.url,
        allow_remote=allow_remote,
        timeout_seconds=config.timeout_seconds,
        health_path=config.health_path,
    )
