"""Airtable connector for Sidol."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from sidol.connectors import airtable_utils as utils
from sidol.connectors.base import BaseConnector
from sidol.context import ConnectorContext
from sidol.errors import ConnectorError, WriteError
from sidol.types import Capabilities, Column, Schema, WriteResult


class AirtableConnector(BaseConnector):
    """Full CRUD connector for Airtable.

    Usage:
        conn = AirtableConnector(base_id="app...", token="pat...")
        # Optional: specify table for single-table mode
        conn = AirtableConnector(base_id="app...", table="Tasks", token="pat...")
    """

    def __init__(
        self,
        base_id: str,
        token: str,
        table: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ):
        self.base_id = base_id
        self.table = table
        self._token = token
        self._timeout = timeout
        self._owns_client = client is None

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.client = client or httpx.Client(headers=headers, timeout=timeout)

    def _url(self, table: str) -> str:
        return f"https://api.airtable.com/v0/{self.base_id}/{table}"

    def schema(self) -> Schema:
        """Return schema by inferring from the first page of records.
        If no specific table is set, lists all tables via Metadata API first.
        """
        # 1. Identify which tables to describe
        target_tables = [self.table] if self.table else []
        
        if not target_tables:
            # Fetch all table names from Metadata API
            try:
                url = f"https://api.airtable.com/v0/meta/bases/{self.base_id}/tables"
                resp = self.client.get(url)
                if resp.status_code == 200:
                    target_tables = [t["name"] for t in resp.json().get("tables", [])]
            except Exception:
                pass

        if not target_tables:
            return Schema(tables={})

        # 2. Build schema for each table (first row inference)
        result_tables = {}
        for table_name in target_tables:
            try:
                resp = self.client.get(self._url(table_name), params={"maxRecords": 1})
                if resp.status_code != 200:
                    continue
                
                records = resp.json().get("records", [])
                if not records:
                    result_tables[table_name] = [Column(name="id", type="text", primary_key=True)]
                    continue

                flat = utils.flatten_record(records[0])
                cols = []
                for name, val in flat.items():
                    dtype = "text"
                    if isinstance(val, bool): dtype = "bool"
                    elif isinstance(val, (int, float)): dtype = "float"
                    cols.append(Column(name=name, type=dtype, primary_key=(name == "id")))
                result_tables[table_name] = cols
            except Exception:
                continue

        return Schema(tables=result_tables)

    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
        context: ConnectorContext | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows from Airtable using pagination and filterByFormula."""
        yielded = 0
        airtable_offset = None  # Airtable's pagination token

        while limit is None or yielded < limit:
            params: dict[str, Any] = {}
            if columns:
                # Airtable uses 'fields[]' repeated params. 
                # 'id' is a top-level property, not a field, so we must exclude it from this param.
                real_cols = [c for c in columns if c != "id"]
                if real_cols:
                    params["fields"] = real_cols
            formula = utils.build_formula(filters)
            if formula:
                params["filterByFormula"] = formula
            if airtable_offset:
                params["offset"] = airtable_offset
            if limit:
                params["maxRecords"] = limit

            resp = self.client.get(self._url(table), params=params)
            if resp.status_code >= 400:
                raise ConnectorError(f"Airtable fetch failed: {utils.airtable_error_detail(resp)}")

            data = resp.json()
            records = data.get("records", [])
            if not records:
                break

            for rec in records:
                if limit is not None and yielded >= limit:
                    return
                flat = utils.flatten_record(rec)
                # Ensure all values are SQL-friendly (scalars or JSON strings)
                clean_row = {}
                for k, v in flat.items():
                    if isinstance(v, (list, dict)):
                        clean_row[k] = json.dumps(v)
                    else:
                        clean_row[k] = v
                yield clean_row
                yielded += 1

            airtable_offset = data.get("offset")
            if not airtable_offset:
                break

    def insert(self, table: str, rows: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Insert records in chunks of 10."""
        if not rows:
            return WriteResult(affected_rows=0)

        results = []
        # Airtable limit: 10 records per request
        for i in range(0, len(rows), 10):
            chunk = rows[i : i + 10]
            payload = {"records": [{"fields": row} for row in chunk]}
            resp = self.client.post(self._url(table), json=payload)
            if resp.status_code >= 400:
                raise WriteError(f"Airtable insert failed: {utils.airtable_error_detail(resp)}")

            data = resp.json()
            for rec in data.get("records", []):
                results.append(utils.flatten_record(rec))

        return WriteResult(affected_rows=len(results), returned=results)

    def update(self, table: str, values: dict[str, Any], filters: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Update records matching filters. Requires fetching sys_ids first."""
        # Airtable PATCH requires record IDs.
        # We fetch matching records to get their IDs.
        matches = list(self.fetch(table, ["id"], filters, limit=None, offset=None))
        if not matches:
            return WriteResult(affected_rows=0)

        ids = [m["id"] for m in matches if m.get("id")]
        results = []

        for i in range(0, len(ids), 10):
            chunk_ids = ids[i : i + 10]
            payload = {"records": [{"id": rid, "fields": values} for rid in chunk_ids]}
            resp = self.client.patch(self._url(table), json=payload)
            if resp.status_code >= 400:
                raise WriteError(f"Airtable update failed: {utils.airtable_error_detail(resp)}")

            data = resp.json()
            for rec in data.get("records", []):
                results.append(utils.flatten_record(rec))

        return WriteResult(affected_rows=len(results), returned=results)

    def delete(self, table: str, filters: list[dict[str, Any]], context: ConnectorContext | None = None) -> WriteResult:
        """Delete records matching filters."""
        matches = list(self.fetch(table, ["id"], filters, limit=None, offset=None))
        if not matches:
            return WriteResult(affected_rows=0)

        ids = [m["id"] for m in matches if m.get("id")]
        deleted_count = 0

        for i in range(0, len(ids), 10):
            chunk_ids = ids[i : i + 10]
            # DELETE expects records[] query params
            params = [("records[]", rid) for rid in chunk_ids]
            resp = self.client.delete(self._url(table), params=params)
            if resp.status_code >= 400:
                raise WriteError(f"Airtable delete failed: {utils.airtable_error_detail(resp)}")
            deleted_count += len(chunk_ids)

        return WriteResult(affected_rows=deleted_count)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=True,
            updatable=True,
            deletable=True,
            filter_pushdown=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
