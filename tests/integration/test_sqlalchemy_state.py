"""The state adapters against real databases: SQLite in a temp file, Postgres in a container."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from migrate_guard.domain.ports.state import StateError
from migrate_guard.infrastructure.adapters.state.sqlalchemy_state import (
    ScriptSeeder,
    SqlAlchemyStateProbe,
    TableSpec,
    _read_only,
    create_state_engine,
)

pytestmark = pytest.mark.integration

SEED = """
DROP TABLE IF EXISTS "Prices";
CREATE TABLE "Prices" ("SKU" VARCHAR(10) PRIMARY KEY, "PriceAmt" NUMERIC(10, 3), note TEXT);
INSERT INTO "Prices" VALUES ('A1', 2.675, NULL);
INSERT INTO "Prices" VALUES ('B2', 10.00, 'x');
"""

PRICES = TableSpec(
    name="prices", table="Prices", key=("sku",), columns={"sku": "SKU", "price": "PriceAmt"}
)


def seeded(url: sa.URL, tmp_path: Path, script: str = SEED) -> sa.Engine:
    engine = create_state_engine(url)
    path = tmp_path / "seed.sql"
    path.write_text(script)
    ScriptSeeder(engine, path).seed()
    return engine


def test_probe_reads_rows_by_key_with_logical_names(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)

    snapshot = SqlAlchemyStateProbe(engine, (PRICES,)).snapshot()

    rows = snapshot.tables["prices"].rows
    assert rows["sku=A1"] == {"sku": "A1", "price": Decimal("2.675"), "note": None}
    assert rows["sku=B2"]["price"] == Decimal("10")  # 10.0 (SQLite REAL) or 10.000 (NUMERIC)


def test_probe_can_not_write_even_on_its_own_connection(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)

    with engine.connect() as conn:
        _read_only(conn)
        with pytest.raises(sa.exc.SQLAlchemyError):
            conn.execute(sa.text("""DELETE FROM "Prices" """))
        conn.rollback()

    assert len(SqlAlchemyStateProbe(engine, (PRICES,)).snapshot().tables["prices"].rows) == 2


def test_probe_quotes_table_names_instead_of_running_them(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)
    evil = TableSpec(name="x", table='Prices"; DROP TABLE "Prices"; --', key=("sku",))

    with pytest.raises(StateError):
        SqlAlchemyStateProbe(engine, (evil,)).snapshot()

    assert len(SqlAlchemyStateProbe(engine, (PRICES,)).snapshot().tables["prices"].rows) == 2


def test_probe_refuses_a_table_larger_than_max_rows(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)

    with pytest.raises(StateError, match="state too large: prices"):
        SqlAlchemyStateProbe(engine, (PRICES,), max_rows=1).snapshot()


def test_probe_refuses_a_key_that_is_not_unique(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(
        db_url,
        tmp_path,
        """
        DROP TABLE IF EXISTS log;
        CREATE TABLE log (sku VARCHAR(10), qty INTEGER);
        INSERT INTO log VALUES ('A1', 1);
        INSERT INTO log VALUES ('A1', 2);
        """,
    )

    with pytest.raises(StateError, match="duplicate key sku=A1"):
        SqlAlchemyStateProbe(engine, (TableSpec("log", "log", ("sku",)),)).snapshot()


def test_probe_reports_a_missing_key_column(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)
    no_mapping = TableSpec(name="prices", table="Prices", key=("sku",))

    with pytest.raises(StateError, match="key column not found: sku"):
        SqlAlchemyStateProbe(engine, (no_mapping,)).snapshot()


def test_seed_that_fails_halfway_leaves_nothing_applied(db_url: sa.URL, tmp_path: Path) -> None:
    engine = seeded(db_url, tmp_path)
    broken = tmp_path / "broken.sql"
    broken.write_text("""DELETE FROM "Prices";\nINSERT INTO "Prices" VALUES (syntax error;""")

    with pytest.raises(StateError):
        ScriptSeeder(engine, broken).seed()

    assert len(SqlAlchemyStateProbe(engine, (PRICES,)).snapshot().tables["prices"].rows) == 2


def test_errors_never_include_the_database_password(postgres_url: sa.URL) -> None:
    wrong = postgres_url.set(password="not-the-real-s3cret")
    probe = SqlAlchemyStateProbe(create_state_engine(wrong), (PRICES,))

    with pytest.raises(StateError) as error:
        probe.snapshot()

    assert "not-the-real-s3cret" not in str(error.value)
