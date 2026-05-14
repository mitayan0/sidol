# Sidol — Agent Coding Constitution

> *"Simplicity is a prerequisite for reliability."* — Dijkstra

---

## Part I — What Sidol Is

Sidol is a SQL interface over heterogeneous data sources. A query arrives as a SQL string. It is parsed, routed to one or more connectors, executed, and returned as a list of dicts. That is the entire domain.

**A new contributor should be able to trace a query end-to-end — from `session.execute()` to the first yielded row — in under 10 minutes.** If they can't, something is too complex.

Every decision in this document serves that goal.

---

## Part II — The Three Laws

### Law 1: One Thing

Before writing any function, class, or module, answer: **what is the one thing this does?**

If you cannot answer in four words or fewer, split it.

```python
# BAD — does two things (fetches AND transforms)
def get_user_records(filters):
    rows = connector.fetch("users", filters=filters)
    return [{"name": r["full_name"].title(), "id": r["user_id"]} for r in rows]

# GOOD — each function does one thing
def fetch_user_rows(filters):
    return connector.fetch("users", filters=filters)

def format_user_record(row):
    return {"name": row["full_name"].title(), "id": row["user_id"]}
```

The word "and" in a function's description is a split signal. Not "fetches *and* validates". Not "parses *and* logs". One thing.

### Law 2: Dependencies Point Inward

```
connectors/  →  core.py  →  registry.py / router.py  →  types.py / errors.py
                                                          (import nothing)
```

`types.py` and `errors.py` are the innermost layer. They never import from Sidol.
`core.py` never imports a connector directly — only `BaseConnector`.
Connectors are plugins. You must be able to swap `ServiceNowConnector` for `MockConnector` without touching `core.py`.

If a dependency points outward, invert it.

### Law 3: Start Dumb, Stay Dumb Until Proven Otherwise

The first implementation is a hypothesis. A `for` loop is almost always correct. A cache, a pool, a generator — these are optimisations. Add them only when you have a measured reason.

**Abstract only after the third repetition.** Copy once — fine. Copy twice — leave a comment. Copy three times — extract a function. Never abstract speculatively.

---

## Part III — Python Rules

### Naming

Names reveal intent. If a name needs a comment to explain it, the name is wrong.

```python
# BAD
def proc(d, f):
    return [x for x in d if x[f]]

# GOOD
def filter_rows_by_column(rows: list[dict], column: str) -> list[dict]:
    return [row for row in rows if row[column]]
```

- `get_*` and `fetch_*` functions must never mutate state.
- Functions that mutate state must be named to show it: `clear_cache()`, `register_connector()`.
- No single-letter variables except loop counters (`i`, `j`) and well-understood math (`n`).

### Imports

All imports go at the top of the file. Never inside functions, loops, or conditionals.

```python
# CORRECT
import sqlite3
from sidol.errors import WriteError
from sidol.types import WriteResult

def my_function(): ...

# WRONG — deferred import
def my_function():
    from sidol.errors import WriteError  # never do this
```

### Functions

- **Max 40 lines.** Longer means it does more than one thing.
- **Max 3 nesting levels.** At level 4, extract a function.
- **Return early.** Guard clauses at the top; happy path at the bottom.
- **No side effects in getters.** A function named `get_*` returns. Period.

```python
# GOOD — guard clauses first
def fetch_rows(table, limit):
    if table not in self._schema:
        raise SchemaError(f"Unknown table: {table}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    return self._connector.fetch(table, limit=limit)
```

### Classes and Files

- One class or one concept per file. `csv_.py` holds only `CSVConnector`.
- No metaclasses.
- No decorators beyond `@abstractmethod` and `@dataclass`. `@property`, `@classmethod`, and `@staticmethod` are allowed. Custom decorators require a comment explaining why no other approach works.
- No `**kwargs` in public APIs. Name every parameter explicitly. `**kwargs` hides the contract.

### Error Handling

- Raise from `sidol.errors`. Never raise bare `Exception`.
- Standard violations use builtins: `ValueError`, `TypeError`, `PermissionError`.
- Always chain exceptions: `raise SidolError("msg") from original_exc`.
- Never swallow silently. No bare `except: pass`. If skipping an error is correct, write a comment saying exactly why.

### Comments

Comments explain *why*, not *what*. If code needs a comment to explain what it does, rename things until it doesn't.

```python
# BAD — explains what
# iterate over rows and return matches
return [row for row in rows if row[column]]

# GOOD — explains why
# ServiceNow paginates at 1000 rows; offset keeps our position across pages
offset = page * 1000
```

---

## Part IV — The Connector Contract

Every connector implements `BaseConnector` in `connectors/base.py`:

```python
def schema(self) -> Schema: ...
def fetch(self, table, columns, filters, limit, offset) -> Iterator[dict]: ...
def capabilities(self) -> Capabilities: ...
```

Writable connectors also implement:

```python
def insert(self, table, rows) -> WriteResult: ...
def update(self, table, updates, filters) -> WriteResult: ...
def delete(self, table, filters) -> WriteResult: ...
```

**Rules:**
- `fetch()` always yields `dict` rows — never lists, never tuples.
- `insert/update/delete` always return `WriteResult(affected_rows=n)`.
- `close()` must be safe to call multiple times (idempotent).
- Connectors never import from `core.py`. The dependency goes one way.

### Minimal Working Connector

This is the floor. Every new connector starts here:

```python
# sidol/connectors/example.py
from collections.abc import Iterator
from sidol.connectors.base import BaseConnector
from sidol.types import Schema, Column, Capabilities, WriteResult


class ExampleConnector(BaseConnector):

    def __init__(self, source):
        self._source = source
        self._closed = False

    def schema(self) -> Schema:
        return Schema(tables={
            "items": [Column(name="id", dtype="int"), Column(name="name", dtype="str")]
        })

    def capabilities(self) -> Capabilities:
        return Capabilities(writable=False)

    def fetch(self, table, columns=None, filters=None, limit=None, offset=0) -> Iterator[dict]:
        for row in self._source:
            yield row

    def close(self):
        self._closed = True  # safe to call again — idempotent
```

Add a direct import to `sidol/__init__.py`. Add tests to `test_sidol_core.py`. No other files change.

---

## Part V — SQL Scope (v1 Hard Limits)

`SELECT` is unlimited — DuckDB handles it.

**Do not add support for:**
- Cross-connector transactions
- DDL (`CREATE TABLE`, `ALTER TABLE`, etc.)
- Subqueries in DML
- `RETURNING` clauses
- Multi-table DML (JOINs in `UPDATE`/`DELETE`)
- Streaming / async iteration

If someone asks for one of the above: decline, explain the v1 boundary, and record it as a future issue. These are not omissions — they are deliberate constraints that keep the codebase traceable.

---

## Part VI — Why We Said No

These are real decisions. They are here so no one re-litigates them without knowing the reasoning.

**Why not async?**
v1 connectors are I/O-bound but not high-concurrency. Sync code is debuggable by anyone. `asyncio` adds a second execution model that every contributor must understand. We will revisit when we have a measured concurrency bottleneck.

**Why not SQLAlchemy?**
We need to own the SQL parsing boundary. A dependency that owns SQL parsing owns a core piece of `router.py`. SQLAlchemy is excellent software — it solves a different problem than Sidol solves.

**Why not one big `connectors.py` file?**
When a connector breaks, you want the blast radius to be exactly one file. Co-location makes that impossible. One file per connector means one file per failure.

**Why not `**kwargs` in connector methods?**
`**kwargs` makes the contract invisible. A new connector author cannot know what keys are expected without reading every caller. Explicit parameters are documentation.

**Why not support `RETURNING`?**
`RETURNING` is not supported by all backends we target. Faking it in the core would either lie to the caller or couple core logic to backend capability detection. The v1 answer is: fetch again after writing.

---

## Part VII — File Layout

```
sidol/
  __init__.py       # public API — direct imports only, no logic
  cache.py          # TTLCache
  core.py           # Session, connect() — the only public entry point
  errors.py         # exception hierarchy — imports nothing
  registry.py       # ConnectorRegistry
  router.py         # parse, extract_*, strict v1 validation guards
  types.py          # Column, Schema, Capabilities, WriteResult — imports nothing
  connectors/
    base.py         # BaseConnector (abstract)
    csv_.py         # CSVConnector
    servicenow.py   # ServiceNowConnector
    sqlite_.py      # SQLiteConnector
tests/
  test_sidol_core.py
```

The newspaper rule applies to every file: most important thing at the top, implementation details at the bottom. Public API before private helpers.

---

## Part VIII — Testing

- Every connector must test: `schema()`, `fetch()`, `insert()`, `update()`, `delete()`.
- No real network calls in tests — use `httpx.MockTransport` or in-memory DBs.
- No temp files left behind — use `tempfile` and clean up in `tearDown`.
- Run: `uv run python -m unittest tests.test_sidol_core -v`

---

## Part IX — Pre-Commit Checklist

Answer every question before opening a PR. These are pass/fail, not aspirational.

| Check | Pass | Fail |
|-------|------|------|
| Single responsibility | Name fits in 4 words or fewer | Description uses "and" |
| Dependency direction | Only imports from inner layers | Connector imports from core |
| Side-effect safety | `get_*` functions only return | `get_*` mutates state |
| Import placement | All imports at file top | Any import inside a function |
| Error chaining | `raise X from original` | Bare `raise X(msg)` after a catch |
| Test coverage | schema, fetch, write all tested | Any method untested |
| No silent swallow | Every `except` has a comment | Bare `except: pass` |
| v1 boundary | No new DML/DDL/async features | Any item from the hard-limits list |
