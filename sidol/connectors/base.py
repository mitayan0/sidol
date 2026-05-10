"""Connector contract for Sidol sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator

from sidol.types import Column, Schema, Capabilities, WriteResult


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
        filters: list[dict],     # [{"col": "x", "op": "=", "val": 1}]
        limit: int | None,
        offset: int | None,
    ) -> Iterator[dict]:
        """Yield rows as dicts. Called for SELECT operations.
        
        Args:
            table: Table name to fetch from
            columns: List of column names to fetch, or None for all
            filters: List of filter dicts with keys: col, op, val
            limit: Maximum rows to return
            offset: Rows to skip
        """

    def insert(self, table: str, rows: list[dict]) -> WriteResult:
        """Insert rows. Raises NotImplementedError if not insertable."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support INSERT")

    def update(self, table: str, values: dict, filters: list[dict]) -> WriteResult:
        """Update rows matching filters with values."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support UPDATE")

    def delete(self, table: str, filters: list[dict]) -> WriteResult:
        """Delete rows matching filters."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support DELETE")

    def capabilities(self) -> Capabilities:
        """Return Capabilities describing what this connector can do."""
        return Capabilities()  # read-only by default

    def close(self) -> None:
        """Clean up any resources (HTTP clients, DB connections, etc.)."""
        pass
