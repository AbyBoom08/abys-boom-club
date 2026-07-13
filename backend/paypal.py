import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_BASE_URL = os.getenv(
    "PAYPAL_BASE_URL",
    "https://api-m.sandbox.paypal.com",
)
PAYPAL_PLAN_ID = os.getenv("PAYPAL_PLAN_ID")
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")


def get_paypal_access_token() -> str:
    """Obtiene un token OAuth temporal de PayPal."""

    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError(
            "Faltan PAYPAL_CLIENT_ID o PAYPAL_CLIENT_SECRET en .env"
        )

    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(
            PAYPAL_CLIENT_ID,
            PAYPAL_CLIENT_SECRET,
        ),
        data={
            "grant_type": "client_credentials",
        },
        headers={
            "Accept": "application/json",
            "Accept-Language": "es_ES",
        },
        timeout=20,
    )

    response.raise_for_status()

    access_token = response.json().get("access_token")

    if not access_token:
        raise RuntimeError(
            "PayPal no devolvió un access_token."
        )

    return access_token


def paypal_headers(
    access_token: str,
    include_request_id: bool = True,
) -> dict[str, str]:
    """Crea los encabezados para llamar a la API de PayPal."""

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }

    if include_request_id:
        headers["PayPal-Request-Id"] = str(uuid.uuid4())

    return headers


def create_paypal_product() -> dict:
    """Crea el producto de membresía en PayPal."""

    access_token = get_paypal_access_token()

    payload = {
        "name": "Abys Boom Club VIP",
        "description": (
            "Acceso mensual al grupo VIP y a su contenido exclusivo."
        ),
        "type": "SERVICE",
    }

    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/catalogs/products",
        headers=paypal_headers(access_token),
        json=payload,
        timeout=20,
    )

    response.raise_for_status()
    return response.json()


def create_paypal_plan(product_id: str) -> dict:
    """Crea el plan mensual de 20 USD."""

    if not product_id:
        raise ValueError(
            "El product_id es obligatorio."
        )

    access_token = get_paypal_access_token()

    payload = {
        "product_id": product_id,
        "name": "Membresía VIP mensual",
        "description": "Suscripción mensual a Abys Boom Club VIP.",
        "status": "ACTIVE",
        "billing_cycles": [
            {
                "frequency": {
                    "interval_unit": "MONTH",
                    "interval_count": 1,
                },
                "tenure_type": "REGULAR",
                "sequence": 1,
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {
                        "value": "20.00",
                        "currency_code": "USD",
                    }
                },
            }
        ],
        "payment_preferences": {
            "auto_bill_outstanding": True,
            "setup_fee": {
                "value": "0.00",
                "currency_code": "USD",
            },
            "setup_fee_failure_action": "CONTINUE",
            "payment_failure_threshold": 1,
        },
    }

    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/billing/plans",
        headers=paypal_headers(access_token),
        json=payload,
        timeout=20,
    )

    response.raise_for_status()
    return response.json()


def create_product_and_plan() -> dict:
    """Crea el producto y el plan de PayPal."""

    product = create_paypal_product()
    plan = create_paypal_plan(product["id"])

    return {
        "product_id": product["id"],
        "product_name": product.get("name"),
        "plan_id": plan["id"],
        "plan_name": plan.get("name"),
        "plan_status": plan.get("status"),
        "price": "20.00",
        "currency": "USD",
        "frequency": "MONTH",
    }


def create_paypal_subscription(
    telegram_id: int,
) -> dict:
    """Crea una suscripción personal para un usuario de Telegram."""

    if not PAYPAL_PLAN_ID:
        raise RuntimeError(
            "Falta PAYPAL_PLAN_ID en el archivo .env"
        )

    if not TELEGRAM_BOT_USERNAME:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_USERNAME en el archivo .env"
        )

    access_token = get_paypal_access_token()

    bot_url = (
        f"https://t.me/{TELEGRAM_BOT_USERNAME}"
        "?start=paypal_return"
    )

    payload = {
        "plan_id": PAYPAL_PLAN_ID,
        "custom_id": str(telegram_id),
        "application_context": {
            "brand_name": "Abys Boom Club",
            "locale": "es-ES",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "SUBSCRIBE_NOW",
            "return_url": bot_url,
            "cancel_url": (
                f"https://t.me/{TELEGRAM_BOT_USERNAME}"
                "?start=paypal_cancel"
            ),
        },
    }

    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/billing/subscriptions",
        headers=paypal_headers(access_token),
        json=payload,
        timeout=20,
    )

    response.raise_for_status()

    subscription = response.json()

    approval_url = next(
        (
            link["href"]
            for link in subscription.get("links", [])
            if link.get("rel") == "approve"
        ),
        None,
    )

    if not approval_url:
        raise RuntimeError(
            "PayPal creó la suscripción, pero no devolvió "
            "el enlace de aprobación."
        )

    return {
        "subscription_id": subscription["id"],
        "status": subscription.get("status"),
        "approval_url": approval_url,
    }


def get_paypal_subscription(
    subscription_id: str,
) -> dict:
    """Obtiene los datos actuales de una suscripción."""

    if not subscription_id:
        raise ValueError(
            "El subscription_id es obligatorio."
        )

    access_token = get_paypal_access_token()

    response = requests.get(
        (
            f"{PAYPAL_BASE_URL}"
            f"/v1/billing/subscriptions/{subscription_id}"
        ),
        headers=paypal_headers(
            access_token,
            include_request_id=False,
        ),
        timeout=20,
    )

    response.raise_for_status()
    return response.json()


def cancel_paypal_subscription(
    subscription_id: str,
    reason: str = (
        "El usuario solicitó cancelar la renovación automática."
    ),
) -> None:
    """
    Cancela futuros cobros de una suscripción.

    PayPal normalmente responde con HTTP 204 sin contenido.
    """

    if not subscription_id:
        raise ValueError(
            "El subscription_id es obligatorio."
        )

    access_token = get_paypal_access_token()

    response = requests.post(
        (
            f"{PAYPAL_BASE_URL}"
            f"/v1/billing/subscriptions/{subscription_id}/cancel"
        ),
        headers=paypal_headers(access_token),
        json={
            "reason": reason,
        },
        timeout=20,
    )

    response.raise_for_status()


def verify_paypal_webhook(
    headers: dict[str, str],
    event: dict,
) -> bool:
    """Verifica con PayPal la firma del webhook recibido."""

    if not PAYPAL_WEBHOOK_ID:
        raise RuntimeError(
            "Falta PAYPAL_WEBHOOK_ID en el archivo .env"
        )

    required_headers = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get(
            "paypal-transmission-id"
        ),
        "transmission_sig": headers.get(
            "paypal-transmission-sig"
        ),
        "transmission_time": headers.get(
            "paypal-transmission-time"
        ),
    }

    missing = [
        name
        for name, value in required_headers.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Faltan encabezados de PayPal: "
            + ", ".join(missing)
        )

    access_token = get_paypal_access_token()

    payload = {
        **required_headers,
        "webhook_id": PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }

    response = requests.post(
        (
            f"{PAYPAL_BASE_URL}"
            "/v1/notifications/verify-webhook-signature"
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    response.raise_for_status()

    return (
        response.json().get("verification_status")
        == "SUCCESS"
    )