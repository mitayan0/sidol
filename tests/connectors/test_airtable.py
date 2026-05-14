from sidol import AirtableConnector, BaseConnectorTestCase

class TestAirtableConnector(BaseConnectorTestCase):
    def setUp(self):
        super().setUp()
        self.token = "pat_test"
        self.base_id = "app_test"
        self.table = "Tasks"

    def _get_connector(self):
        return AirtableConnector(
            base_id=self.base_id,
            token=self.token,
            table=self.table,
            client=self.get_mock_client()
        )

    def test_schema_inference(self):
        self.mock_response(200, json_data={
            "records": [{
                "id": "rec1",
                "fields": {"Name": "Task 1", "Done": True, "Priority": 3},
                "createdTime": "2023-01-01T00:00:00.000Z"
            }]
        })
        
        conn = self._get_connector()
        schema = conn.schema()
        
        cols = {c.name: c for c in schema.tables[self.table]}
        self.assertIn("id", cols)
        self.assertEqual(cols["Name"].type, "text")
        self.assertEqual(cols["Done"].type, "bool")
        self.assertEqual(cols["Priority"].type, "float")

    def test_fetch_with_formula(self):
        self.mock_response(200, json_data={"records": [{"id": "rec1", "fields": {"Name": "X"}}]})
        
        conn = self._get_connector()
        filters = [{"col": "Name", "op": "=", "val": "Test"}]
        list(conn.fetch(self.table, None, filters, limit=1, offset=0))
        
        params = self.mock_calls[0].url.params
        self.assertEqual(params["filterByFormula"], "{Name} = 'Test'")

    def test_fetch_pagination(self):
        self.mock_response(200, json_data={
            "records": [{"id": "rec1"}],
            "offset": "next_page"
        })
        self.mock_response(200, json_data={"records": [{"id": "rec2"}]})
        
        conn = self._get_connector()
        rows = list(conn.fetch(self.table, None, [], limit=None, offset=0))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "rec1")
        self.assertEqual(rows[1]["id"], "rec2")

    def test_insert_chunking(self):
        # We need 2 responses for 15 records (10 + 5)
        self.mock_response(200, json_data={"records": [{"id": f"r{i}", "fields": {}} for i in range(10)]})
        self.mock_response(200, json_data={"records": [{"id": f"r{i}", "fields": {}} for i in range(10, 15)]})
        
        conn = self._get_connector()
        rows = [{"Name": f"T{i}"} for i in range(15)]
        res = conn.insert(self.table, rows)
        
        self.assert_write_result(res, 15)
        self.assertEqual(len(self.mock_calls), 2)

    def test_delete_matching(self):
        # 1 GET to find IDs, then 1 DELETE
        self.mock_response(200, json_data={"records": [{"id": "rec1"}, {"id": "rec2"}]})
        self.mock_response(200, json_data={"deleted": True})
        
        conn = self._get_connector()
        res = conn.delete(self.table, [{"col": "Name", "op": "=", "val": "X"}])
        
        self.assert_write_result(res, 2)
        self.assertEqual(self.mock_calls[0].method, "GET")
        self.assertEqual(self.mock_calls[1].method, "DELETE")
