"""CSV read/write connector for Sidol."""

from __future__ import annotations

import csv
import pathlib
from collections.abc import Iterator
from typing import Any

import duckdb

from sidol.connectors.base import BaseConnector
from sidol.context import ConnectorContext
from sidol.types import Capabilities, Column, Schema, WriteResult

# DuckDB type -> sidol type mapping
_TYPE_MAP = {
    "VARCHAR": "text",
    "BIGINT": "int",
    "INTEGER": "int",
    "DOUBLE": "float",
    "FLOAT": "float",
    "BOOLEAN": "bool",
    "TIMESTAMP": "timestamp",
    "DATE": "timestamp",
}


class CSVConnector(BaseConnector):
    """Read/write connector for CSV files.

    Reads use DuckDB's native CSV scanner (fast, type-inferred).
    Writes append or overwrite the file using stdlib csv.

    Usage:
        conn = CSVConnector(path="./data/risks.csv")
        conn = CSVConnector(path="./data/risks.csv", writable=True)
    """

    def __init__(self, path: str, writable: bool = False, delimiter: str = ","):
        self.path = pathlib.Path(path).resolve()
        self._writable = writable
        self.delimiter = delimiter
        self._schema_cache: Schema | None = None
        # Derive table name from filename: risks.csv -> risks
        self.table_name = self.path.stem.lower().replace("-", "_").replace(" ", "_")

    def schema(self) -> Schema:
        """Return schema inferred from CSV file using DuckDB."""
        if self._schema_cache:
            return self._schema_cache

        if not self.path.exists():
            # Return empty schema for new writable files
            return Schema(tables={self.table_name: []})

        conn = duckdb.connect(":memory:")
        try:
            result = conn.execute(
                f"DESCRIBE SELECT * FROM read_csv_auto('{self.path}')"
            ).fetchall()
        finally:
            conn.close()

        cols = [
            Column(
                name=row[0],
                type=_TYPE_MAP.get(row[1].upper().split("(")[0], "text"),
            )
            for row in result
        ]
        self._schema_cache = Schema(tables={self.table_name: cols})
        return self._schema_cache

    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
        context: ConnectorContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows from CSV using DuckDB."""
        if not self.path.exists():
            return
        conn = duckdb.connect(":memory:")
        try:
            q = self._build_fetch_query(columns, filters, limit, offset)
            result = conn.execute(q)
            col_names = [d[0] for d in result.description] if result.description else []
            for row in result.fetchall():
                yield dict(zip(col_names, row, strict=False))
        finally:
            conn.close()

    def _build_fetch_query(
        self,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
    ) -> str:
        """Build a DuckDB SQL query string for this CSV file."""
        col_str = ", ".join(columns) if columns else "*"
        q = f"SELECT {col_str} FROM read_csv_auto('{self.path}')"
        where_parts = [self._filter_to_sql(f) for f in filters if "raw" not in f and self._filter_to_sql(f)]
        if where_parts:
            q += " WHERE " + " AND ".join(where_parts)
        if limit:
            q += f" LIMIT {limit}"
        if offset:
            q += f" OFFSET {offset}"
        return q

    def _filter_to_sql(self, f: dict[str, Any]) -> str:
        """Convert a single filter dict to a SQL predicate string."""
        col, op, val = f["col"], f["op"], f["val"]
        if val is None:
            return f"{col} IS NULL" if op == "=" else f"{col} IS NOT NULL"
        if isinstance(val, str):
            return f"{col} {op} '{val}'"
        return f"{col} {op} {val}"

    def insert(self, table: str, rows: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Append rows to CSV file."""
        if not self._writable:
            raise PermissionError(
                f"CSVConnector for '{self.path}' is read-only. "
                "Pass writable=True to enable writes."
            )

        if not rows:
            return WriteResult(affected_rows=0)

        file_exists = self.path.exists() and self.path.stat().st_size > 0
        fieldnames = list(rows[0].keys())

        with open(self.path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=self.delimiter)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)

        # Invalidate schema cache since we may have added new columns
        self._schema_cache = None

        return WriteResult(affected_rows=len(rows))

    def update(self, table: str, values: dict[str, Any], filters: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Update matching rows (full file rewrite)."""
        if not self._writable:
            raise PermissionError("CSVConnector is read-only.")

        if not self.path.exists():
            return WriteResult(affected_rows=0)

        all_rows = list(self.fetch(table, [], [], None, None))
        affected = 0

        for row in all_rows:
            if self._matches(row, filters):
                row.update(values)
                affected += 1

        self._rewrite(all_rows)
        return WriteResult(affected_rows=affected)

    def delete(self, table: str, filters: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Delete matching rows (full file rewrite)."""
        if not self._writable:
            raise PermissionError("CSVConnector is read-only.")

        if not self.path.exists():
            return WriteResult(affected_rows=0)

        all_rows = list(self.fetch(table, [], [], None, None))
        surviving = [r for r in all_rows if not self._matches(r, filters)]
        deleted = len(all_rows) - len(surviving)

        self._rewrite(surviving)
        return WriteResult(affected_rows=deleted)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=self._writable,
            updatable=self._writable,
            deletable=self._writable,
        )

    def _matches(self, row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
        """Check if row matches all filters."""
        for f in filters:
            if "raw" in f:
                continue  # Can't evaluate raw SQL in Python
            col, op, val = f["col"], f["op"], f["val"]
            row_val = row.get(col)

            if op == "=" and str(row_val) != str(val):
                return False
            if op == "!=" and str(row_val) == str(val):
                return False
            if op == ">" and not (row_val is not None and row_val > val):
                return False
            if op == "<" and not (row_val is not None and row_val < val):
                return False
            if op == ">=" and not (row_val is not None and row_val >= val):
                return False
            if op == "<=" and not (row_val is not None and row_val <= val):
                return False
        return True

    def _rewrite(self, rows: list[dict[str, Any]]) -> None:
        """Rewrite CSV file with new rows."""
        if not rows:
            self.path.write_text("")
            return

        with open(self.path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=self.delimiter)
            writer.writeheader()
            writer.writerows(rows)
