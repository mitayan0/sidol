# Sidol

[![GitHub](https://img.shields.io/badge/github-mitayan0%2Fsidol-blue?logo=github)](https://github.com/mitayan0/sidol)

**SQL for everything.** Read, write, and delete across any API or database using plain SQL.

- `SELECT` → executed through DuckDB (fast, in-memory, federated)
- `INSERT` / `UPDATE` / `DELETE` → parsed by sqlglot, routed to connector write APIs
- Built-in connectors: **ServiceNow**, **CSV**, **SQLite**
- Extensible: implement `BaseConnector` to add any source

### Connector Support

| Connector | SELECT | INSERT | UPDATE | DELETE | Filter Pushdown |
|-----------|:---:|:---:|:---:|:---:|:---:|
| **ServiceNow** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **CSV** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **SQLite** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Airtable** | ✅ | ✅ | ✅ | ✅ | ✅ |

See [ROADMAP.md](ROADMAP.md) for phased delivery (Phase 2 current focus).

```python
import sidol

db = sidol.connect()
db.register("incidents", sidol.ServiceNowConnector(instance="myco", table="incident", ...))
db.register("local",     sidol.SQLiteConnector(path="./data.db"))

tbl = db.sql("SELECT number, state FROM incidents WHERE priority = 1")  # pyarrow.Table
db.sql("UPDATE incidents SET state = 'closed' WHERE number = 'INC001'")
```

---

## Setup (with uv — recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package manager. Install it once, then:

```bash
# Install uv (one-time)
# Windows:
winget install --id=astral-sh.uv -e
# macOS/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone & install all dependencies in ~2 seconds
git clone https://github.com/mitayan0/sidol
cd sidol
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check sidol/

# Type-check
uv run mypy sidol/
```

## Setup (classic pip)

```bash
pip install -e .
pip install pytest pytest-asyncio respx mypy ruff
python -m pytest
```

---

## Architecture

```
SQL string
  │
  ├─ SELECT ──→ DuckDB (in-memory) ──→ connector.fetch() per table ──→ DataFrame
  │
  └─ DML ─────→ sqlglot AST ──→ extract table/values/filters ──→ connector.insert/update/delete()
```

Three layers:
1. **SQL interface** — `sidol.connect()`, `db.sql()`, `db.register()`
2. **Router** — `sidol/router.py` parses and dispatches
3. **Connectors** — pluggable `BaseConnector` implementations

---

## Writing a Connector

```python
from sidol.connectors.base import BaseConnector
from sidol.types import Schema, Column, Capabilities, WriteResult

class MyConnector(BaseConnector):
    def schema(self) -> Schema:
        return Schema(tables={"mytable": [Column("id", "int", primary_key=True)]})

    def fetch(self, table, columns, filters, limit, offset):
        yield {"id": 1, "name": "hello"}

    def capabilities(self) -> Capabilities:
        return Capabilities(readable=True, insertable=True)

    def insert(self, table, rows) -> WriteResult:
        ...
        return WriteResult(affected_rows=len(rows))
```

---

## Supported SQL Subset (v1)

| Statement | Support |
|-----------|---------|
| `SELECT` | ✅ Full (via DuckDB — JOINs, aggregates, CTEs) |
| `INSERT INTO t (cols) VALUES (...)` | ✅ Literal values only |
| `UPDATE t SET col=val WHERE ...` | ✅ Simple equality filters |
| `DELETE FROM t WHERE ...` | ✅ Simple equality filters |
| `CREATE TABLE` | ❌ Not supported in v1 |
| Subqueries in DML | ❌ Not supported in v1 |

---

## License

MIT — see [LICENSE](https://github.com/mitayan0/sidol/blob/main/LICENSE)
