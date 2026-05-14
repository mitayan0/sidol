"""ServiceNow REST connector for Sidol."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from sidol.cache import TTLCache
from sidol.connectors.base import BaseConnector
from sidol.errors import ConnectorError, WriteError
from sidol.types import Capabilities, Column, Schema, WriteResult

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


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten ServiceNow's {'value': ..., 'display_value': ...} field structure."""
    return {k: (v["value"] if isinstance(v, dict) else v) for k, v in row.items()}


def _escape_sysparm_value(val: str) -> str:
    """Escape ^ for Glide encoded query strings (^ is the AND delimiter)."""
    return val.replace("^", "^^")


def _sysparm_literal(val: Any) -> str:
    """Format a scalar for use in a sysparm_query atom (after column and operator)."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return _escape_sysparm_value(str(val))


def _sn_error_detail(response: httpx.Response) -> str:
    """Build a human-readable error line from a ServiceNow HTTP response."""
    parts: list[str] = [f"HTTP {response.status_code}"]
    cid = response.headers.get("x-snc-correlation-id") or response.headers.get("X-Correlation-ID")
    if cid:
        parts.append(f"correlation_id={cid}")
    if not response.content:
        return " ".join(parts)
    try:
        payload = response.json()
    except json.JSONDecodeError:
        preview = response.text[:400].replace("\n", " ")
        parts.append(f"body={preview!r}")
        return " ".join(parts)
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        msg = err.get("message")
        detail = err.get("detail")
        if msg:
            parts.append(f"message={msg!r}")
        if detail:
            parts.append(f"detail={detail!r}")
    else:
        preview = response.text[:400].replace("\n", " ")
        parts.append(f"body={preview!r}")
    return " ".join(parts)


def _filter_atom(col: str, op: str, val: Any) -> str | None:
    """One sysparm_query atom, or None to skip (unsupported)."""
    if op == "=" and val is None:
        return f"{col}ISEMPTY"
    if op == "!=" and val is None:
        return f"{col}ISNOTEMPTY"
    if op == "IN":
        if not isinstance(val, list):
            return None
        if not val:
            return None
        inner = ",".join(_sysparm_literal(v) for v in val)
        return f"{col}IN{inner}"
    if op == "LIKE":
        return f"{col}LIKE{_sysparm_literal(val)}"
    if op == "=":
        return f"{col}={_sysparm_literal(val)}"
    if op == "!=":
        return f"{col}!={_sysparm_literal(val)}"
    if op == ">":
        return f"{col}>{_sysparm_literal(val)}"
    if op == ">=":
        return f"{col}>={_sysparm_literal(val)}"
    if op == "<":
        return f"{col}<{_sysparm_literal(val)}"
    if op == "<=":
        return f"{col}<={_sysparm_literal(val)}"
    return None


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
        self._cache = TTLCache(default_ttl=300)  # 5 min schema cache
        self._sysparm_display_value = sysparm_display_value

        self._oauth_client_id = oauth_client_id
        self._oauth_client_secret = oauth_client_secret
        self._oauth_refresh_token = oauth_refresh_token
        self._oauth_access_token = oauth_access_token

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        oauth_ready = self._oauth_use_refresh_flow()
        use_basic = bool(
            username
            and password
            and not token
            and not oauth_ready
        )

        self._owns_client = client is None
        if client is not None:
            self.client = client
        else:
            if use_basic and username is not None and password is not None:
                self.client = httpx.Client(headers=headers, auth=(username, password), timeout=timeout)
            else:
                self.client = httpx.Client(headers=headers, auth=None, timeout=timeout)

        if oauth_ready:
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
                f"ServiceNow OAuth token refresh failed: {_sn_error_detail(response)}"
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
        detail = _sn_error_detail(response)
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
        """Yield rows from ServiceNow Table API.

        ``columns`` may include dot-walked fields (e.g. ``caller_id.name``) for Table API
        ``sysparm_fields``. When ``sysparm_display_value`` was True on the connector,
        ``sysparm_display_value=all`` is sent so reference display values are populated.
        """
        yielded = 0
        next_offset = offset or 0
        cap = limit

        while True:
            if cap is not None and yielded >= cap:
                return

            chunk = self.page_size
            if cap is not None:
                remaining = cap - yielded
                if remaining <= 0:
                    return
                chunk = min(self.page_size, remaining)

            params: dict[str, Any] = {
                "sysparm_limit": chunk,
                "sysparm_offset": next_offset,
            }
            if columns:
                params["sysparm_fields"] = ",".join(columns)
            if filters:
                params["sysparm_query"] = self._build_query(filters)
            if self._sysparm_display_value:
                params["sysparm_display_value"] = "all"

            rows = self._get_page(params, table)
            if not rows:
                return

            for row in rows:
                if cap is not None and yielded >= cap:
                    return
                yield _flatten_row(row)
                yielded += 1

            if len(rows) < chunk:
                return
            next_offset += len(rows)

    def insert(self, table: str, rows: list[dict[str, Any]]) -> WriteResult:
        """Insert rows via ServiceNow POST."""
        results = []
        for row in rows:
            resp = self._request("POST", self._table_url(table), json=row)
            payload = self._json_payload(resp, f"INSERT {table}", is_write=True)
            results.append(_flatten_row(payload.get("result", {})))
        return WriteResult(affected_rows=len(results), returned=results)

    def update(self, table: str, values: dict[str, Any], filters: list[dict[str, Any]]) -> WriteResult:
        """Update rows via ServiceNow PATCH."""
        sys_ids = self._resolve_sys_ids(filters, table)
        results = []
        for sys_id in sys_ids:
            resp = self._request("PATCH", f"{self._table_url(table)}/{sys_id}", json=values)
            payload = self._json_payload(resp, f"UPDATE {table}", is_write=True)
            results.append(_flatten_row(payload.get("result", {})))
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
            atom = _filter_atom(col, op, val)
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
