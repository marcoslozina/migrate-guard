"""The database part of the config: DSNs, watched tables, seed scripts and state rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from migrate_guard.application.use_cases.run_corpus import StateCheck
from migrate_guard.domain.value_objects.state_rules import (
    IgnoreColumnRule,
    StateNumericRule,
    StateRule,
)
from migrate_guard.infrastructure.adapters.state.sqlalchemy_state import (
    DEFAULT_MAX_ROWS,
    SUPPORTED_BACKENDS,
    ScriptSeeder,
    SqlAlchemyStateProbe,
    TableSpec,
    create_state_engine,
)


class StateConfigError(Exception):
    """Raised for an invalid state config; the loader turns it into a ConfigError."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TableConfig(_Strict):
    table: str | None = None  # physical name on this side; defaults to the logical name
    schema_name: str | None = Field(default=None, alias="schema")
    key: list[str] = Field(min_length=1)  # required: rows are paired by key, never by order
    columns: dict[str, str] = {}  # logical -> physical


class StateSideConfig(_Strict):
    dsn_env: str | None = None  # the DSN is read from this environment variable
    dsn: str | None = None  # inline DSN: only without a password (SQLite paths, local sockets)
    tables: dict[str, TableConfig] = Field(min_length=1)
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, ge=1, le=1_000_000)


class SeedConfig(_Strict):
    strategy: Literal["script", "transaction"]
    baseline: str
    candidate: str


@dataclass(frozen=True)
class _Side:
    url: sa.URL
    tables: tuple[TableSpec, ...]
    max_rows: int
    seed_script: Path


@dataclass(frozen=True)
class StateSettings:
    baseline: _Side
    candidate: _Side
    rules: tuple[StateRule, ...]

    def build(self) -> StateCheck:
        b_engine = create_state_engine(self.baseline.url)
        c_engine = create_state_engine(self.candidate.url)
        try:
            return StateCheck(
                baseline_seeder=ScriptSeeder(b_engine, self.baseline.seed_script),
                candidate_seeder=ScriptSeeder(c_engine, self.candidate.seed_script),
                baseline_probe=SqlAlchemyStateProbe(
                    b_engine, self.baseline.tables, self.baseline.max_rows
                ),
                candidate_probe=SqlAlchemyStateProbe(
                    c_engine, self.candidate.tables, self.candidate.max_rows
                ),
                rules=self.rules,
            )
        except ValueError as e:
            raise StateConfigError(str(e)) from e


def resolve_state(
    baseline: StateSideConfig | None,
    candidate: StateSideConfig | None,
    seed: SeedConfig | None,
    rules: list[dict[str, Any]],
    base_dir: Path,
    env: Mapping[str, str],
) -> StateSettings | None:
    if baseline is None and candidate is None:
        if seed is not None or rules:
            raise StateConfigError("`seed` and `rules.state` need `state` on both targets")
        return None
    if baseline is None or candidate is None:
        raise StateConfigError("`state` must be configured on both baseline and candidate")
    if seed is None:
        # SPEC §6: every case starts from a known state on both targets.
        raise StateConfigError("comparing state needs a `seed` section to reset both databases")
    if seed.strategy == "transaction":
        raise StateConfigError("seed strategy `transaction` is not implemented yet: use `script`")
    if set(baseline.tables) != set(candidate.tables):
        only = sorted(set(baseline.tables) ^ set(candidate.tables))
        raise StateConfigError(f"watched tables differ between targets: {', '.join(only)}")

    b_url = _resolve_url("baseline", baseline, base_dir, env)
    c_url = _resolve_url("candidate", candidate, base_dir, env)
    if _identity(b_url) == _identity(c_url):
        raise StateConfigError("baseline and candidate point to the same database")

    return StateSettings(
        baseline=_side(baseline, b_url, base_dir / seed.baseline),
        candidate=_side(candidate, c_url, base_dir / seed.candidate),
        rules=tuple(_parse_rule(item, i, set(baseline.tables)) for i, item in enumerate(rules)),
    )


def _side(config: StateSideConfig, url: sa.URL, seed_script: Path) -> _Side:
    if not seed_script.is_file():
        raise StateConfigError(f"seed script not found: {seed_script}")
    tables = tuple(
        TableSpec(
            name=name,
            table=t.table or name,
            key=tuple(t.key),
            columns=dict(t.columns),
            schema=t.schema_name,
        )
        for name, t in config.tables.items()
    )
    return _Side(url, tables, config.max_rows, seed_script.resolve())


def _resolve_url(
    side: str, config: StateSideConfig, base_dir: Path, env: Mapping[str, str]
) -> sa.URL:
    where = f"{side}.state"
    if (config.dsn is None) == (config.dsn_env is None):
        raise StateConfigError(f"{where}: set exactly one of `dsn_env` or `dsn`")
    if config.dsn_env is not None:
        text = env.get(config.dsn_env, "")
        if not text:
            raise StateConfigError(f"{where}: environment variable {config.dsn_env} is not set")
    else:
        text = config.dsn or ""
    try:
        url = sa.make_url(text)
    except sa.exc.ArgumentError:
        raise StateConfigError(f"{where}: invalid DSN") from None  # never echo it: secrets
    if config.dsn is not None and url.password:
        raise StateConfigError(f"{where}: inline `dsn` contains a password; use `dsn_env`")
    if url.get_backend_name() not in SUPPORTED_BACKENDS:
        raise StateConfigError(f"{where}: unsupported database {url.get_backend_name()!r}")
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise StateConfigError(f"{where}: SQLite needs a file, not an in-memory database")
        url = url.set(database=str((base_dir / url.database).resolve()))
    return url


def _identity(url: sa.URL) -> tuple[str, str, int | None, str]:
    # Same server and database name means the same data, whatever the user or the driver.
    return (url.get_backend_name(), url.host or "localhost", url.port, url.database or "")


def _parse_rule(item: dict[str, Any], index: int, tables: set[str]) -> StateRule:
    where = f"rules.state[{index}]"
    if len(item) != 1:
        raise StateConfigError(f"{where}: each rule has exactly one kind, got {sorted(item)}")
    [(kind, spec)] = item.items()
    model: type[IgnoreColumnRule] | type[StateNumericRule]
    match kind:
        case "ignore_column":
            model = IgnoreColumnRule
        case "numeric":
            model = StateNumericRule
        case _:
            raise StateConfigError(f"{where}: unknown state rule {kind!r} (ignore_column, numeric)")
    try:
        rule = model.model_validate(spec)
    except ValidationError as e:
        err = e.errors()[0]
        raise StateConfigError(
            f"{where} ({kind}): {'.'.join(map(str, err['loc']))}: {err['msg']}"
        ) from e
    if rule.table not in tables:
        raise StateConfigError(f"{where}: table {rule.table!r} is not watched")
    return rule
