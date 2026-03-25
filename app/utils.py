from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
import unicodedata

DELTA_AND_MONTROSE_ZIP_CODES = frozenset(
    {
        "81220",
        "81401",
        "81402",
        "81403",
        "81410",
        "81411",
        "81413",
        "81414",
        "81415",
        "81416",
        "81418",
        "81419",
        "81420",
        "81421",
        "81422",
        "81424",
        "81425",
        "81428",
        "81429",
        "81431",
    }
)


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


def normalize_zip_code(value: str) -> str:
    zip_code = value.strip()
    if not re.fullmatch(r"\d{5}", zip_code):
        raise ValueError("Enter a valid 5-digit ZIP code.")
    return zip_code


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return ascii_text or "post"
