"""Connector registry and table resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sidol.connectors.base import BaseConnector
from sidol.errors import UnknownTableError


def normalize_table_name(name: str) -> str:
    return str(name or "").strip().strip('"`[]').lower()


@dataclass(frozen=True)
class RegisteredTable:
    logical_table: str
    native_table: str
    connector: BaseConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._tables: dict[str, RegisteredTable] = {}

    def register_table(
        self,
        logical_table: str,
        connector: BaseConnector,
        native_table: str | None = None,
    ) -> None:
        if not logical_table:
            raise ValueError("logical_table is required")
        native = native_table or logical_table
        self._tables[normalize_table_name(logical_table)] = RegisteredTable(
            logical_table=logical_table,
            native_table=native,
            connector=connector,
        )

    def resolve(self, table: str) -> RegisteredTable:
        key = normalize_table_name(table)
        try:
            return self._tables[key]
        except KeyError as exc:
            raise UnknownTableError(f"Unknown table: {table}") from exc

    def registered_tables(self) -> list[RegisteredTable]:
        return list(self._tables.values())
