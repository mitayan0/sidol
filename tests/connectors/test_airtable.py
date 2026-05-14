import unittest

import httpx

from sidol.connectors.airtable import AirtableConnector


class TestAirtableConnector(unittest.TestCase):
    def setUp(self):
        self.token = "pat_test"
        self.base_id = "app_test"
        self.table = "Tasks"

    def _make_connector(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return AirtableConnector(
            base_id=self.base_id,
            token=self.token,
            table=self.table,
            client=client
        )

    def test_schema_inference(self):
        def handler(request):
            return httpx.Response(200, json={
                "records": [{
                    "id": "rec1",
                    "fields": {"Name": "Task 1", "Done": True, "Priority": 3},
                    "createdTime": "2023-01-01T00:00:00.000Z"
                }]
            })

        conn = self._make_connector(handler)
        schema = conn.schema()

        cols = {c.name: c for c in schema.tables[self.table]}
        self.assertIn("id", cols)
        self.assertEqual(cols["Name"].type, "text")
        self.assertEqual(cols["Done"].type, "bool")
        self.assertEqual(cols["Priority"].type, "float")

    def test_fetch_with_formula(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"records": [{"id": "rec1", "fields": {"Name": "X"}}]})

        conn = self._make_connector(handler)
        filters = [{"col": "Name", "op": "=", "val": "Test"}]
        list(conn.fetch(self.table, None, filters, limit=1, offset=0))

        params = requests[0].url.params
        self.assertEqual(params["filterByFormula"], "{Name} = 'Test'")

    def test_fetch_pagination(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, json={
                    "records": [{"id": "rec1"}],
                    "offset": "next_page"
                })
            return httpx.Response(200, json={"records": [{"id": "rec2"}]})

        conn = self._make_connector(handler)
        rows = list(conn.fetch(self.table, None, [], limit=None, offset=0))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "rec1")
        self.assertEqual(rows[1]["id"], "rec2")

    def test_insert_chunking(self):
        requests = []
        def handler(request):
            requests.append(request)
            data = request.read()
            import json
            payload = json.loads(data)
            # Echo back records with IDs
            recs = [{"id": f"rec{i}", "fields": r["fields"]} for i, r in enumerate(payload["records"])]
            return httpx.Response(200, json={"records": recs})

        conn = self._make_connector(handler)
        # Insert 15 rows (should be 2 requests: 10 + 5)
        rows = [{"Name": f"T{i}"} for i in range(15)]
        res = conn.insert(self.table, rows)

        self.assertEqual(res.affected_rows, 15)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(res.returned), 15)

    def test_delete_matching(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"records": [{"id": "rec1"}, {"id": "rec2"}]})
            return httpx.Response(200, json={"deleted": True})

        conn = self._make_connector(handler)
        res = conn.delete(self.table, [{"col": "Name", "op": "=", "val": "X"}])

        self.assertEqual(res.affected_rows, 2)
        # Should have 1 GET to find IDs, and 1 DELETE to remove them
        methods = [r.method for r in calls]
        self.assertIn("GET", methods)
        self.assertIn("DELETE", methods)

if __name__ == "__main__":
    unittest.main()
