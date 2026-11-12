"""pytest plugin (SPEC R6): `pytest --migrate-guard=migrate-guard.yaml`.

Each case becomes one test, `migrate_guard[<case name>]`. A difference fails it with the same
redacted lines the terminal shows; a case that could not be compared fails it too, never passes.
Without the option the plugin does nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from migrate_guard.application.use_cases.run_corpus import RunRefused
from migrate_guard.domain.entities.case import Case
from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus
from migrate_guard.domain.value_objects.rules import Rule
from migrate_guard.domain.value_objects.state_rules import StateRule
from migrate_guard.infrastructure.adapters.reporters.plain_text import case_lines, describe_rule
from migrate_guard.infrastructure.adapters.targets.http_target import UnsafeTargetError
from migrate_guard.infrastructure.config.loader import ConfigError, LoadedConfig, load_config
from migrate_guard.infrastructure.config.state_config import StateConfigError
from migrate_guard.infrastructure.wiring import Wiring, wire

if TYPE_CHECKING:
    from _pytest._code.code import TerminalRepr

OPTION = "--migrate-guard"
_PLUGIN_NAME = "migrate-guard-run"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("migrate-guard")
    group.addoption(
        OPTION,
        dest="migrate_guard",
        metavar="CONFIG",
        default=None,
        help="Run every case of this migrate-guard.yaml as a test.",
    )
    group.addoption(
        "--migrate-guard-allow-remote",
        dest="migrate_guard_allow_remote",
        action="store_true",
        help="Allow targets that are not loopback.",
    )


def pytest_configure(config: pytest.Config) -> None:
    path = config.getoption("migrate_guard")
    if not path:
        return
    try:
        loaded = load_config(Path(path))
    except (ConfigError, StateConfigError) as e:
        raise pytest.UsageError(f"migrate-guard: {e}") from e
    allow_remote = bool(config.getoption("migrate_guard_allow_remote"))
    config.pluginmanager.register(_CorpusRun(Path(path), loaded, allow_remote), _PLUGIN_NAME)


class CaseFailed(Exception):
    def __init__(self, lines: list[str]) -> None:
        super().__init__("\n".join(lines))
        self.lines = lines


class _CorpusRun:
    """One run of the corpus: the targets are opened on the first case and closed at the end."""

    def __init__(self, path: Path, loaded: LoadedConfig, allow_remote: bool) -> None:
        self.path = path
        self.loaded = loaded
        self.allow_remote = allow_remote
        self._wiring: Wiring | None = None
        # Rules unused in every compared case are stale, as in a CLI run (SPEC R2).
        self._unused: set[Rule | StateRule] | None = None

    @pytest.hookimpl(tryfirst=True)  # before -k/-m deselection, so they apply to cases too
    def pytest_collection_modifyitems(self, session: pytest.Session, items: list[Any]) -> None:
        items.extend(
            CaseItem.from_parent(
                session,
                name=f"migrate_guard[{case.name}]",
                nodeid=f"{self.path.name}::migrate_guard[{case.name}]",
                case=case,
                run=self,
            )
            for case in self.loaded.cases
        )

    def run(self, case: Case) -> CaseResult:
        try:
            if self._wiring is None:
                self._wiring = wire(self.loaded, allow_remote=self.allow_remote)
            result = self._wiring.run_corpus.execute([case])
        except (RunRefused, UnsafeTargetError, StateConfigError) as e:
            raise CaseFailed([f"refused: {e}"]) from e
        outcome = result.cases[0]
        if outcome.status is not CaseStatus.ERROR:
            unused = set(result.unused_rules)
            self._unused = unused if self._unused is None else self._unused & unused
        return outcome

    def pytest_sessionfinish(self) -> None:
        if self._wiring is not None:
            self._wiring.close()
            self._wiring = None

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        for rule in sorted(self._unused or (), key=describe_rule):
            terminalreporter.write_line(
                f"migrate-guard: rule never applied: {describe_rule(rule)}", yellow=True
            )


class CaseItem(pytest.Item):
    def __init__(self, *, case: Case, run: _CorpusRun, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.case = case
        self.corpus_run = run

    def runtest(self) -> None:
        result = self.corpus_run.run(self.case)
        if result.status is not CaseStatus.MATCH:
            raise CaseFailed(case_lines(result, self.corpus_run.loaded.redaction))

    def repr_failure(
        self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None
    ) -> str | TerminalRepr:
        if isinstance(excinfo.value, CaseFailed):
            return "\n".join(excinfo.value.lines)
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Path, int | None, str]:
        return self.corpus_run.path, None, self.name
