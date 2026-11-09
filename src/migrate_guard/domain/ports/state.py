"""Ports for the database side: read a target's state, and reset it before each case."""

from __future__ import annotations

from abc import ABC, abstractmethod

from migrate_guard.domain.entities.state import StateSnapshot


class StateError(Exception):
    """Seeding or probing failed. The case becomes an error, never a match."""


class StateProbe(ABC):
    @abstractmethod
    def snapshot(self) -> StateSnapshot:
        """Read the watched tables. Read-only: it must never write."""


class Seeder(ABC):
    @abstractmethod
    def seed(self) -> None:
        """Reset the database to the known starting state, all or nothing."""
