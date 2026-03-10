from __future__ import annotations

from dataclasses import dataclass

from flask import request

try:
    import stripe
except ImportError:  # pragma: no cover
    stripe = None


CHECKOUT_STATUS_TO_PAYMENT_STATUS = {
    "paid": "paid_online",
    "unpaid": "awaiting_payment",
    "no_payment_required": "paid_online",
}


@dataclass
class PaymentResult:
    payment_status: str
    checkout_url: str | None
    reference: str
    detail: str


@dataclass
class StripeSessionUpdate:
    order_id: int
    payment_status: str
    stripe_reference: str
    checkout_url: str | None
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

    stripe_client = require_stripe_client(config)
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
    session = stripe_client.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        client_reference_id=str(order["id"]),
        success_url=(
            f"{site_url}/orders/{order['id']}/confirmation"
            "?session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=f"{site_url}/orders/{order['id']}/confirmation?cancelled=1",
        customer_email=order["email"],
        metadata={
            "order_id": str(order["id"]),
            "pickup_date": order["pickup_date"],
            "pickup_window": order["pickup_window"],
        },
        payment_intent_data={"metadata": {"order_id": str(order["id"])}},
    )

    return PaymentResult(
        payment_status="awaiting_payment",
        checkout_url=session.url,
        reference=session.id,
        detail="Stripe Checkout session created.",
    )


def refresh_payment_from_session(session_id: str, config: dict) -> StripeSessionUpdate | None:
    if not session_id or config.get("PAYMENT_MODE") != "stripe":
        return None

    stripe_client = require_stripe_client(config)
    session = stripe_client.checkout.Session.retrieve(session_id)
    return build_session_update(session)


def parse_stripe_webhook(payload: bytes, signature: str, config: dict) -> StripeSessionUpdate | None:
    if config.get("PAYMENT_MODE") != "stripe":
        return None

    stripe_client = require_stripe_client(config)
    webhook_secret = config.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise RuntimeError("Stripe webhook secret is not configured.")

    event = stripe_client.Webhook.construct_event(payload, signature, webhook_secret)
    if event["type"] not in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }:
        return None

    session = event["data"]["object"]
    update = build_session_update(session)
    if not update:
        return None

    if event["type"] == "checkout.session.async_payment_failed":
        update.payment_status = "payment_failed"
        update.detail = "Stripe reported an asynchronous payment failure."
    elif event["type"] == "checkout.session.expired":
        update.payment_status = "checkout_expired"
        update.detail = "Stripe Checkout session expired."
    elif event["type"] == "checkout.session.async_payment_succeeded":
        update.payment_status = "paid_online"
        update.detail = "Stripe confirmed asynchronous payment success."

    return update


def require_stripe_client(config: dict):
    if stripe is None:
        raise RuntimeError("Stripe SDK is not installed.")
    if not config.get("STRIPE_SECRET_KEY"):
        raise RuntimeError("Stripe secret key is not configured.")
    stripe.api_key = config["STRIPE_SECRET_KEY"]
    return stripe


def build_session_update(session) -> StripeSessionUpdate | None:
    order_id = session.get("client_reference_id") or session.get("metadata", {}).get("order_id")
    if not order_id:
        return None

    payment_status = CHECKOUT_STATUS_TO_PAYMENT_STATUS.get(
        session.get("payment_status", ""), "awaiting_payment"
    )
    return StripeSessionUpdate(
        order_id=int(order_id),
        payment_status=payment_status,
        stripe_reference=session.get("payment_intent") or session.get("id") or "",
        checkout_url=session.get("url"),
        detail=f"Stripe session {session.get('id', '')} mapped to {payment_status}.",
    )
