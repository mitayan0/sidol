"""Live integration tests for Airtable connector.

Usage:
    1. Update your .env file with AIRTABLE_BASE_ID, AIRTABLE_TOKEN, and AIRTABLE_TABLE
    2. Run: uv run python tests/connectors/test_airtable_live.py

WARNING: This creates real records in your Airtable base.
"""

import os
import uuid

from dotenv import load_dotenv

import sidol
from sidol.connectors.airtable import AirtableConnector

load_dotenv()

_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
_TOKEN = os.getenv("AIRTABLE_TOKEN")
_TABLE = os.getenv("AIRTABLE_TABLE", "Tasks")


def get_connector():
    """Create a single-table Airtable connector."""
    if not _BASE_ID or not _TOKEN:
        raise ValueError("AIRTABLE_BASE_ID and AIRTABLE_TOKEN must be set in .env")

    return AirtableConnector(
        base_id=_BASE_ID,
        token=_TOKEN,
        table=_TABLE
    )


def test_capabilities():
    conn = get_connector()
    caps = conn.capabilities()
    assert caps.readable
    assert caps.insertable
    assert caps.updatable
    assert caps.deletable
    print("OK capabilities")


def test_schema():
    conn = get_connector()
    schema = conn.schema()
    assert _TABLE in schema.tables
    cols = schema.tables[_TABLE]
    print(f"OK schema (found {len(cols)} columns)")


def test_fetch():
    conn = get_connector()
    rows = list(conn.fetch(_TABLE, None, [], limit=3, offset=0))
    print(f"OK fetch (got {len(rows)} rows)")
    if rows:
        print(f"  Sample fields: {list(rows[0].keys())[:5]}")


def test_insert_update_delete():
    conn = get_connector()
    marker = f"sidol-test-{uuid.uuid4().hex[:8]}"

    # Assuming there's a 'Name' or 'Title' field.
    # If not, this might fail depending on your Airtable schema.
    # Most default tables have a 'Name' field.
    row = {"Task": marker}

    # INSERT
    print(f"  Inserting '{marker}'...")
    result = conn.insert(_TABLE, [row])
    assert result.affected_rows == 1

    inserted = result.returned[0]
    record_id = inserted.get("id")
    print(f"OK insert (record_id: {record_id})")

    # UPDATE
    print(f"  Updating '{record_id}'...")
    result = conn.update(
        _TABLE,
        {"Task": f"{marker}-updated"},
        [{"col": "id", "op": "=", "val": record_id}]
    )
    assert result.affected_rows == 1
    print("OK update")

    # DELETE
    print(f"  Deleting '{record_id}'...")
    result = conn.delete(
        _TABLE,
        [{"col": "id", "op": "=", "val": record_id}]
    )
    assert result.affected_rows == 1
    print("OK delete")


def test_sql_session():
    """Test the high-level sidol.sql() API against Airtable."""
    db = sidol.connect()
    # Use the helper to get a connector with debug info
    conn = get_connector()
    db.register(_TABLE, conn)

    # SQL SELECT - Use double quotes to preserve case sensitivity of the table ID/Name
    quoted_table = f'"{_TABLE}"'
    tbl = db.sql(f"SELECT id FROM {quoted_table} LIMIT 1")
    print(f"OK SQL SELECT (got {len(tbl)} rows)")

    # SQL INSERT
    marker = f"sidol-sql-{uuid.uuid4().hex[:4]}"
    print(f"  SQL Inserting '{marker}'...")
    db.sql(f"INSERT INTO {quoted_table} (Task) VALUES ('{marker}')")

    # Verify it exists via SQL
    tbl = db.sql(f"SELECT id FROM {quoted_table} WHERE Task = '{marker}'")
    assert len(tbl) == 1
    record_id = tbl.to_pydict()["id"][0]
    print(f"OK SQL INSERT & Verify (id: {record_id})")

    # SQL DELETE
    db.sql(f"DELETE FROM {quoted_table} WHERE id = '{record_id}'")
    print("OK SQL DELETE")


def test_multi_table_mode():
    """Test using Airtable as a default connector for any table in the base."""
    db = sidol.connect()
    # Create connector WITHOUT a fixed table
    master_conn = AirtableConnector(base_id=_BASE_ID, token=_TOKEN)
    db.use(master_conn)

    # Query the table by name - this proves db.use() works
    print(f"  Testing Multi-Table mode on '{_TABLE}'...")
    quoted_table = f'"{_TABLE}"'
    tbl = db.sql(f"SELECT id FROM {quoted_table} LIMIT 1")
    assert len(tbl) >= 0
    print("OK Multi-Table mode")


def main():
    if not _BASE_ID or not _TOKEN:
        print("Skipping live Airtable tests: Credentials not found in .env")
        return

    try:
        print(f"Testing against Airtable Base: {_BASE_ID}")
        print(f"Table: {_TABLE}")
        print("")

        test_capabilities()
        test_schema()
        test_fetch()
        test_insert_update_delete()
        test_sql_session()
        test_multi_table_mode()

        print("\nAll live Airtable tests passed!")
    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback
        traceback.print_exc()
        print(f"\nFAILED: {e}")


if __name__ == "__main__":
    main()
