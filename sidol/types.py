"""Public value types used by Sidol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # "text", "int", "float", "bool", "json", "timestamp"
    nullable: bool = True
    primary_key: bool = False


@dataclass(frozen=True)
class Schema:
    """Schema description: table_name -> list of columns."""
    tables: dict[str, list["Column"]]


@dataclass(frozen=True)
class Capabilities:
    """What a connector can do."""
    readable: bool = True
    insertable: bool = False
    updatable: bool = False
    deletable: bool = False
    bulk_insert: bool = False
    transactions: bool = False
    filter_pushdown: bool = False


@dataclass(frozen=True)
class WriteResult:
    """Result of INSERT/UPDATE/DELETE operations."""
    affected_rows: int = 0
    returned: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class QueryResult:
    """Result of SELECT operations."""
    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    arrow_table: Any | None = None


@dataclass(frozen=True)
class Result:
    """Generic result that works for both reads and writes."""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    affected_rows: int = 0
    returned: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows) if self.rows else self.affected_rows
