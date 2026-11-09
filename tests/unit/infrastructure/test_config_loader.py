from __future__ import annotations

import textwrap
from decimal import Decimal
from pathlib import Path

import pytest

from migrate_guard.domain.value_objects.rules import IgnoreRule, NumericRule, UnorderedRule
from migrate_guard.infrastructure.config.loader import ConfigError, load_config

CONFIG = """
version: 1
baseline: {target: http, url: "http://127.0.0.1:5001"}
candidate: {target: http, url: "http://127.0.0.1:5002"}
corpus: cases/
rules:
  response:
    - ignore: /request_id
    - numeric: {path: /items/*/price, decimals: 2}
    - unordered: {path: /items, key: sku}
"""

CASE = """
name: apply stacked promotions
request:
  method: post
  path: /cart/price
  json:
    items: [{sku: A1, qty: 3, unit_price: 2.675}]
    at: 2026-12-01T01:00:00Z
"""


def write(tmp_path: Path, config: str = CONFIG, **cases: str) -> Path:
    (tmp_path / "cases").mkdir()
    for name, body in (cases or {"apply": CASE}).items():
        (tmp_path / "cases" / f"{name}.yaml").write_text(textwrap.dedent(body))
    path = tmp_path / "migrate-guard.yaml"
    path.write_text(textwrap.dedent(config))
    return path


def test_load_config_reads_targets_rules_and_cases(tmp_path: Path) -> None:
    loaded = load_config(write(tmp_path))

    assert loaded.baseline.url == "http://127.0.0.1:5001"
    assert loaded.rules == (
        IgnoreRule(path="/request_id"),
        NumericRule(path="/items/*/price", decimals=2),
        UnorderedRule(path="/items", key="sku"),
    )
    assert [c.name for c in loaded.cases] == ["apply stacked promotions"]
    assert loaded.allow_mutating is False


def test_load_config_reads_numbers_as_decimal_and_timestamps_as_text(tmp_path: Path) -> None:
    case = load_config(write(tmp_path)).cases[0]

    assert case.request.method == "POST"
    assert case.request.json == {
        "items": [{"sku": "A1", "qty": 3, "unit_price": Decimal("2.675")}],
        "at": "2026-12-01T01:00:00Z",
    }


def test_load_config_rejects_state_until_it_is_implemented(tmp_path: Path) -> None:
    config = CONFIG.replace(
        'url: "http://127.0.0.1:5001"}', 'url: "http://127.0.0.1:5001", state: {}}'
    )

    with pytest.raises(ConfigError, match="state"):
        load_config(write(tmp_path, config))


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="allow_mutatin"):
        load_config(write(tmp_path, CONFIG + "allow_mutatin: true\n"))


def test_load_config_rejects_an_unknown_rule(tmp_path: Path) -> None:
    config = CONFIG.replace("- ignore: /request_id", "- fuzzy: /request_id")

    with pytest.raises(ConfigError, match="unknown rule 'fuzzy'"):
        load_config(write(tmp_path, config))


def test_load_config_rejects_an_invalid_rule_with_its_position(tmp_path: Path) -> None:
    config = CONFIG.replace("decimals: 2", "decimals: -1")

    with pytest.raises(ConfigError, match=r"rules.response\[1\]"):
        load_config(write(tmp_path, config))


def test_load_config_rejects_duplicate_case_names(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="duplicate case name"):
        load_config(write(tmp_path, a=CASE, b=CASE))


def test_load_config_rejects_infinite_numbers(tmp_path: Path) -> None:
    case = CASE.replace("unit_price: 2.675", "unit_price: .inf")

    with pytest.raises(ConfigError, match="finite"):
        load_config(write(tmp_path, apply=case))


def test_load_config_reports_a_missing_corpus(tmp_path: Path) -> None:
    path = tmp_path / "migrate-guard.yaml"
    path.write_text(CONFIG)

    with pytest.raises(ConfigError, match="corpus directory not found"):
        load_config(path)


def test_load_config_extends_the_default_redaction_patterns(tmp_path: Path) -> None:
    loaded = load_config(write(tmp_path, CONFIG + "redact: {fields: [cuit]}\n"))

    assert loaded.redaction.is_sensitive_field("cuit")
    assert loaded.redaction.is_sensitive_field("password")
