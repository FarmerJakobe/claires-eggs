from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

from app.app import create_app
from app.db import get_db
from app.schedule import DENVER, next_pickup_window


class ClaireEggsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = os.path.join(self.temp_dir.name, "test.db")
        os.environ["DATABASE_PATH"] = database_path
        os.environ["PAYMENT_MODE"] = "demo"
        os.environ["FLASK_SECRET_KEY"] = "test-secret"
        os.environ["CLAIRE_ADMIN_PASSWORD"] = "testing"
        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("PAYMENT_MODE", None)
        os.environ.pop("FLASK_SECRET_KEY", None)
        os.environ.pop("CLAIRE_ADMIN_PASSWORD", None)

    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Claire's Eggs", response.data)

    def test_cash_order_reduces_inventory(self):
        with self.app.app_context():
            database = get_db()
            item = database.execute(
                "SELECT * FROM inventory_items ORDER BY id ASC LIMIT 1"
            ).fetchone()
            item_id = item["id"]
            starting_quantity = item["quantity_available"]

        response = self.client.post(
            "/orders",
            data={
                "customer_name": "Test Customer",
                "email": "test@example.com",
                "phone": "555-1212",
                "payment_method": "cash",
                f"item_{item_id}": "2",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Order confirmed", response.data)

        with self.app.app_context():
            database = get_db()
            updated = database.execute(
                "SELECT quantity_available FROM inventory_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        self.assertEqual(updated["quantity_available"], starting_quantity - 2)

    def test_next_pickup_rolls_after_wednesday_close(self):
        reference = datetime(2026, 3, 11, 16, 1, tzinfo=DENVER)
        pickup = next_pickup_window(reference)
        self.assertEqual(pickup.starts_at.date().isoformat(), "2026-03-18")


if __name__ == "__main__":
    unittest.main()
