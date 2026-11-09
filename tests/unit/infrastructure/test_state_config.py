from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from migrate_guard.domain.value_objects.state_rules import IgnoreColumnRule, StateNumericRule
from migrate_guard.infrastructure.config.loader import ConfigError, LoadedConfig, load_config

CONFIG = """
version: 1
baseline:
  target: http
  url: "http://127.0.0.1:5001"
  state:
    dsn: sqlite:///legacy.db
    tables:
      prices: {table: Prices, key: [sku], columns: {sku: SKU, price: PriceAmt}}
candidate:
  target: http
  url: "http://127.0.0.1:5002"
  state:
    dsn_env: NEW_DSN
    tables:
      prices: {key: [sku]}
corpus: cases/
seed:
  strategy: script
  baseline: seeds/legacy.sql
  candidate: seeds/new.sql
rules:
  state:
    - ignore_column: {table: prices, column: updated_at}
    - numeric: {table: prices, column: price, decimals: 2}
"""

ENV = {"NEW_DSN": "sqlite:///new.db"}


def load(tmp_path: Path, config: str = CONFIG, env: dict[str, str] | None = None) -> LoadedConfig:
    (tmp_path / "cases").mkdir(exist_ok=True)
    (tmp_path / "cases" / "a.yaml").write_text("name: a\nrequest: {method: GET, path: /}\n")
    (tmp_path / "seeds").mkdir(exist_ok=True)
    (tmp_path / "seeds" / "legacy.sql").write_text("SELECT 1;")
    (tmp_path / "seeds" / "new.sql").write_text("SELECT 1;")
    path = tmp_path / "migrate-guard.yaml"
    path.write_text(textwrap.dedent(config))
    return load_config(path, ENV if env is None else env)


def test_load_state_maps_each_side_to_its_own_physical_names(tmp_path: Path) -> None:
    state = load(tmp_path).state

    assert state is not None
    [legacy] = state.baseline.tables
    assert (legacy.name, legacy.table, legacy.columns["price"]) == ("prices", "Prices", "PriceAmt")
    [new] = state.candidate.tables
    assert (new.name, new.table) == ("prices", "prices")
    assert state.rules == (
        IgnoreColumnRule(table="prices", column="updated_at"),
        StateNumericRule(table="prices", column="price", decimals=2),
    )


def test_load_state_resolves_sqlite_paths_next_to_the_config(tmp_path: Path) -> None:
    state = load(tmp_path).state

    assert state is not None
    assert state.baseline.url.database == str(tmp_path / "legacy.db")


def test_load_state_rejects_the_same_database_on_both_sides(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="same database"):
        load(tmp_path, env={"NEW_DSN": f"sqlite:///{tmp_path / 'legacy.db'}"})


def test_load_state_rejects_the_same_postgres_database_with_another_user(tmp_path: Path) -> None:
    config = CONFIG.replace("dsn: sqlite:///legacy.db", "dsn_env: OLD_DSN")
    env = {
        "OLD_DSN": "postgresql://a:x@db:5432/shop",
        "NEW_DSN": "postgresql+psycopg://b:y@db:5432/shop",
    }

    with pytest.raises(ConfigError, match="same database"):
        load(tmp_path, config, env)


def test_load_state_rejects_an_inline_dsn_with_a_password(tmp_path: Path) -> None:
    config = CONFIG.replace("dsn: sqlite:///legacy.db", "dsn: postgresql://u:hunter2@db/shop")

    with pytest.raises(ConfigError, match="dsn_env") as error:
        load(tmp_path, config)

    assert "hunter2" not in str(error.value)


def test_load_state_when_the_env_var_is_missing_then_refuses(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="NEW_DSN is not set"):
        load(tmp_path, env={})


def test_load_state_never_echoes_an_invalid_dsn(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid DSN") as error:
        load(tmp_path, env={"NEW_DSN": "not a url with s3cret"})

    assert "s3cret" not in str(error.value)


def test_load_state_rejects_a_table_without_key(tmp_path: Path) -> None:
    config = CONFIG.replace("prices: {key: [sku]}", "prices: {table: prices}")

    with pytest.raises(ConfigError, match="key"):
        load(tmp_path, config)


def test_load_state_requires_a_seed(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "seed:\n  strategy: script\n  baseline: seeds/legacy.sql\n  candidate: seeds/new.sql\n", ""
    )

    with pytest.raises(ConfigError, match="seed"):
        load(tmp_path, config)


def test_load_state_requires_state_on_both_sides(tmp_path: Path) -> None:
    config = CONFIG.replace(
        "  state:\n    dsn_env: NEW_DSN\n    tables:\n      prices: {key: [sku]}\n", ""
    )

    with pytest.raises(ConfigError, match="both"):
        load(tmp_path, config)


def test_load_state_rejects_a_rule_on_a_table_that_is_not_watched(tmp_path: Path) -> None:
    config = CONFIG.replace("{table: prices, column: updated_at}", "{table: orders, column: x}")

    with pytest.raises(ConfigError, match="'orders' is not watched"):
        load(tmp_path, config)


def test_load_state_rejects_the_transaction_strategy_for_now(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="transaction"):
        load(tmp_path, CONFIG.replace("strategy: script", "strategy: transaction"))


def test_load_state_rejects_a_missing_seed_script(tmp_path: Path) -> None:
    config = CONFIG.replace("seeds/new.sql", "seeds/nope.sql")

    with pytest.raises(ConfigError, match="seed script not found"):
        load(tmp_path, config)


def test_load_without_state_compares_responses_only(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "a.yaml").write_text("name: a\nrequest: {method: GET, path: /}\n")
    path = tmp_path / "migrate-guard.yaml"
    path.write_text(
        "version: 1\n"
        "baseline: {target: http, url: 'http://127.0.0.1:1'}\n"
        "candidate: {target: http, url: 'http://127.0.0.1:2'}\n"
        "corpus: cases/\n"
    )

    assert load_config(path, {}).state is None
