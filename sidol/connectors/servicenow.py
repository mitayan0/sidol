import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from sidol.connectors import servicenow_utils as sn_utils
from sidol.connectors.base import BaseConnector
from sidol.errors import ConnectorError, WriteError
from sidol.types import Capabilities, Column, Schema, WriteResult


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """Simple in-memory TTL cache for ServiceNow schema/metadata."""

    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        duration = ttl if ttl is not None else self.default_ttl
        self._store[key] = CacheEntry(value=value, expires_at=time.time() + duration)


# ServiceNow Table API -> sidol type mapping
_SNOW_TYPE_MAP = {
    "string": "text",
    "integer": "int",
    "boolean": "bool",
    "glide_date_time": "timestamp",
    "glide_date": "timestamp",
    "float": "float",
    "decimal": "float",
    "reference": "text",
    "guid": "text",
    "GUID": "text",
}


class ServiceNowConnector(BaseConnector):
    """Full CRUD connector for ServiceNow Table API.

    Single-table mode (backward compatible):
        conn = ServiceNowConnector(instance="mycompany", table="incident",
                                   username="admin", password="secret")

    Multi-table mode (use with Session.use()):
        conn = ServiceNowConnector(instance="mycompany",
                                   username="admin", password="secret")

    OAuth 2.0 (refresh token) optional:
        ServiceNowConnector(..., oauth_client_id="...", oauth_client_secret="...",
                            oauth_refresh_token="...")
    """

    def __init__(
        self,
        instance: str,
        table: str | None = None,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        page_size: int = 1000,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_refresh_token: str | None = None,
        oauth_access_token: str | None = None,
        sysparm_display_value: bool = False,
    ):
        self.instance_url = f"https://{instance}.service-now.com"
        self.table = table
        self.page_size = max(1, min(page_size, 10_000))
        self._timeout = timeout
        self._cache = TTLCache(default_ttl=300)
        self._sysparm_display_value = sysparm_display_value

        self._init_oauth(oauth_client_id, oauth_client_secret, oauth_refresh_token, oauth_access_token)
        self._init_client(client, username, password, token, timeout)

    def _init_oauth(self, cid: str | None, secret: str | None, refresh: str | None, access: str | None) -> None:
        """Setup OAuth credentials."""
        self._oauth_client_id = cid
        self._oauth_client_secret = secret
        self._oauth_refresh_token = refresh
        self._oauth_access_token = access

    def _init_client(
        self, client: httpx.Client | None, user: str | None, pwd: str | None, token: str | None, timeout: float
    ) -> None:
        """Setup HTTP client with appropriate auth."""
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        self._owns_client = client is None

        if client is not None:
            self.client = client
            return

        auth = (user, pwd) if (user and pwd and not token and not self._oauth_use_refresh_flow()) else None
        self.client = httpx.Client(headers=headers, auth=auth, timeout=timeout)

        if self._oauth_use_refresh_flow():
            self._ensure_initial_oauth_access_token()
            self.client.headers["Authorization"] = f"Bearer {self._oauth_access_token}"
        elif token:
            self.client.headers["Authorization"] = f"Bearer {token}"

    def _oauth_use_refresh_flow(self) -> bool:
        return bool(
            self._oauth_client_id
            and self._oauth_client_secret
            and (self._oauth_refresh_token or self._oauth_access_token)
        )

    def _oauth_can_refresh(self) -> bool:
        return bool(
            self._oauth_client_id
            and self._oauth_client_secret
            and self._oauth_refresh_token
        )

    def _ensure_initial_oauth_access_token(self) -> None:
        if self._oauth_access_token:
            return
        if not self._oauth_refresh_token:
            raise ConnectorError(
                "OAuth is configured with client id/secret but neither oauth_access_token "
                "nor oauth_refresh_token was provided."
            )
        self._exchange_oauth_refresh_token()

    def _exchange_oauth_refresh_token(self) -> None:
        """POST to oauth_token.do via self.client so tests can mock transport."""
        if not self._oauth_can_refresh():
            raise ConnectorError("OAuth refresh called without client id/secret/refresh_token.")
        url = f"{self.instance_url}/oauth_token.do"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._oauth_refresh_token,
            "client_id": self._oauth_client_id,
            "client_secret": self._oauth_client_secret,
        }
        response = self.client.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ConnectorError(
                f"ServiceNow OAuth token refresh failed: {sn_utils.sn_error_detail(response)}"
            ) from None
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ConnectorError(
                f"ServiceNow OAuth token response was not JSON: {response.text[:200]!r}"
            ) from exc
        access = payload.get("access_token")
        if not access or not isinstance(access, str):
            raise ConnectorError("ServiceNow OAuth response missing access_token.")
        self._oauth_access_token = access
        new_refresh = payload.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            self._oauth_refresh_token = new_refresh
        self.client.headers["Authorization"] = f"Bearer {self._oauth_access_token}"

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, url, **kwargs)
        if response.status_code == 401 and self._oauth_can_refresh():
            self._exchange_oauth_refresh_token()
            response = self.client.request(method, url, **kwargs)
        return response

    def _require_ok(self, response: httpx.Response, operation: str, *, is_write: bool) -> None:
        if response.status_code < 400:
            return
        detail = sn_utils.sn_error_detail(response)
        msg = f"ServiceNow {operation} failed: {detail}"
        if is_write:
            raise WriteError(msg) from None
        raise ConnectorError(msg) from None

    def _json_payload(self, response: httpx.Response, operation: str, *, is_write: bool) -> Any:
        self._require_ok(response, operation, is_write=is_write)
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            preview = response.text[:200]
            msg = f"ServiceNow returned non-JSON ({response.status_code}): {preview!r}"
            if is_write:
                raise WriteError(msg) from exc
            raise ConnectorError(msg) from exc

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _table_url(self, table: str) -> str:
        return f"{self.instance_url}/api/now/table/{table}"

    def list_tables(self) -> list[str]:
        """Return all table names from ServiceNow sys_db_object."""
        page_size = 1000
        offset = 0
        names: list[str] = []
        total: int | None = None
        while True:
            resp = self._request(
                "GET",
                self._table_url("sys_db_object"),
                params={
                    "sysparm_query": "nameISNOTEMPTY^ORDERBYname",
                    "sysparm_fields": "name",
                    "sysparm_limit": page_size,
                    "sysparm_offset": offset,
                },
            )
            if total is None:
                raw = resp.headers.get("X-Total-Count")
                total = int(raw) if raw else None
            rows = self._table_api_result(resp, "list_tables(sys_db_object)", is_write=False)
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                name = r.get("name")
                if isinstance(name, dict):
                    name = name.get("value") or name.get("display_value")
                if name:
                    names.append(str(name))
            offset += page_size
            if total is not None and offset >= total:
                break
        self._cache.set("list_tables_total", total)
        return names

    def list_tables_total(self) -> int | None:
        """Return the total table count reported by ServiceNow (from last list_tables call)."""
        return self._cache.get("list_tables_total")

    def capabilities(self) -> Capabilities:
        return Capabilities(
            readable=True,
            insertable=True,
            updatable=True,
            deletable=True,
            filter_pushdown=True,
        )

    def schema(self) -> Schema:
        """Return schema from ServiceNow UI Metadata API (includes inherited fields)."""
        table = self.table
        if not table:
            return Schema(tables={})

        cache_key = f"schema:{table}"
        cached: Schema | None = self._cache.get(cache_key)
        if cached:
            return cached

        resp = self._request("GET", f"{self.instance_url}/api/now/ui/meta/{table}")
        data = self._table_api_result(resp, f"schema(ui/meta/{table})", is_write=False)
        columns = data.get("columns", {}) if isinstance(data, dict) else {}

        cols = []
        for name, col in columns.items():
            if not name:
                continue
            snow_type = col.get("type", "string")
            cols.append(
                Column(
                    name=name,
                    type=_SNOW_TYPE_MAP.get(snow_type, "text"),
                    nullable=not col.get("mandatory", False),
                    primary_key=(name == "sys_id"),
                )
            )

        schema = Schema(tables={table: cols})
        self._cache.set(cache_key, schema)
        return schema

    def fetch(
        self,
        table: str,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        offset: int | None,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows from ServiceNow Table API."""
        yielded = 0
        current_offset = offset or 0

        while limit is None or yielded < limit:
            params = self._build_fetch_params(columns, filters, limit, yielded, current_offset)
            rows = self._get_page(params, table)
            if not rows:
                break

            for row in rows:
                if limit is not None and yielded >= limit:
                    return
                yield sn_utils.flatten_row(row)
                yielded += 1

            if len(rows) < params["sysparm_limit"]:
                break
            current_offset += len(rows)

    def _build_fetch_params(
        self,
        columns: list[str] | None,
        filters: list[dict[str, Any]],
        limit: int | None,
        yielded: int,
        offset: int,
    ) -> dict[str, Any]:
        """Build sysparm_* query parameters for a single page fetch."""
        chunk = self.page_size
        if limit is not None:
            chunk = min(self.page_size, limit - yielded)

        params: dict[str, Any] = {"sysparm_limit": chunk, "sysparm_offset": offset}
        if columns:
            params["sysparm_fields"] = ",".join(columns)
        if filters:
            params["sysparm_query"] = self._build_query(filters)
        if self._sysparm_display_value:
            params["sysparm_display_value"] = "all"
        return params

    def insert(self, table: str, rows: list[dict[str, Any]]) -> WriteResult:
        """Insert rows via ServiceNow POST."""
        results = []
        for row in rows:
            resp = self._request("POST", self._table_url(table), json=row)
            payload = self._json_payload(resp, f"INSERT {table}", is_write=True)
            results.append(sn_utils.flatten_row(payload.get("result", {})))
        return WriteResult(affected_rows=len(results), returned=results)

    def update(self, table: str, values: dict[str, Any], filters: list[dict[str, Any]]) -> WriteResult:
        """Update rows via ServiceNow PATCH."""
        sys_ids = self._resolve_sys_ids(filters, table)
        results = []
        for sys_id in sys_ids:
            resp = self._request("PATCH", f"{self._table_url(table)}/{sys_id}", json=values)
            payload = self._json_payload(resp, f"UPDATE {table}", is_write=True)
            results.append(sn_utils.flatten_row(payload.get("result", {})))
        return WriteResult(affected_rows=len(results), returned=results)

    def _get_page(self, params: dict[str, Any], table: str) -> list[dict[str, Any]]:
        """Fetch one page from the ServiceNow Table API."""
        response = self._request("GET", self._table_url(table), params=params)
        data = self._table_api_result(response, f"GET {table}", is_write=False)
        if isinstance(data, list):
            return data
        return [data] if data else []

    def delete(self, table: str, filters: list[dict[str, Any]]) -> WriteResult:
        """Delete rows via ServiceNow DELETE."""
        sys_ids = self._resolve_sys_ids(filters, table)
        for sys_id in sys_ids:
            resp = self._request("DELETE", f"{self._table_url(table)}/{sys_id}")
            self._require_ok(resp, f"DELETE {table}", is_write=True)
        return WriteResult(affected_rows=len(sys_ids))

    def _build_query(self, filters: list[dict[str, Any]]) -> str:
        """Convert sidol filter dicts to ServiceNow sysparm_query string."""
        parts: list[str] = []
        for f in filters:
            if "raw" in f:
                continue
            col, op, val = f["col"], f["op"], f["val"]
            atom = sn_utils.filter_atom(col, op, val)
            if atom:
                parts.append(atom)
        return "^".join(parts)

    def _resolve_sys_ids(self, filters: list[dict[str, Any]], table: str) -> list[str]:
        """Get sys_ids for UPDATE/DELETE. Fetch if not directly specified."""
        for f in filters:
            if f.get("col") == "sys_id" and f.get("op") == "=":
                return [str(f["val"])]

        rows = list(self.fetch(table, ["sys_id"], filters, limit=None, offset=None))
        return [str(r["sys_id"]) for r in rows if r.get("sys_id")]

    def _table_api_result(self, response: httpx.Response, operation: str, *, is_write: bool) -> Any:
        payload = self._json_payload(response, operation, is_write=is_write)
        if isinstance(payload, dict):
            return payload.get("result", payload)
        return payload
