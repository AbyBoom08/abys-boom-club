from datetime import datetime, timezone

import requests
from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend import models
from backend.database import Base, engine, get_db
from backend.paypal import (
    cancel_paypal_subscription,
    create_paypal_subscription,
    create_product_and_plan,
    get_paypal_access_token,
    get_paypal_subscription,
    verify_paypal_webhook,
)
from backend.schemas import UserCreate, UserResponse


app = FastAPI(
    title="Abys Boom Club API",
    description="Backend oficial del sistema VIP",
    version="1.0.0",
)

Base.metadata.create_all(bind=engine)


def parse_paypal_datetime(
    value: str | None,
) -> datetime | None:
    """Convierte una fecha ISO de PayPal a datetime."""

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed

    except ValueError:
        return None


def normalize_datetime(
    value: datetime | None,
) -> datetime | None:
    """Garantiza que la fecha tenga zona horaria UTC."""

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def user_has_remaining_access(
    user: models.User,
) -> bool:
    """
    Indica si el periodo previamente pagado todavía no ha terminado.
    """

    expiration = normalize_datetime(
        user.subscription_expires_at
    )

    if expiration is None:
        return False

    return expiration > datetime.now(timezone.utc)


def update_expiration_from_subscription(
    user: models.User,
    subscription: dict,
) -> datetime | None:
    """Guarda la próxima fecha de cobro como fin del periodo pagado."""

    next_billing_time = (
        subscription
        .get("billing_info", {})
        .get("next_billing_time")
    )

    parsed = parse_paypal_datetime(
        next_billing_time
    )

    if parsed is not None:
        user.subscription_expires_at = parsed

    return parsed


@app.get("/")
async def home():
    return {
        "status": "online",
        "message": "Backend funcionando correctamente",
    }


@app.get("/health")
async def health():
    return {
        "server": "ok",
    }


@app.get("/database-test")
def database_test():
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    "SELECT current_database(), current_user"
                )
            ).one()

        return {
            "status": "connected",
            "database": result[0],
            "user": result[1],
        }

    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error conectando con PostgreSQL: {error}",
        ) from error


@app.get("/paypal-test")
def paypal_test():
    try:
        access_token = get_paypal_access_token()

        return {
            "status": "connected",
            "message": "Credenciales de PayPal válidas",
            "token_received": bool(access_token),
        }

    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    except requests.RequestException as error:
        response_text = ""

        if error.response is not None:
            response_text = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "PayPal rechazó la conexión. "
                f"{error}. Respuesta: {response_text}"
            ),
        ) from error


@app.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    existing_user = db.scalar(
        select(models.User).where(
            models.User.telegram_id
            == user_data.telegram_id
        )
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este usuario de Telegram ya está registrado.",
        )

    user = models.User(
        telegram_id=user_data.telegram_id,
        username=user_data.username,
        first_name=user_data.first_name,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@app.get(
    "/users/{telegram_id}",
    response_model=UserResponse,
)
def get_user(
    telegram_id: int,
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(models.User).where(
            models.User.telegram_id
            == telegram_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado.",
        )

    return user


@app.post("/paypal/setup")
def paypal_setup():
    """
    Crea el producto y plan de PayPal.

    Solo debe ejecutarse una vez por entorno.
    """

    try:
        result = create_product_and_plan()

        return {
            "status": "created",
            "message": (
                "Producto y plan mensual creados correctamente."
            ),
            **result,
        }

    except (RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    except requests.RequestException as error:
        paypal_response = ""

        if error.response is not None:
            paypal_response = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "No se pudo crear el producto o plan en PayPal. "
                f"{paypal_response or str(error)}"
            ),
        ) from error


@app.post("/paypal/subscriptions/{telegram_id}")
def create_subscription(
    telegram_id: int,
    db: Session = Depends(get_db),
):
    """Crea una suscripción para un usuario."""

    user = db.scalar(
        select(models.User).where(
            models.User.telegram_id == telegram_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario de Telegram no encontrado.",
        )

    if user.subscription_active and user_has_remaining_access(user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya tienes acceso durante el periodo pagado actual. "
                "No necesitas volver a pagar."
            ),
        )

    try:
        paypal_result = create_paypal_subscription(
            telegram_id=telegram_id,
        )

    except (RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    except requests.RequestException as error:
        paypal_response = ""

        if error.response is not None:
            paypal_response = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "PayPal no pudo crear la suscripción. "
                f"{paypal_response or str(error)}"
            ),
        ) from error

    user.paypal_subscription_id = (
        paypal_result["subscription_id"]
    )
    user.subscription_active = False
    user.subscription_expires_at = None

    db.commit()
    db.refresh(user)

    return {
        "status": "created",
        "subscription_id": paypal_result["subscription_id"],
        "paypal_status": paypal_result["status"],
        "approval_url": paypal_result["approval_url"],
    }


@app.post("/paypal/check-subscription/{telegram_id}")
def check_paypal_subscription(
    telegram_id: int,
    db: Session = Depends(get_db),
):
    """
    Consulta directamente el estado de la suscripción en PayPal.
    """

    user = db.scalar(
        select(models.User).where(
            models.User.telegram_id == telegram_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario de Telegram no encontrado.",
        )

    if not user.paypal_subscription_id:
        user.subscription_active = False
        db.commit()

        return {
            "subscription_active": False,
            "paypal_status": None,
            "subscription_expires_at": None,
            "message": "El usuario todavía no tiene una suscripción.",
        }

    try:
        subscription = get_paypal_subscription(
            user.paypal_subscription_id
        )

    except requests.RequestException as error:
        paypal_response = ""

        if error.response is not None:
            paypal_response = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "No se pudo consultar la suscripción en PayPal. "
                f"{paypal_response or str(error)}"
            ),
        ) from error

    custom_id = subscription.get("custom_id")

    if custom_id and str(custom_id) != str(telegram_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La suscripción de PayPal no corresponde "
                "a este usuario."
            ),
        )

    paypal_status = subscription.get("status", "")

    update_expiration_from_subscription(
        user,
        subscription,
    )

    if paypal_status == "ACTIVE":
        user.subscription_active = True

    elif paypal_status == "CANCELLED":
        user.subscription_active = user_has_remaining_access(user)

    else:
        user.subscription_active = False

    db.commit()
    db.refresh(user)

    return {
        "subscription_active": user.subscription_active,
        "paypal_status": paypal_status,
        "subscription_expires_at": user.subscription_expires_at,
        "message": (
            "Suscripción activa."
            if user.subscription_active
            else "La suscripción no está activa."
        ),
    }


@app.post("/paypal/cancel-subscription/{telegram_id}")
def cancel_subscription(
    telegram_id: int,
    db: Session = Depends(get_db),
):
    """
    Cancela futuros cobros, conservando el acceso ya pagado.
    """

    user = db.scalar(
        select(models.User).where(
            models.User.telegram_id == telegram_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario de Telegram no encontrado.",
        )

    if not user.paypal_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No tienes una suscripción de PayPal para cancelar.",
        )

    try:
        subscription = get_paypal_subscription(
            user.paypal_subscription_id
        )

    except requests.RequestException as error:
        paypal_response = ""

        if error.response is not None:
            paypal_response = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "No se pudo consultar la suscripción en PayPal. "
                f"{paypal_response or str(error)}"
            ),
        ) from error

    custom_id = subscription.get("custom_id")

    if custom_id and str(custom_id) != str(telegram_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La suscripción de PayPal no corresponde "
                "a este usuario."
            ),
        )

    paypal_status = subscription.get("status", "")

    update_expiration_from_subscription(
        user,
        subscription,
    )

    if paypal_status == "CANCELLED":
        user.subscription_active = user_has_remaining_access(user)

        db.commit()
        db.refresh(user)

        return {
            "status": "already_cancelled",
            "message": (
                "La renovación automática ya estaba cancelada."
            ),
            "access_until": user.subscription_expires_at,
            "subscription_active": user.subscription_active,
        }

    if paypal_status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "La suscripción no puede cancelarse porque "
                f"su estado actual es {paypal_status or 'DESCONOCIDO'}."
            ),
        )

    if user.subscription_expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "PayPal no devolvió la fecha final del periodo pagado. "
                "No se realizó la cancelación para evitar quitarte "
                "el acceso antes de tiempo."
            ),
        )

    try:
        cancel_paypal_subscription(
            user.paypal_subscription_id
        )

    except (requests.RequestException, ValueError) as error:
        paypal_response = ""

        if (
            isinstance(error, requests.RequestException)
            and error.response is not None
        ):
            paypal_response = error.response.text

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "PayPal no pudo cancelar la renovación. "
                f"{paypal_response or str(error)}"
            ),
        ) from error

    user.subscription_active = user_has_remaining_access(user)

    db.commit()
    db.refresh(user)

    return {
        "status": "cancelled",
        "message": (
            "La renovación automática fue cancelada correctamente."
        ),
        "access_until": user.subscription_expires_at,
        "subscription_active": user.subscription_active,
    }


@app.post("/paypal/webhook")
async def paypal_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Recibe y procesa los eventos enviados por PayPal."""

    event = await request.json()

    try:
        signature_valid = verify_paypal_webhook(
            headers=dict(request.headers),
            event=event,
        )

    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No se pudo verificar el webhook: {error}",
        ) from error

    if not signature_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firma de PayPal inválida.",
        )

    event_type = event.get("event_type", "")
    resource = event.get("resource", {})

    subscription_id = resource.get("id")

    if event_type == "PAYMENT.SALE.COMPLETED":
        subscription_id = resource.get(
            "billing_agreement_id"
        )

    if not subscription_id:
        return {
            "status": "ignored",
            "reason": "El evento no contiene una suscripción.",
        }

    try:
        subscription = get_paypal_subscription(
            subscription_id
        )

    except requests.RequestException as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "No se pudo consultar la suscripción en PayPal: "
                f"{error}"
            ),
        ) from error

    custom_id = subscription.get("custom_id")
    paypal_status = subscription.get("status", "")

    if not custom_id:
        return {
            "status": "ignored",
            "reason": "La suscripción no contiene custom_id.",
        }

    try:
        telegram_id = int(custom_id)
    except (TypeError, ValueError):
        return {
            "status": "ignored",
            "reason": "custom_id no contiene un Telegram ID válido.",
        }

    user = db.scalar(
        select(models.User).where(
            models.User.telegram_id == telegram_id
        )
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario de Telegram no encontrado.",
        )

    update_expiration_from_subscription(
        user,
        subscription,
    )

    active_events = {
        "BILLING.SUBSCRIPTION.ACTIVATED",
        "BILLING.SUBSCRIPTION.RE-ACTIVATED",
        "PAYMENT.SALE.COMPLETED",
    }

    immediately_inactive_events = {
        "BILLING.SUBSCRIPTION.EXPIRED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
        "PAYMENT.SALE.DENIED",
    }

    if event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        # La cancelación detiene renovaciones, pero no elimina
        # el tiempo que el cliente ya pagó.
        user.subscription_active = user_has_remaining_access(user)

    elif event_type in immediately_inactive_events:
        user.subscription_active = False

    elif event_type in active_events or paypal_status == "ACTIVE":
        user.subscription_active = True
        user.paypal_subscription_id = subscription_id

    elif paypal_status == "CANCELLED":
        user.subscription_active = user_has_remaining_access(user)

    db.commit()
    db.refresh(user)

    return {
        "status": "processed",
        "event_type": event_type,
        "paypal_status": paypal_status,
        "telegram_id": telegram_id,
        "subscription_active": user.subscription_active,
        "subscription_expires_at": user.subscription_expires_at,
    }

@app.post("/subscriptions/process-expired")
def process_expired_subscriptions(
    db: Session = Depends(get_db),
):
    """
    Busca suscripciones cuyo periodo pagado ya terminó,
    las marca como inactivas y devuelve sus Telegram IDs.
    """

    now = datetime.now(timezone.utc)

    expired_users = db.scalars(
        select(models.User).where(
            models.User.subscription_active.is_(True),
            models.User.subscription_expires_at.is_not(None),
            models.User.subscription_expires_at <= now,
        )
    ).all()

    if not expired_users:
        return {
            "status": "completed",
            "processed_count": 0,
            "expired_users": [],
            "message": "No hay suscripciones vencidas.",
        }

    expired_telegram_ids: list[int] = []

    for user in expired_users:
        user.subscription_active = False
        expired_telegram_ids.append(user.telegram_id)

    db.commit()

    return {
        "status": "completed",
        "processed_count": len(expired_telegram_ids),
        "expired_users": expired_telegram_ids,
        "message": (
            "Las suscripciones vencidas fueron "
            "marcadas como inactivas."
        ),
    }