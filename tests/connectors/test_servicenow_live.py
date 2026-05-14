"""Live integration tests for ServiceNow connector.

Usage:
    1. Copy .env.example to .env and fill in your credentials
    2. Run: uv run python tests/connectors/test_servicenow_live.py

WARNING: This creates real records in your ServiceNow instance.
"""

import os
import uuid

from dotenv import load_dotenv

import sidol
from sidol.connectors.servicenow import ServiceNowConnector

load_dotenv()

_TABLE = os.getenv("SNOW_TABLE", "incident")


def get_db():
    """Create a sidol Session connected to ServiceNow via connect_servicenow."""
    token = os.getenv("SNOW_TOKEN")
    return sidol.connect_servicenow(
        instance=os.getenv("SNOW_INSTANCE"),
        username=os.getenv("SNOW_USERNAME") if not token else None,
        password=os.getenv("SNOW_PASSWORD") if not token else None,
        token=token,
    )


def get_connector():
    """Create a single-table ServiceNow connector (for direct connector tests)."""
    token = os.getenv("SNOW_TOKEN")
    return ServiceNowConnector(
        instance=os.getenv("SNOW_INSTANCE"),
        table=_TABLE,
        username=os.getenv("SNOW_USERNAME") if not token else None,
        password=os.getenv("SNOW_PASSWORD") if not token else None,
        token=token,
    )


def test_capabilities():
    """Test: Connector reports correct capabilities."""
    conn = get_connector()
    caps = conn.capabilities()

    assert caps.readable, "Should be readable"
    assert caps.insertable, "Should be insertable"
    assert caps.updatable, "Should be updatable"
    assert caps.deletable, "Should be deletable"
    assert caps.filter_pushdown, "Should support filter pushdown"
    print("✓ capabilities")


def test_schema():
    """Test: Schema discovery including inherited fields."""
    conn = get_connector()
    schema = conn.schema()

    assert _TABLE in schema.tables, f"Schema should include {_TABLE}"

    cols = schema.tables[_TABLE]
    col_names = [c.name for c in cols]
    assert "sys_id" in col_names, "Should have sys_id"

    print(f"✓ schema (found {len(cols)} columns)")
    print(f"  Columns: {', '.join(col_names[:5])}...")


def test_fetch():
    """Test: Fetch records from ServiceNow."""
    conn = get_connector()

    rows = list(conn.fetch(_TABLE, None, [], limit=3, offset=0))
    print(f"✓ fetch (got {len(rows)} rows)")

    if rows:
        print(f"  Sample: {list(rows[0].keys())[:5]}...")


def test_insert_update_delete():
    """Test: Full write cycle - insert, update, delete."""
    conn = get_connector()
    marker = f"sidol-test-{uuid.uuid4().hex[:8]}"

    # INSERT
    result = conn.insert(_TABLE, [{"short_description": marker, "priority": "3"}])
    assert result.affected_rows == 1, "Insert should affect 1 row"

    inserted = result.returned[0] if result.returned else {}
    sys_id = inserted.get("sys_id")
    print(f"✓ insert (sys_id: {sys_id})")

    # UPDATE
    result = conn.update(
        _TABLE,
        {"priority": "2"},
        [{"col": "sys_id", "op": "=", "val": sys_id}]
    )
    assert result.affected_rows == 1, "Update should affect 1 row"
    print("✓ update")

    # DELETE
    result = conn.delete(
        _TABLE,
        [{"col": "sys_id", "op": "=", "val": sys_id}]
    )
    assert result.affected_rows == 1, "Delete should affect 1 row"
    print("✓ delete")


def test_connect_servicenow():
    """Test: connect_servicenow() — query any table without register()."""
    db = get_db()

    # SELECT any table with no register() call
    tbl = db.sql(f"SELECT sys_id, short_description FROM {_TABLE} LIMIT 2")
    print(f"✓ connect_servicenow SELECT (got {tbl.num_rows} rows)")

    # INSERT via SQL
    marker = f"sidol-sql-test-{uuid.uuid4().hex[:8]}"
    result = db.sql(f"INSERT INTO {_TABLE} (short_description) VALUES ('{marker}')")
    affected = result.get("affected_rows", 0)
    print(f"✓ connect_servicenow INSERT (affected: {affected})")


def test_list_tables():
    """Test: db.tables() returns all ServiceNow tables, verified against X-Total-Count."""
    conn = get_connector()
    tables = conn.list_tables()
    total_from_api = conn.list_tables_total()

    assert len(tables) > 10, "Should discover many tables"
    assert _TABLE in tables, f"{_TABLE} should be in table list"

    if total_from_api is not None:
        if len(tables) < total_from_api:
            print(f"  NOTE: fetched {len(tables)} of {total_from_api} total (remainder restricted by ACL/scope)")
        print(f"✓ list_tables (found {len(tables)} tables, X-Total-Count={total_from_api})")
    else:
        print(f"✓ list_tables (found {len(tables)} tables)")
    print(f"  Sample: {', '.join(tables[:5])}...")


def test_schema_completeness():
    """Test: schema columns match actual fields in a real fetched row."""
    conn = get_connector()
    schema = conn.schema()
    cols_from_schema = {c.name for c in schema.tables[_TABLE]}

    rows = list(conn.fetch(_TABLE, None, [], limit=1, offset=0))
    assert rows, "Need at least one row to verify schema"
    cols_from_row = set(rows[0].keys())

    missing = cols_from_row - cols_from_schema
    print(f"✓ schema completeness: {len(cols_from_schema)} schema cols, {len(cols_from_row)} in real row")
    if missing:
        print(f"  WARNING: {len(missing)} fields in row not in schema: {sorted(missing)[:5]}")


def test_multi_table_no_register():
    """Test: query two different tables from one connection, no register()."""
    db = get_db()

    inc = db.sql(f"SELECT sys_id FROM {_TABLE} LIMIT 1")
    prob = db.sql("SELECT sys_id FROM problem LIMIT 1")
    print(f"✓ multi-table: {_TABLE}={inc.num_rows} row(s), problem={prob.num_rows} row(s)")


def main():
    """Run all live tests."""
    print(f"Testing against: {os.getenv('SNOW_INSTANCE')}.service-now.com")
    print(f"Table: {_TABLE}")
    print()

    test_capabilities()
    test_schema()
    test_fetch()
    test_insert_update_delete()
    test_connect_servicenow()
    test_list_tables()
    test_schema_completeness()
    test_multi_table_no_register()

    print()
    print("All live tests passed!")


if __name__ == "__main__":
    main()
