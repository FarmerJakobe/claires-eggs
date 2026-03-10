from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def card_payments_enabled(config: dict) -> bool:
    return config.get("PAYMENT_MODE") == "stripe" and config.get(
        "STRIPE_SECRET_KEY", ""
    ).startswith("sk_live_")


def load_config() -> dict:
    return {
        "SECRET_KEY": os.environ.get("FLASK_SECRET_KEY", "claire-farm-eggs-secret"),
        "ADMIN_PASSWORD": os.environ.get("CLAIRE_ADMIN_PASSWORD", "claire-eggs-demo"),
        "DATABASE_PATH": os.environ.get(
            "DATABASE_PATH", str(INSTANCE_DIR / "claire_eggs.db")
        ),
        "SITE_URL": os.environ.get("SITE_URL", "").rstrip("/"),
        "PAYMENT_MODE": os.environ.get("PAYMENT_MODE", "demo").lower(),
        "STRIPE_SECRET_KEY": os.environ.get("STRIPE_SECRET_KEY", ""),
        "STRIPE_PUBLISHABLE_KEY": os.environ.get("STRIPE_PUBLISHABLE_KEY", ""),
        "FACEBOOK_SYNC_MODE": os.environ.get("FACEBOOK_SYNC_MODE", "demo").lower(),
        "FACEBOOK_GROUP_ID": os.environ.get("FACEBOOK_GROUP_ID", ""),
        "FACEBOOK_ACCESS_TOKEN": os.environ.get("FACEBOOK_ACCESS_TOKEN", ""),
        "PREFERRED_URL_SCHEME": os.environ.get("PREFERRED_URL_SCHEME", "https"),
    }
