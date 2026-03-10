from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
import unicodedata


def cents_to_dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def dollars_to_cents(value: str) -> int:
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        raise ValueError("Amount is required.")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid dollar amount.") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def card_fee_cents(subtotal_cents: int) -> int:
    return int(math.floor((subtotal_cents * 0.10) + 0.5))


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return ascii_text or "post"
