"""Port: something that can execute a case (a running service, an in-process app...)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from migrate_guard.domain.entities.case import HttpRequest, HttpResponse


class TargetError(Exception):
    """The target could not answer: connection refused, timeout, invalid response..."""


class TargetRunner(ABC):
    @abstractmethod
    def check_reachable(self) -> None:
        """Raise TargetError if the target can't be reached at all, before any case runs."""

    @abstractmethod
    def execute(self, request: HttpRequest) -> HttpResponse:
        """Send the request and return the response; raise TargetError if there is none."""
