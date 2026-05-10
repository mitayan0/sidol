"""Tests for sidol core functionality using the new Session API."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx
import pyarrow as pa

import sidol
from sidol import (
    BaseConnector,
    Capabilities,
    Schema,
    Column,
    WriteResult,
    TableNotFoundError,
    UnsupportedSQLError,
)
from sidol.connectors.csv_ import CSVConnector
from sidol.connectors.servicenow import ServiceNowConnector
from sidol.connectors.sqlite_ import SQLiteConnector
from sidol.router import (
    parse,
    extract_insert_rows,
    extract_update_set,
    extract_filters,
)
import sqlglot.expressions as exp


# ---------------------------------------------------------------------------
# Shared fake connector
# ---------------------------------------------------------------------------

class FakeConnector(BaseConnector):
    """In-memory connector for unit tests."""

    def __init__(self, rows: list[dict], writable: bool = False):
        self._rows = rows
        self._writable = writable
        self.inserts: list[list[dict]] = []
        self.updates: list[tuple] = []
        self.deletes: list[list[dict]] = []

    def schema(self) -> Schema:
        cols = [Column(k, "text") for k in (self._rows[0] if self._rows else {})]
        return Schema(tables={"items": cols})

    def fetch(self, table, columns, filters, limit, offset):
        for row in self._rows:
            yield row

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=self._writable,
            updatable=self._writable,
            deletable=self._writable,
        )

    def insert(self, table, rows) -> WriteResult:
        self.inserts.append(rows)
        return WriteResult(affected_rows=len(rows))

    def update(self, table, values, filters) -> WriteResult:
        self.updates.append((values, filters))
        return WriteResult(affected_rows=1)

    def delete(self, table, filters) -> WriteResult:
        self.deletes.append(filters)
        return WriteResult(affected_rows=1)


# ---------------------------------------------------------------------------
# Router unit tests  (zero network, zero disk)
# ---------------------------------------------------------------------------

class RouterParseTests(unittest.TestCase):

    def test_parse_insert_values(self):
        tree = parse("INSERT INTO incident (short_description, active, priority) VALUES ('hello', TRUE, 2)")
        rows = extract_insert_rows(tree)
        self.assertEqual(rows, [{"short_description": "hello", "active": True, "priority": 2}])

    def test_parse_update_set(self):
        tree = parse("UPDATE incident SET state = '2', active = FALSE WHERE sys_id = 'abc'")
        values = extract_update_set(tree)
        self.assertEqual(values, {"state": "2", "active": False})

    def test_parse_filters_and(self):
        tree = parse("DELETE FROM incident WHERE sys_id = 'abc' AND number = 'INC001'")
        filters = extract_filters(tree)
        self.assertIn({"col": "sys_id", "op": "=", "val": "abc"}, filters)
        self.assertIn({"col": "number", "op": "=", "val": "INC001"}, filters)

    def test_delete_without_where_raises(self):
        tree = parse("DELETE FROM incident")
        with self.assertRaises(UnsupportedSQLError):
            extract_filters(tree, require_where=True)

    def test_insert_requires_column_list(self):
        tree = parse("INSERT INTO t VALUES (1)")
        with self.assertRaises(UnsupportedSQLError):
            extract_insert_rows(tree)

    def test_literal_negative_int(self):
        tree = parse("INSERT INTO t (priority) VALUES (-1)")
        rows = extract_insert_rows(tree)
        self.assertEqual(rows[0]["priority"], -1)


# ---------------------------------------------------------------------------
# Session integration tests  (in-memory, zero network)
# ---------------------------------------------------------------------------

class SessionTests(unittest.TestCase):

    def _make_session(self, rows=None, writable=False):
        rows = rows or [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
        db = sidol.connect()
        db.register("items", FakeConnector(rows, writable=writable))
        return db

    def test_context_manager(self):
        with sidol.connect() as db:
            db.register("items", FakeConnector([{"id": 1}]))
            tbl = db.sql("SELECT * FROM items")
        self.assertEqual(tbl.num_rows, 1)

    def test_select_returns_arrow_table(self):
        db = self._make_session()
        tbl = db.sql("SELECT * FROM items")
        self.assertIsInstance(tbl, pa.Table)
        self.assertEqual(tbl.num_rows, 2)

    def test_select_with_filter(self):
        db = self._make_session()
        tbl = db.sql("SELECT name FROM items WHERE id = 2")
        self.assertEqual(tbl.column("name").to_pylist(), ["beta"])

    def test_select_join(self):
        db = sidol.connect()
        db.register("items",  FakeConnector([{"id": 1, "name": "alpha"}]))
        db.register("owners", FakeConnector([{"item_id": 1, "owner": "sidol"}]))
        tbl = db.sql("SELECT i.name, o.owner FROM items i JOIN owners o ON i.id = o.item_id")
        self.assertEqual(tbl.column("owner").to_pylist(), ["sidol"])

    def test_unknown_table_raises(self):
        db = sidol.connect()
        with self.assertRaises(TableNotFoundError):
            db.sql("INSERT INTO missing (name) VALUES ('x')")

    def test_insert_routes_to_connector(self):
        connector = FakeConnector([], writable=True)
        db = sidol.connect()
        db.register("incident", connector)
        result = db.sql("INSERT INTO incident (name) VALUES ('test')")
        self.assertEqual(result["affected_rows"], 1)
        self.assertEqual(connector.inserts[0], [{"name": "test"}])

    def test_update_routes_to_connector(self):
        connector = FakeConnector([], writable=True)
        db = sidol.connect()
        db.register("incident", connector)
        result = db.sql("UPDATE incident SET state = 'closed' WHERE sys_id = 'abc'")
        self.assertEqual(result["affected_rows"], 1)
        values, filters = connector.updates[0]
        self.assertEqual(values, {"state": "closed"})
        self.assertIn({"col": "sys_id", "op": "=", "val": "abc"}, filters)

    def test_delete_routes_to_connector(self):
        connector = FakeConnector([], writable=True)
        db = sidol.connect()
        db.register("incident", connector)
        result = db.sql("DELETE FROM incident WHERE sys_id = 'xyz'")
        self.assertEqual(result["affected_rows"], 1)

    def test_read_only_connector_rejects_insert(self):
        from sidol import CapabilityError
        connector = FakeConnector([], writable=False)
        db = sidol.connect()
        db.register("incident", connector)
        with self.assertRaises(CapabilityError):
            db.sql("INSERT INTO incident (name) VALUES ('x')")

    def test_tables_returns_names(self):
        db = self._make_session()
        self.assertIn("items", db.tables())


# ---------------------------------------------------------------------------
# CSV connector integration tests  (temp files, no network)
# ---------------------------------------------------------------------------

class CSVConnectorTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path = str(Path(self._tmpdir) / "data.csv")

    def test_write_and_read(self):
        conn = CSVConnector(self._path, writable=True)
        conn.insert("data", [{"id": "1", "name": "alice"}, {"id": "2", "name": "bob"}])
        rows = list(conn.fetch("data", None, [], None, None))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "alice")

    def test_csv_via_session(self):
        conn = CSVConnector(self._path, writable=True)
        conn.insert("data", [{"id": "1", "score": "42"}, {"id": "2", "score": "7"}])

        db = sidol.connect()
        db.register("data", conn)
        tbl = db.sql("SELECT * FROM data")
        self.assertEqual(tbl.num_rows, 2)

    def test_read_only_raises(self):
        conn = CSVConnector(self._path, writable=False)
        with self.assertRaises(PermissionError):
            conn.insert("data", [{"id": "1"}])


# ---------------------------------------------------------------------------
# SQLite connector integration tests  (in-memory DB, no network)
# ---------------------------------------------------------------------------

class SQLiteConnectorTests(unittest.TestCase):

    def setUp(self):
        self._db_path = ":memory:"
        # Pre-create a real on-disk temp file for tests that need persistence
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmpfile.close()
        self._disk_path = self._tmpfile.name
        # Seed the table
        con = sqlite3.connect(self._disk_path)
        con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        con.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta')")
        con.commit()
        con.close()

    def test_schema_discovery(self):
        conn = SQLiteConnector(self._disk_path)
        schema = conn.schema()
        self.assertIn("items", schema.tables)
        col_names = [c.name for c in schema.tables["items"]]
        self.assertIn("id", col_names)
        self.assertIn("name", col_names)

    def test_fetch_rows(self):
        conn = SQLiteConnector(self._disk_path)
        rows = list(conn.fetch("items", None, [], None, None))
        self.assertEqual(len(rows), 2)

    def test_insert_update_delete(self):
        conn = SQLiteConnector(self._disk_path)

        r = conn.insert("items", [{"id": 3, "name": "gamma"}])
        self.assertEqual(r.affected_rows, 1)

        r = conn.update("items", {"name": "GAMMA"}, [{"col": "id", "op": "=", "val": 3}])
        self.assertEqual(r.affected_rows, 1)

        r = conn.delete("items", [{"col": "id", "op": "=", "val": 3}])
        self.assertEqual(r.affected_rows, 1)

        rows = list(conn.fetch("items", None, [], None, None))
        self.assertEqual(len(rows), 2)

    def test_sqlite_via_session(self):
        conn = SQLiteConnector(self._disk_path)
        db = sidol.connect()
        db.register("items", conn)
        tbl = db.sql("SELECT name FROM items WHERE id = 1")
        self.assertEqual(tbl.column("name").to_pylist(), ["alpha"])


# ---------------------------------------------------------------------------
# ServiceNow connector tests  (mocked HTTP, no network)
# ---------------------------------------------------------------------------

class ServiceNowConnectorTests(unittest.TestCase):

    def _make_connector(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return ServiceNowConnector(
            instance="example",
            table="incident",
            client=client,
        )

    def test_insert_posts_to_table_api(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"sys_id": "abc123", "number": "INC001"}})

        conn = self._make_connector(handler)
        result = conn.insert("incident", [{"short_description": "test"}])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertIn("/api/now/table/incident", requests[0].url.path)
        self.assertEqual(result.affected_rows, 1)

    def test_update_patches_by_sys_id(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"sys_id": "abc"}})

        conn = self._make_connector(handler)
        result = conn.update("incident", {"state": "2"}, [{"col": "sys_id", "op": "=", "val": "abc"}])
        self.assertEqual(requests[0].method, "PATCH")
        self.assertIn("/abc", requests[0].url.path)
        self.assertEqual(result.affected_rows, 1)

    def test_delete_by_sys_id(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(204)

        conn = self._make_connector(handler)
        result = conn.delete("incident", [{"col": "sys_id", "op": "=", "val": "abc"}])
        self.assertEqual(requests[0].method, "DELETE")
        self.assertIn("/abc", requests[0].url.path)
        self.assertEqual(result.affected_rows, 1)

    def test_capabilities(self):
        conn = self._make_connector(lambda r: httpx.Response(200, json={"result": []}))
        caps = conn.capabilities()
        self.assertTrue(caps.insertable)
        self.assertTrue(caps.updatable)
        self.assertTrue(caps.deletable)
        self.assertTrue(caps.filter_pushdown)


if __name__ == "__main__":
    unittest.main()
