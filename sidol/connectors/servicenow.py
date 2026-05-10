"""ServiceNow REST connector for Sidol."""

from __future__ import annotations

from typing import Any, Iterator

import httpx

from sidol.cache import TTLCache
from sidol.connectors.base import BaseConnector
from sidol.errors import ConnectorError, CapabilityError
from sidol.types import Column, Schema, Capabilities, WriteResult


# ServiceNow Table API -> sidol type mapping
_SNOW_TYPE_MAP = {
    "string": "text",
    "integer": "int",
    "boolean": "bool",
    "glide_date_time": "timestamp",
    "float": "float",
    "reference": "text",  # sys_id reference
}


def _flatten_row(row: dict) -> dict:
    """Flatten ServiceNow's {'value': ..., 'display_value': ...} field structure."""
    return {k: (v["value"] if isinstance(v, dict) else v) for k, v in row.items()}


class ServiceNowConnector(BaseConnector):
    """Full CRUD connector for ServiceNow Table API.
    
    Usage:
        conn = ServiceNowConnector(
            instance="mycompany",
            table="incident",
            username="admin",
            password="secret",
        )
    """

    def __init__(
        self,
        instance: str,
        table: str,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        page_size: int = 1000,
    ):
        self.base_url = f"https://{instance}.service-now.com/api/now/table/{table}"
        self.table = table
        self.page_size = page_size
        self._cache = TTLCache(default_ttl=300)  # 5 min schema cache
        
        self._owns_client = client is None
        if client is not None:
            self.client = client
        else:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            auth = (username, password) if username and password and not token else None
            self.client = httpx.Client(headers=headers, auth=auth, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=True,
            updatable=True,
            deletable=True,
            filter_pushdown=True,
        )

    def schema(self) -> Schema:
        """Return schema from ServiceNow sys_dictionary table."""
        cached = self._cache.get("schema")
        if cached:
            return cached
        
        # Get metadata from sys_dictionary
        instance_url = self.base_url.split("/api/now/table/")[0]
        resp = self.client.get(
            f"{instance_url}/api/now/table/sys_dictionary",
            params={
                "sysparm_query": f"name={self.table}",
                "sysparm_fields": "element,internal_type,mandatory,read_only",
                "sysparm_limit": 500,
            }
        )
        data = self._result(resp)
        rows = data if isinstance(data, list) else []
        
        cols = []
        for row in rows:
            if not row.get("element"):
                continue
            snow_type = row.get("internal_type", {})
            if isinstance(snow_type, dict):
                snow_type = snow_type.get("value", "string")
            cols.append(Column(
                name=row["element"],
                type=_SNOW_TYPE_MAP.get(snow_type, "text"),
                nullable=not row.get("mandatory"),
                primary_key=(row["element"] == "sys_id"),
            ))
        
        schema = Schema(tables={self.table: cols})
        self._cache.set("schema", schema)
        return schema

    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict],
        limit: int | None,
        offset: int | None,
    ) -> Iterator[dict]:
        """Yield rows from ServiceNow Table API."""
        params: dict[str, Any] = {
            "sysparm_limit": min(limit or self.page_size, self.page_size),
            "sysparm_offset": offset or 0,
        }
        if columns:
            params["sysparm_fields"] = ",".join(columns)
        if filters:
            params["sysparm_query"] = self._build_query(filters)

        while True:
            rows = self._get_page(params)
            if not rows:
                break
            for row in rows:
                yield _flatten_row(row)
            if limit and params["sysparm_offset"] + self.page_size >= limit:
                break
            params["sysparm_offset"] += self.page_size
            if len(rows) < self.page_size:
                break

    def insert(self, table: str, rows: list[dict]) -> WriteResult:
        """Insert rows via ServiceNow POST."""
        results = []
        for row in rows:
            resp = self.client.post(self.base_url, json=row)
            resp.raise_for_status()
            results.append(_flatten_row(resp.json().get("result", {})))
        return WriteResult(affected_rows=len(results), returned=results)

    def update(self, table: str, values: dict, filters: list[dict]) -> WriteResult:
        """Update rows via ServiceNow PATCH."""
        sys_ids = self._resolve_sys_ids(filters)
        results = []
        for sys_id in sys_ids:
            resp = self.client.patch(f"{self.base_url}/{sys_id}", json=values)
            resp.raise_for_status()
            results.append(_flatten_row(resp.json().get("result", {})))
        return WriteResult(affected_rows=len(results), returned=results)

    def _get_page(self, params: dict) -> list[dict]:
        """Fetch one page from the ServiceNow Table API."""
        data = self._result(self.client.get(self.base_url, params=params))
        if isinstance(data, list):
            return data
        return [data] if data else []

    def delete(self, table: str, filters: list[dict]) -> WriteResult:
        """Delete rows via ServiceNow DELETE."""
        sys_ids = self._resolve_sys_ids(filters)
        for sys_id in sys_ids:
            resp = self.client.delete(f"{self.base_url}/{sys_id}")
            resp.raise_for_status()
        return WriteResult(affected_rows=len(sys_ids))

    def _build_query(self, filters: list[dict]) -> str:
        """Convert sidol filter dicts to ServiceNow sysparm_query string."""
        parts = []
        for f in filters:
            if "raw" in f:
                continue
            col, op, val = f["col"], f["op"], f["val"]
            if op == "=":
                parts.append(f"{col}={val}")
            elif op == "!=":
                parts.append(f"{col}!={val}")
            elif op == ">":
                parts.append(f"{col}>{val}")
            elif op == "<":
                parts.append(f"{col}<{val}")
            elif op == "LIKE":
                parts.append(f"{col}LIKE{val}")
            elif op == "IN":
                parts.append(f"{col}IN{','.join(str(v) for v in val)}")
        return "^".join(parts)

    def _resolve_sys_ids(self, filters: list[dict]) -> list[str]:
        """Get sys_ids for UPDATE/DELETE. Fetch if not directly specified."""
        for f in filters:
            if f.get("col") == "sys_id" and f.get("op") == "=":
                return [f["val"]]
        
        # Need to fetch matching records first
        rows = list(self.fetch(self.table, ["sys_id"], filters, limit=None, offset=None))
        return [r["sys_id"] for r in rows if r.get("sys_id")]

    def _result(self, response: httpx.Response) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ConnectorError(str(exc)) from exc
        payload = response.json() if response.content else {}
        return payload.get("result", payload)
