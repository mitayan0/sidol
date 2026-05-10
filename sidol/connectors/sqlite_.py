"""SQLite connector for Sidol."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

from sidol.connectors.base import BaseConnector
from sidol.types import Capabilities, Column, Schema, WriteResult

# SQLite affinity -> sidol type mapping
_SQLITE_TYPE_MAP = {
    "TEXT": "text",
    "INTEGER": "int",
    "REAL": "float",
    "NUMERIC": "float",
    "BLOB": "text",
}


def _build_where(filters: list[dict[str, Any]]) -> tuple[str, list[Any]]:
    """Return a (WHERE clause string, params list) pair for the given filters."""
    parts = []
    params = []
    for f in filters:
        if "raw" in f:
            continue
        parts.append(f"{f['col']} {f['op']} ?")
        params.append(f["val"])
    clause = (" WHERE " + " AND ".join(parts)) if parts else ""
    return clause, params


class SQLiteConnector(BaseConnector):
    """Full CRUD connector for SQLite.

    Reads can go through DuckDB's native SQLite extension for complex queries.
    This connector provides schema discovery and write operations.

    Usage:
        conn = SQLiteConnector(path="./mydb.sqlite")
    """

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._schema_cache: Schema | None = None

    def schema(self) -> Schema:
        """Return schema from SQLite table_info."""
        if self._schema_cache:
            return self._schema_cache
        conn = sqlite3.connect(self.path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            table_names = [row[0] for row in cursor.fetchall()]
            tables = {name: self._table_columns(cursor, name) for name in table_names}
            self._schema_cache = Schema(tables=tables)
            return self._schema_cache
        finally:
            conn.close()

    def _table_columns(self, cursor: sqlite3.Cursor, table_name: str) -> list[Column]:
        """Return Column list for one table using PRAGMA table_info."""
        cursor.execute(f"PRAGMA table_info({table_name})")
        cols = []
        for _, name, col_type, notnull, _, pk in cursor.fetchall():
            sidol_type = _SQLITE_TYPE_MAP.get((col_type or "TEXT").upper().split("(")[0], "text")
            cols.append(Column(name=name, type=sidol_type, nullable=not bool(notnull), primary_key=bool(pk)))
        return cols

    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows from SQLite."""
        col_str = ", ".join(columns) if columns else "*"
        where_clause, params = _build_where(filters)
        q = f"SELECT {col_str} FROM {table}{where_clause}"
        if limit:
            q += " LIMIT ?"
            params.append(limit)
        if offset:
            q += " OFFSET ?"
            params.append(offset)
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(q, params):
                yield dict(row)
        finally:
            conn.close()

    def insert(self, table: str, rows: list[dict[str, Any]]) -> WriteResult:
        """Insert rows into SQLite."""
        if not rows:
            return WriteResult(affected_rows=0)

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(columns)
        q = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders})"

        conn = sqlite3.connect(self.path)
        try:
            cursor = conn.cursor()
            for row in rows:
                cursor.execute(q, [row.get(c) for c in columns])
            conn.commit()

            # Get last row IDs if possible
            returned = []
            for i, row in enumerate(rows):
                row_id = cursor.lastrowid if i == len(rows) - 1 else None
                returned.append({**row, "rowid": row_id} if row_id else row)

            return WriteResult(affected_rows=len(rows), returned=returned)
        finally:
            conn.close()

    def update(self, table: str, values: dict[str, Any], filters: list[dict[str, Any]]) -> WriteResult:
        """Update rows matching filters."""
        if not values:
            return WriteResult(affected_rows=0)
        set_clause = ", ".join(f"{k} = ?" for k in values)
        where_clause, where_params = _build_where(filters)
        q = f"UPDATE {table} SET {set_clause}{where_clause}"
        params = list(values.values()) + where_params
        conn = sqlite3.connect(self.path)
        try:
            cursor = conn.execute(q, params)
            conn.commit()
            return WriteResult(affected_rows=cursor.rowcount)
        finally:
            conn.close()

    def delete(self, table: str, filters: list[dict[str, Any]]) -> WriteResult:
        """Delete rows matching filters."""
        where_clause, params = _build_where(filters)
        q = f"DELETE FROM {table}{where_clause}"
        conn = sqlite3.connect(self.path)
        try:
            cursor = conn.execute(q, params)
            conn.commit()
            return WriteResult(affected_rows=cursor.rowcount)
        finally:
            conn.close()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=True,
            updatable=True,
            deletable=True,
            transactions=True,
            filter_pushdown=True,
        )

    def close(self) -> None:
        """SQLite connections are per-operation, nothing to close at this level."""
        pass
