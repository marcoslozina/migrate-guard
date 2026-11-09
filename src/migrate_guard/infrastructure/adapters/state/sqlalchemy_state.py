"""State adapters on SQLAlchemy: a read-only probe and a script seeder (SQLite and Postgres)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from migrate_guard.domain.entities.state import Row, StateSnapshot, TableSnapshot
from migrate_guard.domain.ports.state import Seeder, StateError, StateProbe
from migrate_guard.domain.services.state_values import (
    UnsupportedValueError,
    format_row_key,
    normalize_db_value,
)

SUPPORTED_BACKENDS = frozenset({"sqlite", "postgresql"})
DEFAULT_MAX_ROWS = 10_000
_MAX_ERROR_CHARS = 300


@dataclass(frozen=True)
class TableSpec:
    """One watched table on one side. Names in `key` and in rules are logical column names."""

    name: str  # logical name, shared by both sides
    table: str  # physical table name on this side
    key: tuple[str, ...]
    columns: dict[str, str] = field(default_factory=dict)  # logical -> physical
    schema: str | None = None


def create_state_engine(url: sa.URL) -> Engine:
    if url.get_backend_name() not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported database {url.get_backend_name()!r} (sqlite, postgresql)")
    # NullPool: no connection outlives a probe or a seed, so no session setting leaks between
    # them, and every probe reads what the target just committed.
    return sa.create_engine(url, poolclass=NullPool)


class SqlAlchemyStateProbe(StateProbe):
    def __init__(
        self, engine: Engine, tables: tuple[TableSpec, ...], max_rows: int = DEFAULT_MAX_ROWS
    ) -> None:
        self._engine = engine
        self._tables = tables
        self._max_rows = max_rows

    def snapshot(self) -> StateSnapshot:
        with _translated_errors(self._engine), self._engine.connect() as conn:
            try:
                _read_only(conn)
                return StateSnapshot({spec.name: self._read(conn, spec) for spec in self._tables})
            finally:
                conn.rollback()  # it never writes; this closes the read-only transaction

    def _read(self, conn: Connection, spec: TableSpec) -> TableSnapshot:
        # Built with SQLAlchemy constructs: names are quoted, never pasted into SQL text.
        query = (
            sa.select(sa.text("*"))
            .select_from(sa.table(spec.table, schema=spec.schema))
            .limit(self._max_rows + 1)
        )
        records = conn.execute(query).mappings().all()
        if len(records) > self._max_rows:
            raise StateError(f"state too large: {spec.name} (more than {self._max_rows} rows)")

        to_logical = {physical: logical for logical, physical in spec.columns.items()}
        rows: dict[str, Row] = {}
        for record in records:
            row = {to_logical.get(col, col): _normalize(spec, col, v) for col, v in record.items()}
            missing = [k for k in spec.key if k not in row]
            if missing:
                raise StateError(f"{spec.name}: key column not found: {', '.join(missing)}")
            try:
                key = format_row_key(spec.key, tuple(row[k] for k in spec.key))
            except ValueError as e:
                raise StateError(f"{spec.name}: {e}") from e
            if key in rows:
                # Pairing rows by a key that isn't unique would hide differences.
                raise StateError(f"{spec.name}: duplicate key {key}")
            rows[key] = row
        return TableSnapshot(rows)


class ScriptSeeder(Seeder):
    """Runs a SQL script inside one transaction: either the whole script applies, or nothing."""

    def __init__(self, engine: Engine, script: Path) -> None:
        self._engine = engine
        self._backend = engine.url.get_backend_name()
        try:
            self._script = script.read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(f"can't read seed script {script}: {e.strerror}") from e

    def seed(self) -> None:
        with _translated_errors(self._engine):
            raw = self._engine.raw_connection()
            try:
                self._run(raw)
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()

    def _run(self, raw: Any) -> None:
        if self._backend == "sqlite":
            # executescript() commits first and then runs in autocommit, so the transaction is
            # explicit. If a statement fails, the BEGIN is still open and the rollback undoes it.
            raw.driver_connection.executescript(f"BEGIN;\n{self._script}\nCOMMIT;")
        else:
            cursor = raw.cursor()
            cursor.execute(self._script)  # psycopg runs several statements when there are no params
            raw.commit()


def _read_only(conn: Connection) -> None:
    backend = conn.engine.url.get_backend_name()
    if backend == "sqlite":
        conn.exec_driver_sql("PRAGMA query_only = ON")
    elif backend == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")


def _normalize(spec: TableSpec, column: str, value: object) -> Any:
    try:
        return normalize_db_value(value)
    except UnsupportedValueError as e:
        raise StateError(f"{spec.name}.{column}: {e}") from e


def _driver_errors() -> tuple[type[BaseException], ...]:
    # DB-API errors raised by raw connections (the seeder) are not wrapped by SQLAlchemy.
    errors: list[type[BaseException]] = [sa.exc.SQLAlchemyError, sqlite3.Error]
    try:
        import psycopg
    except ImportError:  # the [postgres] extra is not installed
        pass
    else:
        errors.append(psycopg.Error)
    return tuple(errors)


_DRIVER_ERRORS = _driver_errors()


@contextmanager
def _translated_errors(engine: Engine) -> Iterator[None]:
    """Turn driver errors into StateError, with the DSN password removed from the message."""
    try:
        yield
    except _DRIVER_ERRORS as e:
        original = getattr(e, "orig", None) or e
        lines = str(original).strip().splitlines()
        message = f"{type(original).__name__}: {lines[0] if lines else ''}"
        if engine.url.password:
            message = message.replace(engine.url.password, "***")
        raise StateError(message[:_MAX_ERROR_CHARS]) from None
