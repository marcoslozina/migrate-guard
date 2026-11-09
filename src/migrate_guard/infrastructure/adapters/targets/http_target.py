"""Target adapter: a service already running, reached over HTTP with httpx."""

from __future__ import annotations

import ipaddress
import json
from decimal import Decimal
from typing import Any, NoReturn
from urllib.parse import urlsplit

import httpx

from migrate_guard.domain.entities.case import HttpRequest, HttpResponse
from migrate_guard.domain.ports.target_runner import TargetError, TargetRunner
from migrate_guard.domain.value_objects.json_value import JsonValue, to_json_text

DEFAULT_TIMEOUT_SECONDS = 10.0


class UnsafeTargetError(ValueError):
    """The URL is not allowed: not loopback without --allow-remote, or it carries credentials."""


def check_target_url(url: str, *, allow_remote: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeTargetError(f"target URL must be http or https: {url!r}")
    if parts.username or parts.password:
        # Secrets live in environment variables, never in the config file (SPEC §7).
        raise UnsafeTargetError("target URL must not contain credentials")
    host = parts.hostname or ""
    if not allow_remote and not _is_loopback(host):
        raise UnsafeTargetError(
            f"target {host!r} is not loopback; pass --allow-remote to use it on purpose"
        )


def _is_loopback(host: str) -> bool:
    # No DNS lookup: a name that resolves to 127.0.0.1 today can resolve elsewhere tomorrow.
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpTarget(TargetRunner):
    def __init__(
        self,
        base_url: str,
        *,
        allow_remote: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        health_path: str = "/",
        transport: httpx.BaseTransport | None = None,  # tests inject httpx.MockTransport
    ) -> None:
        check_target_url(base_url, allow_remote=allow_remote)
        self._health_path = health_path
        # follow_redirects=False: a redirect must not take a loopback-only run somewhere else.
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    def check_reachable(self) -> None:
        # Any HTTP answer, even a 404, proves the service is up.
        self._send("GET", self._health_path)

    def execute(self, request: HttpRequest) -> HttpResponse:
        kwargs: dict[str, Any] = {"params": list(request.query), "headers": list(request.headers)}
        if request.json is not None:
            # Encode it here: the stdlib encoder writes Decimal values exactly as they were read.
            kwargs["content"] = to_json_text(request.json).encode()
            kwargs["headers"] = [*request.headers, ("content-type", "application/json")]
        response = self._send(request.method.upper(), request.path, **kwargs)
        return HttpResponse(
            status=response.status_code,
            headers=tuple(response.headers.multi_items()),
            body=_parse_body(response),
        )

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            _fail("timeout")
        except httpx.HTTPError as e:
            _fail(type(e).__name__)  # not str(e): it can include the full URL and query


def _fail(reason: str) -> NoReturn:
    raise TargetError(reason)


def _parse_body(response: httpx.Response) -> JsonValue:
    text = response.text
    if "json" not in response.headers.get("content-type", ""):
        return text
    try:
        parsed: JsonValue = json.loads(text, parse_float=Decimal, parse_constant=_reject)
    except ValueError:
        # Broken JSON is still behavior: compare it as text instead of failing the case.
        return text
    return parsed


def _reject(constant: str) -> NoReturn:
    raise ValueError(f"{constant} is not valid JSON")
