"""SQL router for Sidol.

This module parses SQL and extracts the key components needed for routing
to the appropriate connector. It uses sqlglot for parsing.
"""

from __future__ import annotations

from typing import Any, cast

import sqlglot
import sqlglot.expressions as exp

from sidol.errors import ParseError, SidolError, UnsupportedSQLError


def parse(sql: str) -> exp.Expression:
    """Parse SQL and return an AST expression.

    Raises:
        ParseError: If SQL cannot be parsed
    """
    try:
        return cast(exp.Expression, sqlglot.parse_one(sql))
    except sqlglot.errors.ParseError as e:
        raise ParseError(f"SQL parse error: {e}") from e


def statement_type(tree: exp.Expression) -> str:
    """Return the statement type: SELECT, INSERT, UPDATE, DELETE, CREATE.

    Raises:
        SidolError: If statement type is not supported
    """
    if isinstance(tree, exp.Select):
        return "SELECT"
    if isinstance(tree, exp.Insert):
        return "INSERT"
    if isinstance(tree, exp.Update):
        return "UPDATE"
    if isinstance(tree, exp.Delete):
        return "DELETE"
    if isinstance(tree, exp.Create):
        return "CREATE"
    raise SidolError(f"Unsupported statement type: {type(tree).__name__}")


def extract_table(tree: exp.Expression) -> str:
    """Extract the target table name from a SQL statement."""
    t = tree.find(exp.Table)
    if not t:
        raise SidolError("Could not find target table in SQL")
    return t.name


def _reject_unsupported_dml(tree: exp.Expression) -> None:
    """Raise UnsupportedSQLError for DML features not supported in Sidol v1."""
    if tree.args.get("with"):
        raise UnsupportedSQLError("CTE DML is not supported in Sidol v1")
    if tree.args.get("returning"):
        raise UnsupportedSQLError("RETURNING is not supported in Sidol v1")
    if list(tree.find_all(exp.Join)):
        raise UnsupportedSQLError("Multi-table DML and JOINs are not supported in Sidol v1")
    if list(tree.find_all(exp.Subquery)):
        raise UnsupportedSQLError("Subqueries are not supported in Sidol v1 DML")


def extract_insert_rows(tree: exp.Expression) -> list[dict[str, Any]]:
    """Extract column names and VALUES rows from an INSERT statement."""
    _reject_unsupported_dml(tree)

    target = tree.this
    if isinstance(target, exp.Schema):
        cols = [_identifier_name(c) for c in target.expressions]
    else:
        raise UnsupportedSQLError("INSERT must include an explicit column list: INSERT INTO t (col1, ...) VALUES (...)")

    values_node = tree.expression
    if not isinstance(values_node, exp.Values):
        raise UnsupportedSQLError("Only INSERT ... VALUES is supported in Sidol v1")

    rows: list[dict[str, Any]] = []
    for tuple_node in values_node.expressions:
        if not isinstance(tuple_node, exp.Tuple):
            raise UnsupportedSQLError("INSERT values must be literal tuples")
        vals = [_literal_value(v) for v in tuple_node.expressions]
        if len(vals) != len(cols):
            raise ParseError("INSERT column count does not match value count")
        rows.append(dict(zip(cols, vals, strict=False)))

    if not rows:
        raise ParseError("INSERT must include at least one VALUES row")
    return rows


def extract_update_set(tree: exp.Expression) -> dict[str, Any]:
    """Extract SET col=val pairs from an UPDATE statement."""
    _reject_unsupported_dml(tree)

    result: dict[str, Any] = {}
    for assignment in tree.expressions:
        if not isinstance(assignment, exp.EQ):
            raise UnsupportedSQLError("UPDATE SET only supports simple col = literal assignments")
        col = _column_name(assignment.left)
        result[col] = _literal_value(assignment.right)
    if not result:
        raise ParseError("UPDATE must include at least one SET assignment")
    return result


def extract_filters(tree: exp.Expression, require_where: bool = False) -> list[dict[str, Any]]:
    """Extract WHERE conditions as a list of filter dicts.

    Each filter dict has keys: col, op, val  (or 'raw' for complex expressions).
    If require_where=True, raises UnsupportedSQLError when WHERE is absent.
    """
    where = tree.find(exp.Where)
    if not where:
        if require_where:
            raise UnsupportedSQLError(
                "UPDATE and DELETE require a WHERE clause in Sidol v1 to prevent full-table mutations"
            )
        return []

    filters: list[dict[str, Any]] = []
    _walk_conditions(where.this, filters)
    return filters


def _walk_conditions(node: Any, out: list[dict[str, Any]]) -> None:
    """Recursively walk AND-joined WHERE conditions."""
    if isinstance(node, exp.And):
        _walk_conditions(node.left, out)
        _walk_conditions(node.right, out)
        return

    if isinstance(node, exp.Paren):
        _walk_conditions(node.this, out)
        return

    op_map: dict[type, str] = {
        exp.EQ: "=",
        exp.NEQ: "!=",
        exp.GT: ">",
        exp.GTE: ">=",
        exp.LT: "<",
        exp.LTE: "<=",
        exp.Like: "LIKE",
        exp.In: "IN",
    }
    for cls, op in op_map.items():
        if isinstance(node, cls):
            expr = cast(exp.Expression, node)
            col = expr.this.name if hasattr(expr.this, "name") else str(expr.this)
            val = _literal_value(expr.expression) if hasattr(expr, "expression") else None
            out.append({"col": col, "op": op, "val": val})
            return

    out.append({"raw": node.sql()})


def _identifier_name(node: Any) -> str:
    if isinstance(node, exp.Identifier):
        return str(node.this)
    if isinstance(node, exp.Expression) and hasattr(node, "name"):
        return str(node.name)
    return str(node)


def _column_name(node: Any) -> str:
    if isinstance(node, exp.Column):
        if node.table:
            raise UnsupportedSQLError("Only unqualified column names are supported in Sidol v1 DML")
        return _identifier_name(node.this)
    if hasattr(node, "name"):
        return str(node.name)
    raise UnsupportedSQLError(f"Expected column name, got: {node}")


def _coerce_number(text: str) -> int | float | str:
    """Parse a numeric string to int, float, or leave as str."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _literal_value(node: Any) -> Any:
    """Extract a Python scalar from a sqlglot literal node."""
    if isinstance(node, exp.Literal):
        if node.is_string:
            return str(node.this)
        return _coerce_number(str(node.this))
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Neg):
        val = _literal_value(node.this)
        if isinstance(val, (int, float)):
            return -val
    if isinstance(node, exp.Tuple):
        return [_literal_value(v) for v in node.expressions]
    raise UnsupportedSQLError(f"Only literal values are supported in DML (got: {node.sql()!r})")
