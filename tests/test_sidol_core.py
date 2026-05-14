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
    Column,
    Schema,
    TableNotFoundError,
    UnsupportedSQLError,
    WriteError,
    WriteResult,
)
from sidol.connectors.csv_ import CSVConnector
from sidol.connectors.servicenow import ServiceNowConnector
from sidol.connectors.sqlite_ import SQLiteConnector
from sidol.router import (
    extract_filters,
    extract_insert_rows,
    extract_update_set,
    parse,
)


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

    def test_schema_uses_ui_meta_endpoint(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"columns": {
                "sys_id": {"type": "GUID", "mandatory": False, "read_only": True},
                "number": {"type": "string", "mandatory": False, "read_only": True},
                "priority": {"type": "integer", "mandatory": False, "read_only": False},
            }}})

        conn = self._make_connector(handler)
        schema = conn.schema()

        self.assertIn("/api/now/ui/meta/incident", requests[0].url.path)
        cols = {c.name: c for c in schema.tables["incident"]}
        self.assertIn("sys_id", cols)
        self.assertTrue(cols["sys_id"].primary_key)
        self.assertIn("number", cols)
        self.assertEqual(cols["priority"].type, "int")

    def test_fetch_respects_limit_with_pagination(self):
        requests = []

        def handler(request):
            requests.append(request)
            limit = int(request.url.params["sysparm_limit"])
            return httpx.Response(
                200,
                json={"result": [{"sys_id": f"r{len(requests)}-{i}"} for i in range(limit)]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        conn = ServiceNowConnector(
            instance="example",
            table="incident",
            client=client,
            page_size=2,
        )
        rows = list(conn.fetch("incident", None, [], limit=5, offset=0))
        self.assertEqual(len(rows), 5)
        limits = [int(request.url.params["sysparm_limit"]) for request in requests]
        self.assertEqual(limits, [2, 2, 1])
        offsets = [int(request.url.params["sysparm_offset"]) for request in requests]
        self.assertEqual(offsets, [0, 2, 4])

    def test_sysparm_query_escapes_caret(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": []})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        conn = ServiceNowConnector(instance="example", table="incident", client=client)
        filters = [{"col": "short_description", "op": "=", "val": "a^b"}]
        list(conn.fetch("incident", None, filters, limit=1, offset=0))
        q = requests[0].url.params.get("sysparm_query")
        self.assertIsNotNone(q)
        self.assertIn("short_description=a^^b", q)

    def test_sysparm_fields_accepts_dot_walk(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": []})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        conn = ServiceNowConnector(instance="example", table="incident", client=client)
        list(conn.fetch("incident", ["number", "caller_id.name"], [], limit=1, offset=0))
        fields = requests[0].url.params.get("sysparm_fields")
        self.assertEqual(fields, "number,caller_id.name")

    def test_sysparm_display_value_all(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": []})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        conn = ServiceNowConnector(
            instance="example",
            table="incident",
            client=client,
            sysparm_display_value=True,
        )
        list(conn.fetch("incident", None, [], limit=1, offset=0))
        dv = requests[0].url.params.get("sysparm_display_value")
        self.assertEqual(dv, "all")

    def test_oauth_refresh_on_401(self):
        calls = []

        def handler(request):
            calls.append((request.method, str(request.url)))
            if "oauth_token.do" in str(request.url):
                return httpx.Response(200, json={"access_token": "good", "refresh_token": "r2"})
            if request.headers.get("Authorization") != "Bearer good":
                return httpx.Response(401, json={"error": {"message": "Invalid token"}})
            return httpx.Response(200, json={"result": [{"sys_id": "1"}]})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        conn = ServiceNowConnector(
            instance="example",
            table="incident",
            oauth_client_id="cid",
            oauth_client_secret="sec",
            oauth_refresh_token="r1",
            oauth_access_token="bad",
            client=client,
        )
        rows = list(conn.fetch("incident", None, [], limit=1, offset=0))
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("oauth_token.do" in u for _, u in calls))

    def test_insert_write_error_includes_servicenow_message(self):
        def handler(request):
            return httpx.Response(
                400,
                json={"error": {"message": "Required field missing", "detail": "short_description"}},
            )

        conn = self._make_connector(handler)
        with self.assertRaises(WriteError) as ctx:
            conn.insert("incident", [{"priority": "1"}])
        self.assertIn("Required field missing", str(ctx.exception))
        self.assertIn("short_description", str(ctx.exception))


# ---------------------------------------------------------------------------
# Session.use() — default connector (zero network, zero disk)
# ---------------------------------------------------------------------------

class DefaultConnectorTests(unittest.TestCase):

    def test_use_enables_any_table_for_select(self):
        rows = [{"id": 1, "name": "alpha"}]
        db = sidol.connect()
        db.use(FakeConnector(rows))
        tbl = db.sql("SELECT * FROM anything")
        self.assertEqual(tbl.num_rows, 1)

    def test_use_dml_routes_to_default_connector(self):
        connector = FakeConnector([], writable=True)
        db = sidol.connect()
        db.use(connector)
        result = db.sql("INSERT INTO whatever (name) VALUES ('test')")
        self.assertEqual(result["affected_rows"], 1)

    def test_register_takes_priority_over_use(self):
        default_conn = FakeConnector([{"id": 99}])
        specific_conn = FakeConnector([{"id": 1}])
        db = sidol.connect()
        db.use(default_conn)
        db.register("specific", specific_conn)
        tbl = db.sql("SELECT * FROM specific")
        self.assertEqual(tbl.column("id").to_pylist(), [1])

    def test_use_returns_session_for_chaining(self):
        db = sidol.connect()
        result = db.use(FakeConnector([]))
        self.assertIs(result, db)


# ---------------------------------------------------------------------------
# ServiceNow multi-table tests  (mocked HTTP)
# ---------------------------------------------------------------------------

class ServiceNowMultiTableTests(unittest.TestCase):

    def _make_multi_connector(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return ServiceNowConnector(instance="example", client=client)

    def test_fetch_uses_table_arg_in_url(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": [{"sys_id": "x"}]})

        conn = self._make_multi_connector(handler)
        list(conn.fetch("problem", None, [], limit=1, offset=0))
        self.assertIn("/api/now/table/problem", requests[0].url.path)

    def test_insert_uses_table_arg_in_url(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"result": {"sys_id": "abc"}})

        conn = self._make_multi_connector(handler)
        conn.insert("change_request", [{"short_description": "test"}])
        self.assertIn("/api/now/table/change_request", requests[0].url.path)

    def test_session_use_routes_two_tables(self):
        def handler(request):
            if "incident" in request.url.path:
                return httpx.Response(200, json={"result": [{"id": "inc1"}]})
            if "problem" in request.url.path:
                return httpx.Response(200, json={"result": [{"id": "prb1"}]})
            return httpx.Response(200, json={"result": []})

        conn = self._make_multi_connector(handler)
        db = sidol.connect()
        db.use(conn)

        inc = db.sql("SELECT * FROM incident")
        prb = db.sql("SELECT * FROM problem")
        self.assertEqual(inc.column("id").to_pylist(), ["inc1"])
        self.assertEqual(prb.column("id").to_pylist(), ["prb1"])

    def test_schema_returns_empty_in_multi_table_mode(self):
        conn = self._make_multi_connector(lambda r: httpx.Response(200, json={"result": []}))
        schema = conn.schema()
        self.assertEqual(schema.tables, {})


if __name__ == "__main__":
    unittest.main()
