from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from migrate_guard.domain.entities.case import HttpRequest
from migrate_guard.domain.ports.target_runner import TargetError
from migrate_guard.infrastructure.adapters.targets.http_target import (
    HttpTarget,
    UnsafeTargetError,
    check_target_url,
)


def target(handler: httpx.MockTransport) -> HttpTarget:
    return HttpTarget("http://127.0.0.1:5001", transport=handler)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:5001", "http://localhost:8000", "http://[::1]:80", "http://127.0.0.2"]
)
def test_check_target_url_accepts_loopback(url: str) -> None:
    check_target_url(url, allow_remote=False)


@pytest.mark.parametrize("url", ["http://10.0.0.5:5001", "https://api.example.com"])
def test_check_target_url_rejects_remote_hosts_by_default(url: str) -> None:
    with pytest.raises(UnsafeTargetError, match="--allow-remote"):
        check_target_url(url, allow_remote=False)


def test_check_target_url_allows_remote_hosts_on_purpose() -> None:
    check_target_url("https://staging.example.com", allow_remote=True)


def test_check_target_url_rejects_a_name_that_only_looks_like_loopback() -> None:
    with pytest.raises(UnsafeTargetError):
        check_target_url("http://localhost.evil.com", allow_remote=False)


def test_check_target_url_rejects_credentials_in_the_url() -> None:
    with pytest.raises(UnsafeTargetError, match="credentials"):
        check_target_url("http://admin:pw@127.0.0.1", allow_remote=False)


def test_execute_parses_json_numbers_as_decimal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"price": 2.675})

    response = target(httpx.MockTransport(handler)).execute(HttpRequest("GET", "/prices/A1"))

    assert response.body == {"price": Decimal("2.675")}


def test_execute_sends_decimal_numbers_exactly_as_written() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(204)

    request = HttpRequest("post", "/cart", json={"qty": 3, "price": Decimal("2.675")})
    target(httpx.MockTransport(handler)).execute(request)

    assert seen == [b'{"qty":3,"price":2.675}']


def test_execute_keeps_a_non_json_body_as_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    response = target(httpx.MockTransport(handler)).execute(HttpRequest("GET", "/"))

    assert (response.status, response.body) == (500, "Internal Server Error")


def test_execute_compares_nan_as_text_instead_of_parsing_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'{"price": NaN}', headers={"content-type": "application/json"}
        )

    response = target(httpx.MockTransport(handler)).execute(HttpRequest("GET", "/"))

    assert response.body == '{"price": NaN}'


def test_execute_does_not_follow_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/"})

    response = target(httpx.MockTransport(handler)).execute(HttpRequest("GET", "/"))

    assert response.status == 302


def test_execute_turns_a_timeout_into_a_target_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TargetError, match="timeout"):
        target(httpx.MockTransport(handler)).execute(HttpRequest("GET", "/"))


def test_execute_error_does_not_leak_the_query_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    request = HttpRequest("GET", "/", query=(("api_key", "s3cret"),))
    with pytest.raises(TargetError) as error:
        target(httpx.MockTransport(handler)).execute(request)

    assert "s3cret" not in str(error.value)


def test_check_reachable_accepts_any_http_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text=json.dumps({}))

    target(httpx.MockTransport(handler)).check_reachable()
