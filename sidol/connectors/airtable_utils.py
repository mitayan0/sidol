"""Pure helper functions for the Airtable connector."""

from __future__ import annotations

import json
from typing import Any

import httpx


def flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten Airtable's {"id": "...", "fields": {...}} structure."""
    fields = record.get("fields", {})
    return {"id": record.get("id"), **fields, "createdTime": record.get("createdTime")}


def airtable_literal(val: Any) -> str:
    """Format a scalar for use in an Airtable formula."""
    if val is None:
        return "BLANK()"
    if isinstance(val, bool):
        return "TRUE()" if val else "FALSE()"
    if isinstance(val, (int, float)):
        return str(val)
    # Escape single quotes by doubling them (Airtable style)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def filter_atom(col: str, op: str, val: Any) -> str | None:
    """One Airtable formula atom, or None to skip."""
    # Airtable uses {Field Name} for column references
    field = f"{{{col}}}"
    literal = airtable_literal(val)

    if op == "=":
        if val is None:
            return f"{field} = BLANK()"
        return f"{field} = {literal}"
    if op == "!=":
        if val is None:
            return f"{field} != BLANK()"
        return f"{field} != {literal}"
    if op == ">":
        return f"{field} > {literal}"
    if op == ">=":
        return f"{field} >= {literal}"
    if op == "<":
        return f"{field} < {literal}"
    if op == "<=":
        return f"{field} <= {literal}"
    if op == "LIKE":
        # SEARCH returns position (1-based) or 0 if not found
        return f"SEARCH({literal}, {field}) > 0"
    return None


def build_formula(filters: list[dict[str, Any]]) -> str | None:
    """Convert sidol filter dicts to an Airtable filterByFormula string."""
    atoms = []
    for f in filters:
        if "raw" in f:
            continue
        atom = filter_atom(f["col"], f["op"], f["val"])
        if atom:
            atoms.append(atom)

    if not atoms:
        return None
    if len(atoms) == 1:
        return atoms[0]
    return f"AND({', '.join(atoms)})"


def airtable_error_detail(response: httpx.Response) -> str:
    """Build a human-readable error line from an Airtable HTTP response."""
    parts: list[str] = [f"HTTP {response.status_code}"]
    if not response.content:
        return " ".join(parts)
    try:
        payload = response.json()
        error = payload.get("error")
        if isinstance(error, dict):
            msg = error.get("message")
            etype = error.get("type")
            if etype:
                parts.append(f"type={etype}")
            if msg:
                parts.append(f"message={msg!r}")
        elif isinstance(error, str):
            parts.append(f"error={error!r}")
    except json.JSONDecodeError:
        preview = response.text[:200].replace("\n", " ")
        parts.append(f"body={preview!r}")
    return " ".join(parts)
