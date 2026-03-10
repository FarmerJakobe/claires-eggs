from __future__ import annotations

import io
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

    def test_home_page_records_visitor_event(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("claire_visitor", response.headers.get("Set-Cookie", ""))

        with self.app.app_context():
            database = get_db()
            event = database.execute(
                "SELECT path FROM visitor_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["path"], "/")

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

    def test_admin_can_log_manual_sale(self):
        self.login_admin()

        response = self.client.post(
            "/admin/sales/new",
            data={
                "title": "Wednesday market",
                "sale_date": "2026-03-10",
                "amount": "42.50",
                "payment_method": "cash",
                "notes": "Six dozen sold after lunch.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sale logged.", response.data)

        with self.app.app_context():
            database = get_db()
            sale = database.execute(
                "SELECT * FROM sales_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(sale)
        self.assertEqual(sale["title"], "Wednesday market")
        self.assertEqual(sale["amount_cents"], 4250)

    def test_admin_can_upload_expense_receipt(self):
        self.login_admin()

        response = self.client.post(
            "/admin/expenses/new",
            data={
                "vendor": "Feed Store",
                "category": "Feed",
                "expense_date": "2026-03-10",
                "amount": "18.75",
                "notes": "Layer feed refill",
                "receipt_file": (io.BytesIO(b"%PDF-1.4\nfake receipt"), "feed-receipt.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Expense saved.", response.data)

        with self.app.app_context():
            database = get_db()
            expense = database.execute(
                "SELECT * FROM expense_receipts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(expense)
            self.assertEqual(expense["vendor"], "Feed Store")
            self.assertEqual(expense["amount_cents"], 1875)
            receipt_path = os.path.join(
                self.app.config["RECEIPTS_UPLOAD_DIR"], expense["receipt_stored_name"]
            )
        self.assertTrue(os.path.exists(receipt_path))

        receipt_response = self.client.get(f"/admin/expenses/{expense['id']}/receipt")
        self.assertEqual(receipt_response.status_code, 200)
        receipt_response.close()

    def login_admin(self):
        response = self.client.post(
            "/admin/login",
            data={"password": "testing"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)


if __name__ == "__main__":
    unittest.main()
