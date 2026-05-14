"""Pure helper functions for the ServiceNow connector."""

from __future__ import annotations

import json
from typing import Any

import httpx


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten ServiceNow's {'value': ..., 'display_value': ...} field structure."""
    return {k: (v["value"] if isinstance(v, dict) else v) for k, v in row.items()}


def escape_sysparm_value(val: str) -> str:
    """Escape ^ for Glide encoded query strings (^ is the AND delimiter)."""
    return val.replace("^", "^^")


def sysparm_literal(val: Any) -> str:
    """Format a scalar for use in a sysparm_query atom (after column and operator)."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    return escape_sysparm_value(str(val))


def filter_atom(col: str, op: str, val: Any) -> str | None:
    """One sysparm_query atom, or None to skip (unsupported)."""
    if op == "=" and val is None:
        return f"{col}ISEMPTY"
    if op == "!=" and val is None:
        return f"{col}ISNOTEMPTY"
    if op == "IN":
        if not isinstance(val, list) or not val:
            return None
        inner = ",".join(sysparm_literal(v) for v in val)
        return f"{col}IN{inner}"
    if op == "LIKE":
        return f"{col}LIKE{sysparm_literal(val)}"
    if op == "=":
        return f"{col}={sysparm_literal(val)}"
    if op == "!=":
        return f"{col}!={sysparm_literal(val)}"
    if op == ">":
        return f"{col}>{sysparm_literal(val)}"
    if op == ">=":
        return f"{col}>={sysparm_literal(val)}"
    if op == "<":
        return f"{col}<{sysparm_literal(val)}"
    if op == "<=":
        return f"{col}<={sysparm_literal(val)}"
    return None


def sn_error_detail(response: httpx.Response) -> str:
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
