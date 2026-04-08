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
                "zip_code": "81416",
                "pickup_type": "market",
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
        reference = datetime(2026, 3, 11, 16, 31, tzinfo=DENVER)
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

    def test_admin_can_edit_manual_sale(self):
        self.login_admin()

        with self.app.app_context():
            database = get_db()
            database.execute(
                """
                INSERT INTO sales_entries (
                    sale_date, title, amount_cents, payment_method, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-04-07",
                    "Original market sale",
                    2500,
                    "cash",
                    "Initial note",
                    "2026-04-07T09:00:00-06:00",
                ),
            )
            database.commit()
            sale_id = database.execute(
                "SELECT id FROM sales_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]

        response = self.client.post(
            f"/admin/sales/{sale_id}/edit",
            data={
                "title": "Updated market sale",
                "sale_date": "2026-04-08",
                "amount": "31.75",
                "payment_method": "card",
                "notes": "Adjusted after recount.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sale entry updated.", response.data)
        self.assertIn(b"Edit", response.data)

        with self.app.app_context():
            database = get_db()
            sale = database.execute(
                "SELECT * FROM sales_entries WHERE id = ?",
                (sale_id,),
            ).fetchone()
        self.assertEqual(sale["title"], "Updated market sale")
        self.assertEqual(sale["sale_date"], "2026-04-08")
        self.assertEqual(sale["amount_cents"], 3175)
        self.assertEqual(sale["payment_method"], "card")
        self.assertEqual(sale["notes"], "Adjusted after recount.")

    def test_admin_can_delete_manual_sale(self):
        self.login_admin()

        with self.app.app_context():
            database = get_db()
            database.execute(
                """
                INSERT INTO sales_entries (
                    sale_date, title, amount_cents, payment_method, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "2026-04-07",
                    "Delete me",
                    1200,
                    "cash",
                    "Temporary sale",
                    "2026-04-07T09:30:00-06:00",
                ),
            )
            database.commit()
            sale_id = database.execute(
                "SELECT id FROM sales_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]

        response = self.client.post(
            f"/admin/sales/{sale_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sale entry deleted.", response.data)

        with self.app.app_context():
            database = get_db()
            sale = database.execute(
                "SELECT * FROM sales_entries WHERE id = ?",
                (sale_id,),
            ).fetchone()
        self.assertIsNone(sale)

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
        self.assertIn(b"ZIP 81416", response.data)
        self.assertIn(b"Pickup choice:</strong> Hitching Post", response.data)
        self.assertIn(b"Confirm", response.data)
        self.assertIn(b"Fulfilled", response.data)
        self.assertIn(b"Cancel + Restock", response.data)

    def test_dashboard_shows_farm_pickup_choice(self):
        self.create_test_order(
            pickup_type="farm",
            pickup_location="Farm pickup",
            pickup_date="",
            pickup_window="Claire will contact you to arrange pickup.",
        )
        self.login_admin()

        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pickup choice:</strong> Farm pickup", response.data)
        self.assertIn(b"Claire will contact you to arrange pickup.", response.data)

    def test_order_rejects_zip_codes_outside_local_counties(self):
        with self.app.app_context():
            database = get_db()
            item = database.execute(
                "SELECT * FROM inventory_items ORDER BY id ASC LIMIT 1"
            ).fetchone()

        response = self.client.post(
            "/orders",
            data={
                "customer_name": "Outside Customer",
                "email": "outside@example.com",
                "phone": "555-0000",
                "zip_code": "80202",
                "pickup_type": "market",
                "payment_method": "cash",
                f"item_{item['id']}": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Reservations are only available for Delta County or Montrose County ZIP codes.",
            response.data,
        )

    def test_farm_pickup_order_shows_contact_follow_up(self):
        with self.app.app_context():
            database = get_db()
            item = database.execute(
                "SELECT * FROM inventory_items ORDER BY id ASC LIMIT 1"
            ).fetchone()

        response = self.client.post(
            "/orders",
            data={
                "customer_name": "Farm Pickup Customer",
                "email": "farm@example.com",
                "phone": "555-7777",
                "zip_code": "81416",
                "pickup_type": "farm",
                "payment_method": "cash",
                f"item_{item['id']}": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Claire will contact you", response.data)

        with self.app.app_context():
            database = get_db()
            order = database.execute(
                "SELECT pickup_type, pickup_location, pickup_date, pickup_window FROM orders ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(order["pickup_type"], "farm")
        self.assertEqual(order["pickup_location"], "Farm pickup")
        self.assertEqual(order["pickup_date"], "")
        self.assertEqual(order["pickup_window"], "Claire will contact you to arrange pickup.")

    def test_admin_can_post_notice_with_image(self):
        self.login_admin()

        response = self.client.post(
            "/admin/notices/new",
            data={
                "message": "Fresh eggs are packed and ready for Wednesday pickup.",
                "publish_to_facebook": "1",
                "notice_image": (io.BytesIO(b"fakepng"), "notice.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Notice board post published.", response.data)

        with self.app.app_context():
            database = get_db()
            post = database.execute(
                "SELECT title, body, image_stored_name, publish_to_facebook FROM posts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(post)
            self.assertIn("Fresh eggs are packed", post["title"])
            self.assertEqual(post["body"], "Fresh eggs are packed and ready for Wednesday pickup.")
            self.assertEqual(post["publish_to_facebook"], 1)
            image_path = os.path.join(
                self.app.config["POSTS_UPLOAD_DIR"], post["image_stored_name"]
            )
        self.assertTrue(os.path.exists(image_path))

    def test_admin_can_delete_contact_message(self):
        with self.app.app_context():
            database = get_db()
            database.execute(
                """
                INSERT INTO contact_messages (name, email, phone, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "Spam Caller",
                    "spam@example.com",
                    "555-9999",
                    "Please call me about crypto.",
                    "2026-03-24T12:00:00-06:00",
                ),
            )
            database.commit()
            message_id = database.execute(
                "SELECT id FROM contact_messages ORDER BY id DESC LIMIT 1"
            ).fetchone()["id"]

        self.login_admin()

        response = self.client.post(
            f"/admin/messages/{message_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Message deleted.", response.data)

        with self.app.app_context():
            database = get_db()
            remaining = database.execute(
                "SELECT id FROM contact_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
        self.assertIsNone(remaining)

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

    def test_cancelled_order_returns_inventory(self):
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
                "customer_name": "Cancel Test",
                "email": "cancel@example.com",
                "phone": "555-8888",
                "zip_code": "81416",
                "pickup_type": "market",
                "payment_method": "cash",
                f"item_{item_id}": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        order_id = int(response.headers["Location"].split("/orders/")[1].split("/")[0])

        self.login_admin()
        response = self.client.post(
            f"/admin/orders/{order_id}/status",
            data={"order_status": "cancelled"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cancelled and stock returned to inventory", response.data)

        with self.app.app_context():
            database = get_db()
            order = database.execute(
                "SELECT order_status FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()
            updated = database.execute(
                "SELECT quantity_available FROM inventory_items WHERE id = ?",
                (item_id,),
            ).fetchone()
            movement = database.execute(
                """
                SELECT delta, reason
                FROM inventory_movements
                WHERE inventory_item_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        self.assertEqual(order["order_status"], "cancelled")
        self.assertEqual(updated["quantity_available"], starting_quantity)
        self.assertEqual(movement["delta"], 1)
        self.assertIn("cancelled", movement["reason"])

    def login_admin(self):
        response = self.client.post(
            "/admin/login",
            data={"password": "testing"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin dashboard", response.data)

    def create_test_order(
        self,
        payment_method="cash",
        payment_status="reserved",
        order_status="open",
        pickup_type="market",
        pickup_location="Hitching Post, Crawford, Colorado",
        pickup_date="2026-03-11",
        pickup_window="3:00 PM - 4:30 PM MDT",
    ):
        with self.app.app_context():
            database = get_db()
            cursor = database.execute(
                """
                INSERT INTO orders (
                    customer_name, email, phone, zip_code, pickup_type, pickup_location,
                    payment_method, payment_status, order_status,
                    pickup_date, pickup_window, subtotal_cents, fee_cents, total_cents,
                    notes, created_at, updated_at, stripe_reference, stripe_checkout_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Test Customer",
                    "test@example.com",
                    "555-1212",
                    "81416",
                    pickup_type,
                    pickup_location,
                    payment_method,
                    payment_status,
                    order_status,
                    pickup_date,
                    pickup_window,
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
