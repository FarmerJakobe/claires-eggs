from __future__ import annotations

import sqlite3
from typing import Iterable

from flask import current_app

from .facebook import publish_post
from .payments import create_payment
from .schedule import local_now, next_pickup_window
from .utils import card_fee_cents, slugify


class StoreError(Exception):
    pass


def list_active_inventory(database: sqlite3.Connection):
    return database.execute(
        """
        SELECT *
        FROM inventory_items
        WHERE is_active = 1 AND quantity_available > 0
        ORDER BY display_order ASC, name ASC
        """
    ).fetchall()


def list_all_inventory(database: sqlite3.Connection):
    return database.execute(
        """
        SELECT *
        FROM inventory_items
        ORDER BY display_order ASC, name ASC
        """
    ).fetchall()


def get_inventory_item(database: sqlite3.Connection, item_id: int):
    return database.execute(
        "SELECT * FROM inventory_items WHERE id = ?",
        (item_id,),
    ).fetchone()


def create_inventory_item(database: sqlite3.Connection, form_data: dict) -> None:
    price_cents = int(form_data["price_cents"])
    quantity_available = int(form_data["quantity_available"])
    display_order = int(form_data.get("display_order", 0))
    if price_cents < 0 or quantity_available < 0 or display_order < 0:
        raise StoreError("Price, quantity, and display order must be zero or greater.")

    now = local_now().isoformat()
    database.execute(
        """
        INSERT INTO inventory_items (
            name, description, unit_label, price_cents, quantity_available,
            is_active, display_order, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_data["name"].strip(),
            form_data["description"].strip(),
            form_data["unit_label"].strip(),
            price_cents,
            quantity_available,
            1 if form_data.get("is_active") else 0,
            display_order,
            now,
            now,
        ),
    )


def update_inventory_item(
    database: sqlite3.Connection, item_id: int, form_data: dict, reason: str
) -> None:
    existing = get_inventory_item(database, item_id)
    if not existing:
        raise StoreError("Inventory item not found.")

    price_cents = int(form_data["price_cents"])
    new_quantity = int(form_data["quantity_available"])
    display_order = int(form_data.get("display_order", 0))
    if price_cents < 0 or new_quantity < 0 or display_order < 0:
        raise StoreError("Price, quantity, and display order must be zero or greater.")

    quantity_delta = new_quantity - int(existing["quantity_available"])
    now = local_now().isoformat()

    database.execute(
        """
        UPDATE inventory_items
        SET name = ?, description = ?, unit_label = ?, price_cents = ?,
            quantity_available = ?, is_active = ?, display_order = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            form_data["name"].strip(),
            form_data["description"].strip(),
            form_data["unit_label"].strip(),
            price_cents,
            new_quantity,
            1 if form_data.get("is_active") else 0,
            display_order,
            now,
            item_id,
        ),
    )

    if quantity_delta:
        database.execute(
            """
            INSERT INTO inventory_movements (inventory_item_id, delta, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (item_id, quantity_delta, reason, now),
        )


def list_recent_orders(database: sqlite3.Connection):
    return database.execute(
        """
        SELECT *
        FROM orders
        ORDER BY created_at DESC
        """
    ).fetchall()


def list_order_items(database: sqlite3.Connection, order_ids: Iterable[int]):
    order_ids = list(order_ids)
    if not order_ids:
        return {}

    placeholders = ",".join(["?"] * len(order_ids))
    rows = database.execute(
        f"""
        SELECT *
        FROM order_items
        WHERE order_id IN ({placeholders})
        ORDER BY id ASC
        """,
        order_ids,
    ).fetchall()

    grouped = {order_id: [] for order_id in order_ids}
    for row in rows:
        grouped[row["order_id"]].append(row)
    return grouped


def get_order(database: sqlite3.Connection, order_id: int):
    order = database.execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not order:
        return None

    items = database.execute(
        """
        SELECT *
        FROM order_items
        WHERE order_id = ?
        ORDER BY id ASC
        """,
        (order_id,),
    ).fetchall()

    return {"order": order, "items": items}


def update_order_status(database: sqlite3.Connection, order_id: int, new_status: str) -> None:
    order_bundle = get_order(database, order_id)
    if not order_bundle:
        raise StoreError("Order not found.")

    order = order_bundle["order"]
    if order["order_status"] == new_status:
        return

    now = local_now().isoformat()
    if new_status == "cancelled" and order["order_status"] != "cancelled":
        for item in order_bundle["items"]:
            database.execute(
                """
                UPDATE inventory_items
                SET quantity_available = quantity_available + ?, updated_at = ?
                WHERE id = ?
                """,
                (item["quantity"], now, item["inventory_item_id"]),
            )
            database.execute(
                """
                INSERT INTO inventory_movements (inventory_item_id, delta, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (item["inventory_item_id"], item["quantity"], f"Order #{order_id} cancelled", now),
            )

    payment_status = order["payment_status"]
    if new_status == "picked_up" and order["payment_method"] == "cash":
        payment_status = "paid_in_person"

    database.execute(
        """
        UPDATE orders
        SET order_status = ?, payment_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_status, payment_status, now, order_id),
    )


def list_posts(database: sqlite3.Connection, published_only: bool = False):
    if published_only:
        return database.execute(
            """
            SELECT *
            FROM posts
            WHERE is_published = 1
            ORDER BY COALESCE(published_at, created_at) DESC
            """
        ).fetchall()

    return database.execute(
        """
        SELECT *
        FROM posts
        ORDER BY created_at DESC
        """
    ).fetchall()


def get_post_by_slug(database: sqlite3.Connection, slug: str):
    return database.execute(
        "SELECT * FROM posts WHERE slug = ? AND is_published = 1",
        (slug,),
    ).fetchone()


def get_post(database: sqlite3.Connection, post_id: int):
    return database.execute(
        "SELECT * FROM posts WHERE id = ?",
        (post_id,),
    ).fetchone()


def save_post(database: sqlite3.Connection, form_data: dict, post_id: int | None = None) -> int:
    title = form_data["title"].strip()
    body = form_data["body"].strip()
    if not title or not body:
        raise StoreError("Title and body are required.")
    excerpt = form_data["excerpt"].strip() or body[:140].strip()
    is_published = 1 if form_data.get("is_published") else 0
    publish_to_facebook = 1 if form_data.get("publish_to_facebook") else 0
    facebook_message = form_data["facebook_message"].strip() or excerpt
    now = local_now().isoformat()
    slug = slugify(title)

    if post_id:
        existing = get_post(database, post_id)
        if not existing:
            raise StoreError("Post not found.")
        if existing["slug"] != slug:
            slug = ensure_unique_slug(database, slug, post_id)
        published_at = existing["published_at"] or (now if is_published else None)
        database.execute(
            """
            UPDATE posts
            SET title = ?, slug = ?, excerpt = ?, body = ?, is_published = ?,
                publish_to_facebook = ?, facebook_message = ?, facebook_status = ?, facebook_last_error = ?,
                updated_at = ?, published_at = ?
            WHERE id = ?
            """,
            (
                title,
                slug,
                excerpt,
                body,
                is_published,
                publish_to_facebook,
                facebook_message,
                existing["facebook_status"] if publish_to_facebook else "not-requested",
                "" if not publish_to_facebook else existing["facebook_last_error"],
                now,
                published_at,
                post_id,
            ),
        )
        target_id = post_id
    else:
        slug = ensure_unique_slug(database, slug, None)
        cursor = database.execute(
            """
            INSERT INTO posts (
                title, slug, excerpt, body, is_published, publish_to_facebook,
                facebook_status, facebook_message, created_at, updated_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                slug,
                excerpt,
                body,
                is_published,
                publish_to_facebook,
                "queued" if (is_published and publish_to_facebook) else "not-requested",
                facebook_message,
                now,
                now,
                now if is_published else None,
            ),
        )
        target_id = cursor.lastrowid

    if is_published and publish_to_facebook:
        sync_post_to_facebook(database, target_id)

    return target_id


def sync_post_to_facebook(database: sqlite3.Connection, post_id: int) -> None:
    post = get_post(database, post_id)
    if not post:
        raise StoreError("Post not found.")

    sync_result = publish_post(dict(post), current_app.config)
    database.execute(
        """
        UPDATE posts
        SET facebook_status = ?, facebook_last_error = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            sync_result.status,
            "" if sync_result.status != "queued" else sync_result.detail,
            local_now().isoformat(),
            post_id,
        ),
    )


def save_contact_message(database: sqlite3.Connection, form_data: dict) -> None:
    database.execute(
        """
        INSERT INTO contact_messages (name, email, phone, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            form_data["name"].strip(),
            form_data["email"].strip(),
            form_data["phone"].strip(),
            form_data["message"].strip(),
            local_now().isoformat(),
        ),
    )


def list_contact_messages(database: sqlite3.Connection):
    return database.execute(
        """
        SELECT *
        FROM contact_messages
        ORDER BY created_at DESC
        """
    ).fetchall()


def place_order(database: sqlite3.Connection, form_data: dict) -> int:
    line_items = normalize_line_items(form_data)
    if not line_items:
        raise StoreError("Choose at least one carton before placing an order.")

    customer_name = form_data["customer_name"].strip()
    email = form_data["email"].strip()
    phone = form_data["phone"].strip()
    payment_method = form_data["payment_method"]
    notes = form_data.get("notes", "").strip()

    if not customer_name or not email or not phone:
        raise StoreError("Name, email, and phone are required.")
    if payment_method not in {"cash", "card"}:
        raise StoreError("Choose either cash or card.")

    pickup_window = next_pickup_window()
    database.execute("BEGIN IMMEDIATE")
    try:
        normalized_items = []
        subtotal_cents = 0
        for item_id, quantity in line_items.items():
            item = get_inventory_item(database, item_id)
            if not item or not item["is_active"]:
                raise StoreError("One of the selected items is no longer available.")
            if int(item["quantity_available"]) < quantity:
                raise StoreError(f"Only {item['quantity_available']} left for {item['name']}.")

            line_total = int(item["price_cents"]) * quantity
            subtotal_cents += line_total
            normalized_items.append(
                {
                    "inventory_item_id": item_id,
                    "inventory_name": item["name"],
                    "quantity": quantity,
                    "unit_price_cents": int(item["price_cents"]),
                    "line_total_cents": line_total,
                }
            )

        fee_cents = card_fee_cents(subtotal_cents) if payment_method == "card" else 0
        total_cents = subtotal_cents + fee_cents
        now = local_now().isoformat()
        payment_status = "reserved" if payment_method == "cash" else "payment_processing"
        cursor = database.execute(
            """
            INSERT INTO orders (
                customer_name, email, phone, payment_method, payment_status, order_status,
                pickup_date, pickup_window, subtotal_cents, fee_cents, total_cents,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_name,
                email,
                phone,
                payment_method,
                payment_status,
                "open",
                pickup_window.starts_at.date().isoformat(),
                f"{pickup_window.time_label} {pickup_window.timezone_label}",
                subtotal_cents,
                fee_cents,
                total_cents,
                notes,
                now,
                now,
            ),
        )
        order_id = cursor.lastrowid

        for item in normalized_items:
            database.execute(
                """
                INSERT INTO order_items (
                    order_id, inventory_item_id, inventory_name, quantity,
                    unit_price_cents, line_total_cents
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item["inventory_item_id"],
                    item["inventory_name"],
                    item["quantity"],
                    item["unit_price_cents"],
                    item["line_total_cents"],
                ),
            )
            database.execute(
                """
                UPDATE inventory_items
                SET quantity_available = quantity_available - ?, updated_at = ?
                WHERE id = ?
                """,
                (item["quantity"], now, item["inventory_item_id"]),
            )
            database.execute(
                """
                INSERT INTO inventory_movements (inventory_item_id, delta, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    item["inventory_item_id"],
                    -item["quantity"],
                    f"Order #{order_id}",
                    now,
                ),
            )

        if payment_method == "card":
            payment_result = create_payment(
                {
                    "id": order_id,
                    "email": email,
                    "pickup_date": pickup_window.starts_at.date().isoformat(),
                    "pickup_window": f"{pickup_window.time_label} {pickup_window.timezone_label}",
                    "fee_cents": fee_cents,
                    "items": normalized_items,
                },
                current_app.config,
            )
            database.execute(
                """
                UPDATE orders
                SET payment_status = ?, stripe_reference = ?, stripe_checkout_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payment_result.payment_status,
                    payment_result.reference,
                    payment_result.checkout_url,
                    now,
                    order_id,
                ),
            )

        database.commit()
        return order_id
    except Exception:
        database.rollback()
        raise


def normalize_line_items(form_data: dict) -> dict[int, int]:
    line_items = {}
    for key, value in form_data.items():
        if not key.startswith("item_"):
            continue
        if not value:
            continue
        quantity = int(value)
        if quantity <= 0:
            continue
        item_id = int(key.split("_", 1)[1])
        line_items[item_id] = quantity
    return line_items


def ensure_unique_slug(database: sqlite3.Connection, slug: str, post_id: int | None) -> str:
    candidate = slug
    suffix = 2
    while True:
        if post_id:
            row = database.execute(
                "SELECT id FROM posts WHERE slug = ? AND id != ?",
                (candidate, post_id),
            ).fetchone()
        else:
            row = database.execute(
                "SELECT id FROM posts WHERE slug = ?",
                (candidate,),
            ).fetchone()
        if not row:
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1
