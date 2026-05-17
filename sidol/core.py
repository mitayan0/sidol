"""Core Session class — the main sidol API."""

from __future__ import annotations

from typing import Any, cast

import duckdb
import pyarrow as pa
import sqlglot
import sqlglot.expressions as exp

from sidol.connectors.base import BaseConnector
from sidol.context import ConnectorContext
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
from sidol.types import QueryResult, WriteResult


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
        self._default_connector: BaseConnector | None = None
        self._context = ConnectorContext()

    def register(self, name: str, connector: BaseConnector) -> Session:
        """Register a connector under a table name.

        Args:
            name: The logical table name for SQL queries
            connector: A BaseConnector instance
        """
        self._registry.register_table(name, connector)
        return self

    def use(self, connector: BaseConnector) -> Session:
        """Set a default connector used for any table not explicitly registered."""
        self._default_connector = connector
        return self

    def unregister(self, name: str) -> Session:
        """Remove a registered connector and close it."""
        entry = self._registry._tables.get(name.lower())
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

        # Handle read results (QueryResult) — return Arrow Table
        return pa.Table.from_pydict(
            {col: [row[i] for row in result.rows] for i, col in enumerate(result.columns)}
        ) if result.rows else pa.table({col: [] for col in result.columns})

    def execute(self, query: str) -> QueryResult | WriteResult:
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
            return connector.insert(table, extract_insert_rows(tree), context=self._context)

        if stype == "UPDATE":
            self._check_capability(connector, caps.updatable, "UPDATE")
            return connector.update(table, extract_update_set(tree), extract_filters(tree, require_where=True), context=self._context)

        if stype == "DELETE":
            self._check_capability(connector, caps.deletable, "DELETE")
            return connector.delete(table, extract_filters(tree, require_where=True), context=self._context)

        raise UnsupportedSQLError(f"Statement type not supported: {stype}")

    def _check_capability(self, connector: BaseConnector, supported: bool, operation: str) -> None:
        """Raise CapabilityError if the connector does not support the operation."""
        if not supported:
            raise CapabilityError(connector.__class__.__name__, operation)

    def _execute_select(self, query: str) -> QueryResult:
        """Execute SELECT via DuckDB after registering necessary tables."""
        tree = cast(exp.Expression, sqlglot.parse_one(query))
        tables = [t.name for t in tree.find_all(exp.Table)]
        # Pushdown only when there is a single table — multi-table queries
        # (JOINs) require per-table filter attribution which is out of v1 scope.
        where_filters = extract_filters(tree) if len(tables) == 1 else []
        for name in tables:
            self._register_table_with_duckdb(name, where_filters)
        arrow_result = self._duckdb.execute(query).to_arrow_table()
        cols = list(arrow_result.column_names)
        result_rows = [tuple(r.values()) for r in arrow_result.to_pylist()]
        return QueryResult(columns=cols, rows=result_rows)

    def _register_table_with_duckdb(
        self, table_name: str, pushdown_filters: list[dict[str, Any]] | None = None
    ) -> None:
        """Fetch data from connector and register as a view in DuckDB."""
        try:
            entry = self._registry.resolve(table_name)
            connector = entry.connector
            native_table = entry.native_table
        except UnknownTableError:
            if self._default_connector is None:
                return
            connector = self._default_connector
            native_table = table_name

        caps = connector.capabilities()
        filters = pushdown_filters if (caps.filter_pushdown and pushdown_filters) else []
        fetched = list(connector.fetch(native_table, None, filters, None, None, context=self._context))
        if fetched:
            # Map sidol types to pyarrow types if schema is available
            schema_hints = connector.schema().tables.get(native_table, [])
            pa_schema = None
            if schema_hints:
                fields = []
                type_map = {
                    "text": pa.string(),
                    "int": pa.int64(),
                    "float": pa.float64(),
                    "bool": pa.bool_(),
                    "json": pa.string(),
                    "timestamp": pa.string(), # Fallback for now
                }
                for col in schema_hints:
                    fields.append(pa.field(col.name, type_map.get(col.type, pa.string()), nullable=True))
                pa_schema = pa.schema(fields)

            try:
                if pa_schema:
                    # Filter data to only include columns in schema, or arrow might complain
                    schema_cols = {f.name for f in pa_schema}
                    safe_fetched = []
                    for row in fetched:
                        safe_fetched.append({k: v for k, v in row.items() if k in schema_cols})
                    arrow_table = pa.Table.from_pylist(safe_fetched, schema=pa_schema)
                else:
                    arrow_table = pa.Table.from_pylist(fetched)
                self._duckdb.register(table_name, arrow_table)
            except Exception:
                # pyarrow rejects rows whose column types are inconsistent (e.g. a
                # column that holds both ints and strings across pages).  Converting
                # every value to str is lossy but always succeeds and lets DuckDB
                # still run the query.  A connector that returns clean types should
                # never reach this branch.
                stringified = []
                for row in fetched:
                    stringified.append({k: str(v) if v is not None else None for k, v in row.items()})
                arrow_table = pa.Table.from_pylist(stringified)
                self._duckdb.register(table_name, arrow_table)

    def _get_connector(self, table: str) -> BaseConnector:
        """Get connector for a table, raising helpful error if not found."""
        try:
            entry = self._registry.resolve(table)
            return entry.connector
        except Exception as exc:
            if self._default_connector is not None:
                return self._default_connector
            available = list(self._registry._tables.keys())
            raise TableNotFoundError(table, available) from exc

    def tables(self) -> list[str]:
        """Return list of registered table names, or all tables from default connector."""
        if self._default_connector is not None and hasattr(self._default_connector, "list_tables"):
            return cast(list[str], self._default_connector.list_tables())
        return list(self._registry._tables.keys())

    def close(self) -> None:
        """Close all connectors and cleanup."""
        for entry in self._registry.registered_tables():
            entry.connector.close()
        if self._default_connector is not None:
            self._default_connector.close()
        self._duckdb.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        self.close()


def connect() -> Session:
    """Create a new sidol session."""
    return Session()
