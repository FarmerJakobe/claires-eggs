from __future__ import annotations

from dataclasses import dataclass

from flask import request

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None


@dataclass
class PaymentResult:
    payment_status: str
    checkout_url: str | None
    reference: str
    detail: str


def create_payment(order: dict, config: dict) -> PaymentResult:
    payment_mode = config.get("PAYMENT_MODE", "demo")

    if payment_mode != "stripe":
        return PaymentResult(
            payment_status="paid_demo",
            checkout_url=None,
            reference=f"DEMO-{order['id']}",
            detail="Card payment completed in local demo mode.",
        )

    if stripe is None:
        raise RuntimeError("Stripe SDK is not installed.")
    if not config.get("STRIPE_SECRET_KEY"):
        raise RuntimeError("Stripe secret key is not configured.")

    stripe.api_key = config["STRIPE_SECRET_KEY"]
    line_items = [
        {
            "price_data": {
                "currency": "usd",
                "product_data": {"name": item["inventory_name"]},
                "unit_amount": item["unit_price_cents"],
            },
            "quantity": item["quantity"],
        }
        for item in order["items"]
    ]

    if order["fee_cents"] > 0:
        line_items.append(
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Card processing fee"},
                    "unit_amount": order["fee_cents"],
                },
                "quantity": 1,
            }
        )

    site_url = (config.get("SITE_URL") or request.url_root.rstrip("/")).rstrip("/")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        success_url=f"{site_url}/orders/{order['id']}/confirmation",
        cancel_url=f"{site_url}/orders/{order['id']}/confirmation?cancelled=1",
        customer_email=order["email"],
        metadata={
            "order_id": str(order["id"]),
            "pickup_date": order["pickup_date"],
            "pickup_window": order["pickup_window"],
        },
    )

    return PaymentResult(
        payment_status="awaiting_payment",
        checkout_url=session.url,
        reference=session.id,
        detail="Stripe Checkout session created.",
    )
