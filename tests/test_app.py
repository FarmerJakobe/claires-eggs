from __future__ import annotations

import io
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.app import create_app
from app.facebook import publish_post
from app.db import get_db
from app.payments import StripeSessionUpdate
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
        os.environ.pop("SITE_URL", None)
        os.environ.pop("STRIPE_SECRET_KEY", None)
        os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        os.environ.pop("FACEBOOK_SYNC_MODE", None)
        os.environ.pop("FACEBOOK_PAGE_ID", None)
        os.environ.pop("FACEBOOK_PAGE_ACCESS_TOKEN", None)

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

    @patch("app.app.parse_stripe_webhook")
    def test_stripe_webhook_marks_order_paid(self, mock_parse_webhook):
        order_id = self.create_test_order(payment_method="card", payment_status="awaiting_payment")
        mock_parse_webhook.return_value = StripeSessionUpdate(
            order_id=order_id,
            payment_status="paid_online",
            stripe_reference="pi_test_123",
            checkout_url=None,
            detail="Stripe confirmed payment.",
        )

        response = self.client.post(
            "/webhooks/stripe",
            data=b"{}",
            headers={"Stripe-Signature": "test-signature"},
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            database = get_db()
            order = database.execute(
                "SELECT payment_status, stripe_reference, stripe_checkout_url FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(order["payment_status"], "paid_online")
        self.assertEqual(order["stripe_reference"], "pi_test_123")
        self.assertFalse(order["stripe_checkout_url"])

    @patch("app.facebook.urlopen")
    def test_facebook_page_publish_returns_published_status(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = b'{"id":"123_456"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        mock_urlopen.return_value = response

        result = publish_post(
            {
                "publish_to_facebook": 1,
                "facebook_message": "Fresh eggs this Wednesday.",
                "excerpt": "Fresh eggs this Wednesday.",
                "slug": "fresh-eggs",
                "is_published": 1,
            },
            {
                "FACEBOOK_SYNC_MODE": "page",
                "FACEBOOK_PAGE_ID": "page-123",
                "FACEBOOK_PAGE_ACCESS_TOKEN": "page-token",
                "FACEBOOK_GRAPH_API_VERSION": "v23.0",
                "SITE_URL": "https://eggsincrawford.com",
            },
        )

        self.assertEqual(result.status, "published")
        self.assertIn("123_456", result.detail)

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

    def test_dashboard_shows_reservation_contact_info_and_actions(self):
        order_id = self.create_test_order()
        self.login_admin()

        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"test@example.com", response.data)
        self.assertIn(b"555-1212", response.data)
        self.assertIn(b"Confirm", response.data)
        self.assertIn(b"Fulfilled", response.data)

    def test_confirmed_and_fulfilled_statuses_work_for_cash_orders(self):
        order_id = self.create_test_order()
        self.login_admin()

        response = self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"order_status": "confirmed"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            database = get_db()
            order = database.execute(
                "SELECT order_status, payment_status FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(order["order_status"], "confirmed")
        self.assertEqual(order["payment_status"], "reserved")

        response = self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"order_status": "fulfilled"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            database = get_db()
            order = database.execute(
                "SELECT order_status, payment_status FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        self.assertEqual(order["order_status"], "fulfilled")
        self.assertEqual(order["payment_status"], "paid_in_person")

    def login_admin(self):
        response = self.client.post(
            "/admin/login",
            data={"password": "testing"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)

    def create_test_order(self, payment_method="cash", payment_status="reserved", order_status="open"):
        with self.app.app_context():
            database = get_db()
            cursor = database.execute(
                """
                INSERT INTO orders (
                    customer_name, email, phone, payment_method, payment_status, order_status,
                    pickup_date, pickup_window, subtotal_cents, fee_cents, total_cents,
                    notes, created_at, updated_at, stripe_reference, stripe_checkout_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Test Customer",
                    "test@example.com",
                    "555-1212",
                    payment_method,
                    payment_status,
                    order_status,
                    "2026-03-11",
                    "3:00 PM - 4:00 PM MDT",
                    650,
                    65 if payment_method == "card" else 0,
                    715 if payment_method == "card" else 650,
                    "",
                    "2026-03-10T00:00:00-06:00",
                    "2026-03-10T00:00:00-06:00",
                    "",
                    "https://checkout.stripe.test/session",
                ),
            )
            database.commit()
            return cursor.lastrowid


if __name__ == "__main__":
    unittest.main()
