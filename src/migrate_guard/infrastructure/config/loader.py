"""Load `migrate-guard.yaml` and the corpus of case files, and validate both."""

from __future__ import annotations

import decimal
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from migrate_guard.domain.entities.case import Case, HttpRequest
from migrate_guard.domain.services.redaction import DEFAULT_FIELD_PATTERNS, RedactionPolicy
from migrate_guard.domain.value_objects.json_value import JsonValue
from migrate_guard.domain.value_objects.rules import (
    DatetimeRule,
    IgnoreRule,
    NumericRule,
    Rule,
    UnorderedRule,
)
from migrate_guard.infrastructure.config.state_config import (
    SeedConfig,
    StateConfigError,
    StateSettings,
    StateSideConfig,
    resolve_state,
)


class ConfigError(Exception):
    """The config or the corpus is invalid. Maps to exit code 2."""


# --- YAML: numbers as Decimal, timestamps as plain strings ------------------------------


class _Loader(yaml.SafeLoader):
    pass


def _decimal(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> Decimal:
    text = str(loader.construct_scalar(node))
    problem = f"not a finite number: {text!r} (line {node.start_mark.line + 1})"
    try:
        value = Decimal(text)
    except decimal.InvalidOperation:
        raise ConfigError(problem) from None
    if not value.is_finite():
        raise ConfigError(problem)
    return value


def _text(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    # A timestamp stays the exact string that was written: its offset is part of the case.
    return str(loader.construct_scalar(node))


_Loader.add_constructor("tag:yaml.org,2002:float", _decimal)
_Loader.add_constructor("tag:yaml.org,2002:timestamp", _text)


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.load(f, Loader=_Loader)  # noqa: S506 - _Loader is a SafeLoader
    except OSError as e:
        raise ConfigError(f"can't read {path}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e


# --- schema ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetConfig(_Strict):
    target: Literal["http"]  # asgi and wsgi come later
    url: str
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    health_path: str = "/"
    state: StateSideConfig | None = None


class _RulesConfig(_Strict):
    response: list[dict[str, Any]] = []
    state: list[dict[str, Any]] = []


class _RedactConfig(_Strict):
    fields: list[str] = []  # added to the defaults, never replacing them


class _ConfigFile(_Strict):
    version: Literal[1]
    baseline: TargetConfig
    candidate: TargetConfig
    corpus: str
    rules: _RulesConfig = _RulesConfig()
    redact: _RedactConfig = _RedactConfig()
    seed: SeedConfig | None = None
    allow_mutating: bool = False


class _RequestFile(_Strict):
    method: str
    path: str = Field(pattern=r"^/")
    query: dict[str, str] = {}
    headers: dict[str, str] = {}
    json_body: Any = Field(default=None, alias="json")

    @field_validator("method")
    @classmethod
    def _known_method(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"unknown HTTP method {value!r}")
        return upper


class _CaseFile(_Strict):
    name: str = Field(min_length=1)
    request: _RequestFile
    mutating: bool = False


# --- loading --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedConfig:
    baseline: TargetConfig
    candidate: TargetConfig
    rules: tuple[Rule, ...]
    redaction: RedactionPolicy
    allow_mutating: bool
    cases: tuple[Case, ...]
    state: StateSettings | None = None  # None: responses only


def load_config(path: Path, env: Mapping[str, str] | None = None) -> LoadedConfig:
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping")
    try:
        config = _ConfigFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from e

    corpus_dir = (path.parent / config.corpus).resolve()
    try:
        state = resolve_state(
            config.baseline.state,
            config.candidate.state,
            config.seed,
            config.rules.state,
            path.parent,
            os.environ if env is None else env,
        )
    except StateConfigError as e:
        raise ConfigError(str(e)) from e
    return LoadedConfig(
        baseline=config.baseline,
        candidate=config.candidate,
        rules=tuple(_parse_rule(item, i) for i, item in enumerate(config.rules.response)),
        redaction=RedactionPolicy((*DEFAULT_FIELD_PATTERNS, *config.redact.fields)),
        allow_mutating=config.allow_mutating,
        cases=load_corpus(corpus_dir),
        state=state,
    )


def load_corpus(directory: Path) -> tuple[Case, ...]:
    if not directory.is_dir():
        raise ConfigError(f"corpus directory not found: {directory}")
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    cases: list[Case] = []
    seen: dict[str, Path] = {}
    for file in files:
        try:
            parsed = _CaseFile.model_validate(_read_yaml(file))
        except ValidationError as e:
            raise ConfigError(_explain(file, e)) from e
        if parsed.name in seen:
            raise ConfigError(
                f"duplicate case name {parsed.name!r} in {seen[parsed.name]} and {file}"
            )
        seen[parsed.name] = file
        cases.append(_to_case(parsed, file))
    return tuple(cases)


def _parse_rule(item: dict[str, Any], index: int) -> Rule:
    where = f"rules.response[{index}]"
    if len(item) != 1:
        raise ConfigError(f"{where}: each rule has exactly one kind, got {sorted(item)}")
    [(kind, spec)] = item.items()
    try:
        match kind:
            case "ignore":
                return IgnoreRule(path=spec)
            case "numeric":
                return NumericRule.model_validate(spec)
            case "unordered":
                return UnorderedRule.model_validate(spec)
            case "datetime":
                return DatetimeRule.model_validate(spec)
    except ValidationError as e:
        raise ConfigError(f"{where} ({kind}): {_first_error(e)}") from e
    raise ConfigError(f"{where}: unknown rule {kind!r} (ignore, numeric, unordered, datetime)")


def _to_case(parsed: _CaseFile, file: Path) -> Case:
    req = parsed.request
    return Case(
        name=parsed.name,
        request=HttpRequest(
            method=req.method,
            path=req.path,
            query=tuple(req.query.items()),
            headers=tuple(req.headers.items()),
            json=_json_value(req.json_body, file),
        ),
        mutating=parsed.mutating,
    )


def _json_value(value: Any, file: Path) -> JsonValue:
    if value is None or isinstance(value, bool | int | Decimal | str):
        return value
    if isinstance(value, list):
        return [_json_value(v, file) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ConfigError(f"{file}: JSON object keys must be strings")
        return {k: _json_value(v, file) for k, v in value.items()}
    raise ConfigError(f"{file}: unsupported value in the request body: {type(value).__name__}")


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {err['msg']}" if loc else str(err["msg"])


def _explain(path: Path, e: ValidationError) -> str:
    return f"{path}: {_first_error(e)}"
