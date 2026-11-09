"""A case: one request replayed against both targets, and the response it gets back."""

from __future__ import annotations

from dataclasses import dataclass

from migrate_guard.domain.value_objects.json_value import JsonValue

# Methods that can change state on the target: they need `allow_mutating: true` (SPEC §7).
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

type Headers = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HttpRequest:
    method: str
    path: str
    query: tuple[tuple[str, str], ...] = ()
    headers: Headers = ()
    json: JsonValue | None = None


@dataclass(frozen=True)
class Case:
    name: str
    request: HttpRequest
    mutating: bool = False  # set it for a GET that writes; mutating methods count anyway

    @property
    def is_mutating(self) -> bool:
        return self.mutating or self.request.method.upper() in MUTATING_METHODS


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Headers
    # The parsed JSON body (numbers as Decimal), or the raw text when the body is not JSON.
    body: JsonValue
