# Sidol — Agent Coding Constitution

> *"Simplicity is a prerequisite for reliability."* — Dijkstra  
> *"Simple is not easy. Simple takes work."* — Rich Hickey  
> *"The best code is no code at all."* — Jeff Atwood  
> *"Don't be clever. Be obvious."* — George Hotz

---

## Part I — The Philosophy (read this first)

### 1. Simple Made Easy (Rich Hickey, 2011)

The most important distinction in software:

| Word | Meaning | Test |
|------|---------|------|
| **Simple** | One role, one concept, one dimension | "How many things does this do?" |
| **Easy** | Familiar, near, convenient | "Have I done this before?" |

**Simple and easy are not the same.** A class that does two things is *easy to write* but *not simple*. Always choose simple over easy.

**Complecting** = braiding two separate concerns into one thing. It is the root cause of all accidental complexity.

> Before writing any function, class, or module, ask:  
> *"What is the one thing this does? Can I name it in 4 words or fewer?"*  
> If you can't — split it.

**Prefer data.** Plain dicts, dataclasses, and primitives outlive clever objects. Data is the simplest possible thing. When in doubt, return a dict.

---

### 2. Karpathy Empiricism

> *"The first implementation is a hypothesis. Measure before you optimise."*

- **Start with the dumbest thing that works.** A `for` loop over a list is almost always the right first answer. A generator, a cache, a connection pool — these are optimisations. Add them only when you have evidence they are needed.
- **Delete code aggressively.** Code you don't have cannot have bugs. Every line is a liability. Ask: *"What breaks if I remove this?"* If the answer is "nothing obvious", remove it.
- **Abstract only after the third repetition.** Copy once — that's fine. Copy twice — leave a comment. Copy three times — extract a function. Never abstract speculatively.
- **If you can't explain it to a 12-year-old, you don't understand it.** Rewrite until you can.

---

### 3. Hotz Minimalism

> *"Complexity is the enemy. The solution is almost always simpler than you think."*

- Ship the smallest possible thing that solves the real problem.
- When you feel the urge to add a feature, ask: *"Do we need this right now?"* If not, don't build it.
- A 50-line file that does one thing perfectly beats a 500-line "framework" every time.
- The right architecture emerges from real constraints — not from anticipating future ones.

---

### 4. Clean Code (Robert C. Martin)

**Names reveal intent.**
```python
# BAD — what is d?
def proc(d, f):
    return [x for x in d if x[f]]

# GOOD — name tells the whole story
def filter_rows_by_column(rows: list[dict], column: str) -> list[dict]:
    return [row for row in rows if row[column]]
```

**Functions do one thing.** Not "one thing and error handling". Not "one thing and logging". One thing. If you use the word "and" to describe what a function does, split it.

**No side effects.** A function named `get_*` or `fetch_*` must never change state. A function that changes state must be named to show it.

**The newspaper rule.** A file should read top-to-bottom like a news article: the most important thing first, details below. Public API at the top, private helpers at the bottom.

**Comments explain *why*, not *what*.** If the code needs a comment to explain what it does, rename things until it doesn't.

---

### 5. Clean Architecture (Robert C. Martin)

**Dependencies point inward — always.**

```
connectors/  →  core.py  →  types.py / errors.py
                              (depend on nothing)
```

- `types.py` and `errors.py` are the innermost layer. They import nothing from sidol.
- `core.py` depends on `types`, `errors`, `registry`, `router`. Never on a connector directly.
- Connectors are **plugins**. They implement `BaseConnector`. The core doesn't care which connector is used.
- You must be able to swap `ServiceNowConnector` for `MockConnector` without touching `core.py`.

**Boundaries protect the domain.** The SQL parsing boundary (`router.py`) means `core.py` never sees raw SQL strings after `parse()`. The connector boundary means `core.py` never makes HTTP calls.

---

## Part II — Sidol-Specific Rules

### Python Style

- **All imports at the top of every file.** Never inside functions, loops, or conditionals.
- **One class or one concept per file.** `csv_.py` holds only `CSVConnector`. No bundling.
- **No metaclasses. No decorators beyond `@abstractmethod` and `@dataclass`.** If you need another decorator, write a comment explaining why.
- **No `**kwargs` in public APIs.** Name every parameter explicitly. `**kwargs` hides the contract.
- **Prefer flat over nested.** Max 3 nesting levels. If you hit 4, extract a function.
- **Short functions.** Max 40 lines. If it's longer, it does more than one thing.
- **Return early.** Guard clauses at the top, happy path at the bottom.

### Imports

```python
# CORRECT — all imports at the top
import sqlite3
from sidol.errors import WriteError
from sidol.types import WriteResult

def my_function():
    ...

# WRONG — deferred import
def my_function():
    from sidol.errors import WriteError  # never do this
    ...
```

### Error Handling

- Raise from `sidol.errors`. Never raise bare `Exception`.
- Stdlib-level violations use builtins: `ValueError`, `TypeError`, `PermissionError`.
- Always chain: `raise SidolError("msg") from original_exc`.
- Never silently swallow. No bare `except: pass`. If you skip an error, write a comment saying exactly why.

### Connector Contract

Every connector implements `BaseConnector` in `connectors/base.py`:

```python
def schema(self) -> Schema: ...
def fetch(self, table, columns, filters, limit, offset) -> Iterator[dict]: ...
def capabilities(self) -> Capabilities: ...
```

Writable connectors also implement `insert`, `update`, `delete`.

- `fetch()` always yields `dict` rows — never lists, never tuples.
- `insert/update/delete` always return `WriteResult(affected_rows=n)`.
- `close()` must be safe to call multiple times (idempotent).

### SQL Scope — v1 Hard Limits

Do not add support for:
- Cross-connector transactions
- DDL (`CREATE TABLE`, `ALTER TABLE`, etc.)
- Subqueries in DML
- `RETURNING` clauses
- Multi-table DML (JOINs in `UPDATE`/`DELETE`)
- Streaming / async iteration

`SELECT` is unlimited — DuckDB handles it.

> If someone asks you to add one of the above: say no, explain the v1 boundary, and record it as a future issue.

### File Layout

```
sidol/
  __init__.py       # public API — direct imports only, no logic
  cache.py          # TTLCache
  core.py           # Session, connect() — the only public entry point
  errors.py         # exception hierarchy — imports nothing
  registry.py       # ConnectorRegistry
  router.py         # parse, extract_*, strict v1 validation guards
  types.py          # Column, Schema, Capabilities, WriteResult, Result — imports nothing
  connectors/
    base.py         # BaseConnector (abstract)
    csv_.py         # CSVConnector
    servicenow.py   # ServiceNowConnector
    sqlite_.py      # SQLiteConnector
tests/
  test_sidol_core.py  # all tests live here
```

**Adding a connector:** create `connectors/<name>.py`, subclass `BaseConnector`, add a direct import to `sidol/__init__.py`, add tests to `test_sidol_core.py`. That's it — no other files change.

### Testing

- Every connector must have tests for: `schema()`, `fetch()`, `insert()`, `update()`, `delete()`.
- No real network in tests — use `httpx.MockTransport` or in-memory DBs.
- No temp files left behind — use `tempfile` and clean up in `tearDown`.
- Run: `uv run python -m unittest tests.test_sidol_core -v`

---

## Part III — Decision Checklist

Before writing or changing any code, answer these:

1. **What is the one thing this does?** (If you can't answer in 4 words, split it.)
2. **What is the simplest possible implementation?** (Start there. Optimise later.)
3. **What can I delete?** (Every line removed is a win.)
4. **Does this complect two concerns?** (If yes, separate them.)
5. **Does this dependency point inward?** (If it doesn't, invert it.)
6. **Will a new contributor understand this in 60 seconds?** (If not, simplify or rename.)
