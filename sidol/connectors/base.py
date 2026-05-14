"""Connector contract for Sidol sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from sidol.context import ConnectorContext
from sidol.types import Capabilities, Schema, WriteResult


class BaseConnector(ABC):
    """Base class for all sidol connectors.

    Every connector must implement:
    - schema(): Return Schema describing available tables and columns
    - fetch(): Yield rows (dicts) for SELECT operations

    Writable connectors must additionally implement:
    - insert(), update(), delete()
    """

    @abstractmethod
    def schema(self) -> Schema:
        """Return Schema describing all tables and their columns."""

    @abstractmethod
    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
        context: ConnectorContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows as dicts. Called for SELECT operations."""

    def insert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        context: ConnectorContext | None = None,
    ) -> WriteResult:
        """Insert rows. Raises NotImplementedError if not insertable."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support INSERT")

    def update(
        self,
        table: str,
        values: dict[str, Any],
        filters: list[dict[str, Any]],
        context: ConnectorContext | None = None,
    ) -> WriteResult:
        """Update rows matching filters with values."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support UPDATE")

    def delete(
        self,
        table: str,
        filters: list[dict[str, Any]],
        context: ConnectorContext | None = None,
    ) -> WriteResult:
        """Delete rows matching filters."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support DELETE")

    def capabilities(self) -> Capabilities:
        """Return Capabilities describing what this connector can do."""
        return Capabilities()  # read-only by default

    def close(self) -> None:  # noqa: B027 — intentional no-op default; override when needed
        """Clean up any resources (HTTP clients, DB connections, etc.)."""
