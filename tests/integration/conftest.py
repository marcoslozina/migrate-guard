from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa


@pytest.fixture
def sqlite_url(tmp_path: Path) -> sa.URL:
    return sa.make_url(f"sqlite:///{tmp_path / 'state.db'}")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[sa.URL]:
    testcontainers = pytest.importorskip("testcontainers.postgres")
    try:
        container = testcontainers.PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as e:  # no Docker here: skip, don't fail the suite
        pytest.skip(f"Postgres container unavailable: {type(e).__name__}")
    try:
        yield sa.make_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request: pytest.FixtureRequest) -> sa.URL:
    name = "sqlite_url" if request.param == "sqlite" else "postgres_url"
    url: sa.URL = request.getfixturevalue(name)
    return url
