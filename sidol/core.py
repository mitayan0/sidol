"""Core Session class — the main sidol API."""

from __future__ import annotations

from typing import Any

import duckdb
import pyarrow as pa
import sqlglot
import sqlglot.expressions as exp

from sidol.connectors.base import BaseConnector
from sidol.errors import (
    CapabilityError,
    TableNotFoundError,
    UnknownTableError,
    UnsupportedSQLError,
)
from sidol.registry import ConnectorRegistry
from sidol.router import (
    extract_filters,
    extract_insert_rows,
    extract_table,
    extract_update_set,
    parse,
    statement_type,
)
from sidol.types import Result, WriteResult


class Session:
    """The main sidol object for SQL operations across multiple sources.

    Usage:
        db = sidol.connect()
        db.register("incidents", ServiceNowConnector(...))
        db.register("risks", PostgreSQLConnector(...))

        # SELECT returns a pyarrow.Table
        tbl = db.sql("SELECT * FROM incidents WHERE priority = 1")

        # Write operations
        db.sql("UPDATE incidents SET state='closed' WHERE number='INC001'")
        db.sql("INSERT INTO incidents (description) VALUES ('New issue')")
    """

    def __init__(self) -> None:
        self._registry = ConnectorRegistry()
        self._duckdb = duckdb.connect(":memory:")

    def register(self, name: str, connector: BaseConnector) -> Session:
        """Register a connector under a table name.

        Args:
            name: The logical table name for SQL queries
            connector: A BaseConnector instance
        """
        name = name.lower()
        self._registry.register_table(name, connector)
        return self

    def unregister(self, name: str) -> Session:
        """Remove a registered connector and close it."""
        name = name.lower()
        entry = self._registry._tables.get(name)
        if not entry:
            return self
        entry.connector.close()
        del self._registry._tables[name]
        self._duckdb.execute(f"DROP VIEW IF EXISTS {name}")
        return self

    def sql(self, query: str) -> pa.Table | dict[str, Any]:
        """Execute SQL and return an Arrow Table for SELECT, or dict for DML.

        Args:
            query: SQL statement (SELECT, INSERT, UPDATE, DELETE)

        Returns:
            pyarrow.Table for SELECT queries  (call .to_pandas() if you need a DataFrame)
            dict with 'affected_rows' and 'returned' for DML queries
        """
        result = self.execute(query)

        # Handle write results
        if isinstance(result, WriteResult):
            return {"affected_rows": result.affected_rows, "returned": result.returned}

        # Handle read results — return Arrow Table
        if isinstance(result, Result):
            return pa.Table.from_pydict(
                {col: [row[i] for row in result.rows] for i, col in enumerate(result.columns)}
            ) if result.rows else pa.table({col: [] for col in result.columns})

        return result

    def execute(self, query: str) -> Result | WriteResult:
        """Execute any SQL and return a Result or WriteResult object."""
        tree = parse(query)
        stype = statement_type(tree)

        if stype == "SELECT":
            return self._execute_select(query)

        table = extract_table(tree)
        connector = self._get_connector(table)
        caps = connector.capabilities()

        if stype == "INSERT":
            self._check_capability(connector, caps.insertable, "INSERT")
            return connector.insert(table, extract_insert_rows(tree))

        if stype == "UPDATE":
            self._check_capability(connector, caps.updatable, "UPDATE")
            return connector.update(table, extract_update_set(tree), extract_filters(tree, require_where=True))

        if stype == "DELETE":
            self._check_capability(connector, caps.deletable, "DELETE")
            return connector.delete(table, extract_filters(tree, require_where=True))

        raise UnsupportedSQLError(f"Statement type not supported: {stype}")

    def _check_capability(self, connector: BaseConnector, supported: bool, operation: str) -> None:
        """Raise CapabilityError if the connector does not support the operation."""
        if not supported:
            raise CapabilityError(connector.__class__.__name__, operation)

    def _execute_select(self, query: str) -> Result:
        """Execute SELECT via DuckDB with registered connectors as Arrow sources."""
        tree = sqlglot.parse_one(query)
        tables = [t.name for t in tree.find_all(exp.Table)]

        for table_name in tables:
            try:
                entry = self._registry.resolve(table_name)
            except UnknownTableError:
                continue  # CTE or subquery alias — skip

            fetched = list(entry.connector.fetch(entry.native_table, None, [], None, None))
            if fetched:
                arrow_table = pa.Table.from_pylist(fetched)
                self._duckdb.register(table_name, arrow_table)

        arrow_result = self._duckdb.execute(query).to_arrow_table()
        cols = list(arrow_result.column_names)
        result_rows = [tuple(r.values()) for r in arrow_result.to_pylist()]
        return Result(columns=cols, rows=result_rows)

    def _get_connector(self, table: str) -> BaseConnector:
        """Get connector for a table, raising helpful error if not found."""
        try:
            entry = self._registry.resolve(table)
            return entry.connector
        except Exception as e:
            available = list(self._registry._tables.keys())
            raise TableNotFoundError(table, available) from e

    def tables(self) -> list[str]:
        """Return list of registered table names."""
        return list(self._registry._tables.keys())

    def close(self) -> None:
        """Close all connectors and cleanup."""
        for entry in self._registry.registered_tables():
            entry.connector.close()
        self._duckdb.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        self.close()


def connect() -> Session:
    """Create a new sidol session."""
    return Session()
