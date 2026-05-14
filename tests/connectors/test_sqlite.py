import unittest
import tempfile
import sqlite3
import pathlib
import sidol
from sidol.connectors.sqlite_ import SQLiteConnector

class TestSQLiteCRUD(unittest.TestCase):
    def setUp(self):
        # Create a temp SQLite file
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        
        # Seed the database
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO users VALUES (1, 'alice')")
        con.commit()
        con.close()
        
        # Register connector
        self.db = sidol.connect()
        self.db.register("users", SQLiteConnector(self.path))

    def tearDown(self):
        self.db.close()
        pathlib.Path(self.path).unlink(missing_ok=True)

    def test_sqlite_sql_crud(self):
        """Verify full CRUD cycle on SQLite via Session.sql()."""
        # 1. READ
        res = self.db.sql("SELECT * FROM users")
        rows = res.to_pylist() if hasattr(res, 'to_pylist') else res
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'alice')

        # 2. CREATE
        self.db.sql("INSERT INTO users (id, name) VALUES (2, 'bob')")
        rows = self.db.sql("SELECT * FROM users").to_pylist()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['name'], 'bob')

        # 3. UPDATE
        self.db.sql("UPDATE users SET name='ALICE' WHERE id=1")
        rows = self.db.sql("SELECT * FROM users WHERE id=1").to_pylist()
        self.assertEqual(rows[0]['name'], 'ALICE')

        # 4. DELETE
        self.db.sql("DELETE FROM users WHERE id=2")
        rows = self.db.sql("SELECT * FROM users").to_pylist()
        self.assertEqual(len(rows), 1)

if __name__ == "__main__":
    unittest.main()
