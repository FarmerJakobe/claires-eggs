from __future__ import annotations

import math
import re
import unicodedata


def cents_to_dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def card_fee_cents(subtotal_cents: int) -> int:
    return int(math.floor((subtotal_cents * 0.10) + 0.5))


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return ascii_text or "post"
