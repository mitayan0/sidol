# Connector Authoring Guide

So you want to add a new data source to Sidol? Excellent. Follow this guide to ensure your connector is reliable, traceable, and "Constitutional."

---

## 1. The Connector Contract

Every connector must inherit from `sidol.BaseConnector` and implement the following methods:

### `schema(self) -> Schema`
Return a `Schema` object describing the tables and columns available in this source.
*   **Rule**: If metadata is expensive to fetch, cache it.
*   **Rule**: Use `sidol.Column` to map native types to Sidol types (`text`, `int`, `float`, `bool`, `timestamp`).

### `fetch(self, table, columns, filters, limit, offset, context=None) -> Iterator[dict]`
The core of the connector. Yields rows as dictionaries.
*   **Filters**: A list of dicts like `{"col": "priority", "op": "=", "val": 1}`.
*   **Pushdown**: If your API supports server-side filtering, translate these filters into the API's native format.
*   **Context**: Use `context.query_timeout` or `context.user_id` if your API requires them.

### `insert`, `update`, `delete` (Optional)
Implement these for writable sources. They must return a `WriteResult(affected_rows=n)`.

---

## 2. Testing with `BaseConnectorTestCase`

Sidol provides a standardized test harness in `sidol.BaseConnectorTestCase`. Using it ensures your connector handles pagination, errors, and JSON parsing correctly.

```python
from sidol import BaseConnectorTestCase, AirtableConnector

class TestMyConnector(BaseConnectorTestCase):
    def setUp(self):
        super().setUp()
        self.conn = MyConnector(client=self.get_mock_client())

    def test_fetch_mapping(self):
        # 1. Queue a mock response
        self.mock_response(200, json_data={"records": [{"id": "1", "fields": {"name": "test"}}]})
        
        # 2. Execute the fetch
        rows = list(self.conn.fetch("mytable", None, [], limit=1, offset=0))
        
        # 3. Assert on results
        self.assertEqual(len(rows), 1)
        self.assert_last_request("GET", "/api/v1/mytable")
```

---

## 3. The "Law of One Thing"

Avoid "complected" code. If your connector needs to build complex query strings (like ServiceNow's GlideEncodedQuery or Airtable's formulas), extract that logic into a separate `utils.py` file.

**Correct Layout:**
```
sidol/connectors/
  my_service.py       # Holds the MyConnector class (thin wrapper)
  my_service_utils.py # Pure functions for formula building/mapping
```

---

## 4. Checklist Before Submission

- [ ] **Unit of Thought**: Function length < 40 lines (fits on one screen).
- [ ] **Low Complexity**: Max 3 levels of nesting.
- [ ] **Explicit Contract**: No `**kwargs` in public methods.
- [ ] **Clean Scope**: All imports at the top of the file.
- [ ] **Standard Compliance**: `ruff check .` passes.
- [ ] **Contract Verification**: Unit tests cover `schema`, `fetch`, and any write operations.
