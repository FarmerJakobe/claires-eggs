from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from flask import current_app, g

from .schedule import local_now
from .utils import slugify


SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    unit_label TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    quantity_available INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_item_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    zip_code TEXT NOT NULL,
    payment_method TEXT NOT NULL,
    payment_status TEXT NOT NULL,
    order_status TEXT NOT NULL,
    pickup_date TEXT NOT NULL,
    pickup_window TEXT NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    fee_cents INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stripe_reference TEXT,
    stripe_checkout_url TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    inventory_item_id INTEGER NOT NULL,
    inventory_name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    line_total_cents INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (inventory_item_id) REFERENCES inventory_items(id)
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    excerpt TEXT NOT NULL,
    body TEXT NOT NULL,
    is_published INTEGER NOT NULL DEFAULT 0,
    publish_to_facebook INTEGER NOT NULL DEFAULT 0,
    facebook_status TEXT NOT NULL DEFAULT 'not-requested',
    facebook_message TEXT NOT NULL DEFAULT '',
    facebook_last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS contact_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_token TEXT NOT NULL,
    path TEXT NOT NULL,
    visit_date TEXT NOT NULL,
    visited_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visitor_events_visit_date
ON visitor_events(visit_date);

CREATE INDEX IF NOT EXISTS idx_visitor_events_path
ON visitor_events(path);

CREATE TABLE IF NOT EXISTS sales_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date TEXT NOT NULL,
    title TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    payment_method TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sales_entries_sale_date
ON sales_entries(sale_date);

CREATE TABLE IF NOT EXISTS expense_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_date TEXT NOT NULL,
    vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    receipt_original_name TEXT,
    receipt_stored_name TEXT,
    receipt_content_type TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expense_receipts_expense_date
ON expense_receipts(expense_date);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE_PATH"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_: Any = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


def init_db() -> None:
    database = get_db()
    database.executescript(SCHEMA)
    ensure_orders_columns(database)
    ensure_posts_columns(database)
    seed_demo_data(database)
    database.commit()


def seed_demo_data(database: sqlite3.Connection) -> None:
    inventory_exists = database.execute(
        "SELECT COUNT(*) AS count FROM inventory_items"
    ).fetchone()["count"]
    post_exists = database.execute("SELECT COUNT(*) AS count FROM posts").fetchone()[
        "count"
    ]

    if not inventory_exists:
        now = local_now().isoformat()
        items = [
            (
                "Classic Brown Eggs",
                "Fresh brown eggs from free-ranging hens, packed by the dozen.",
                "dozen",
                650,
                18,
                1,
                1,
                now,
                now,
            ),
            (
                "Rainbow Carton",
                "A colorful dozen with blue, green, and speckled shells.",
                "dozen",
                750,
                10,
                1,
                2,
                now,
                now,
            ),
            (
                "Baking Half-Dozen",
                "A smaller carton for weeknight baking or a quick breakfast plan.",
                "half-dozen",
                350,
                12,
                1,
                3,
                now,
                now,
            ),
        ]
        database.executemany(
            """
            INSERT INTO inventory_items (
                name, description, unit_label, price_cents, quantity_available,
                is_active, display_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            items,
        )

    if not post_exists:
        now = local_now().isoformat()
        title = "Fresh eggs every Wednesday in Crawford"
        database.execute(
            """
            INSERT INTO posts (
                title, slug, excerpt, body, is_published, publish_to_facebook,
                facebook_status, facebook_message, created_at, updated_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                slugify(title),
                "Pickup is every Wednesday from 3 PM to 4 PM at the Hitching Post in Crawford.",
                (
                    "Claire gathers eggs through the week and brings the freshest cartons "
                    "to town every Wednesday afternoon. Reserve online, choose cash or card, "
                    "and pick up your order at the Hitching Post in Crawford between 3 PM and 4 PM."
                ),
                1,
                1,
                "simulated",
                "Fresh eggs are available this Wednesday at the Hitching Post from 3 PM to 4 PM.",
                now,
                now,
                now,
            ),
        )


def ensure_posts_columns(database: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in database.execute("PRAGMA table_info(posts)").fetchall()
    }
    missing_columns = {
        "image_original_name": "TEXT",
        "image_stored_name": "TEXT",
        "image_content_type": "TEXT",
    }

    for column_name, column_type in missing_columns.items():
        if column_name in columns:
            continue
        database.execute(
            f"ALTER TABLE posts ADD COLUMN {column_name} {column_type}"
        )


def ensure_orders_columns(database: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in database.execute("PRAGMA table_info(orders)").fetchall()
    }

    if "zip_code" not in columns:
        database.execute(
            "ALTER TABLE orders ADD COLUMN zip_code TEXT NOT NULL DEFAULT ''"
        )
