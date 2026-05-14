import unittest
import tempfile
import pathlib
import csv
import sidol
from sidol.connectors.csv_ import CSVConnector

class TestCSVCRUD(unittest.TestCase):
    def setUp(self):
        # Create a temp CSV file
        self.tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        with open(self.path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'name', 'role'])
            for i in range(1, 21):
                writer.writerow([i, f"user{i}", f"role{i}"])
        
        # Register connector
        self.db = sidol.connect()
        self.db.register("users", CSVConnector(self.path, writable=True))

    def tearDown(self):
        self.db.close()
        pathlib.Path(self.path).unlink(missing_ok=True)

    def test_csv_sql_crud(self):
        """Verify full CRUD cycle on CSV via Session.sql()."""
        # 1. READ
        res = self.db.sql("SELECT * FROM users")
        rows = res.to_pylist() if hasattr(res, 'to_pylist') else res
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]['name'], 'user1')

        # 2. CREATE
        self.db.sql("INSERT INTO users (id, name, role) VALUES ('21', 'newuser', 'newrole')")
        rows = self.db.sql("SELECT * FROM users").to_pylist()
        self.assertEqual(len(rows), 21)
        self.assertEqual(rows[20]['name'], 'newuser')

        # 3. UPDATE
        self.db.sql("UPDATE users SET name='UPDATED' WHERE id='1'")
        rows = self.db.sql("SELECT * FROM users WHERE id='1'").to_pylist()
        self.assertEqual(rows[0]['name'], 'UPDATED')

        # 4. DELETE
        self.db.sql("DELETE FROM users WHERE id='21'")
        rows = self.db.sql("SELECT * FROM users").to_pylist()
        self.assertEqual(len(rows), 20)

if __name__ == "__main__":
    unittest.main()
