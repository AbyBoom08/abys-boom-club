        )

    except (requests.RequestException, RuntimeError) as error:
        logger.exception(
            "No se pudo registrar el usuario."
        )

        await update.message.reply_text(
            f"⚠️ No pude iniciar tu cuenta:\n{error}"
        )
        return

    await update.message.reply_text(
        f"Hola, {user.first_name} 👋\n\n"
        "Bienvenido a Abys Boom Club.\n\n"
        "Selecciona una de las siguientes opciones:\n\n"
        f"Versión: {BOT_CODE_VERSION}",
        reply_markup=main_menu(),
    )


async def handle_normal_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not user or not update.message:
        return

    try:
        user_data = await asyncio.to_thread(
            get_user,
            user.id,
        )

    except requests.RequestException:
        await update.message.reply_text(
            "⚠️ No pude conectar con el servidor."
        )
        return

    if not user_data:
        await update.message.reply_text(
            "👋 Para comenzar, escribe:\n\n/start"
        )
        return

    await update.message.reply_text(
        "Selecciona una opción:",
        reply_markup=main_menu(),
    )


async def handle_unknown_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await handle_normal_message(update, context)


async def handle_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query or not query.message:
        return

    if await user_is_spamming(query.from_user.id):
        await query.answer(
            "⏳ Espera 5 segundos antes de volver a intentarlo.",
            show_alert=True,
        )
        return

    await query.answer()

    if query.data == "buy_access":
        waiting_message = await query.message.reply_text(
            "🔎 Comprobando tu membresía..."
        )

        try:
            user_data = await asyncio.to_thread(
                get_user,
                query.from_user.id,
            )

            logger.info(
                "BUY_ACCESS version=%s telegram_id=%s user_data=%r",
                BOT_CODE_VERSION,
                query.from_user.id,
                user_data,
            )

            if user_data and subscription_is_current(user_data):
                await waiting_message.edit_text(
                    active_membership_message(user_data),
                    reply_markup=request_access_keyboard(),
                )
                return

            await waiting_message.edit_text(
                "⏳ Preparando tu enlace personal de PayPal..."
            )

            subscription = await asyncio.to_thread(
                create_subscription,
                query.from_user.id,
            )

        except (requests.RequestException, RuntimeError) as error:
            logger.exception(
                "Error preparando la suscripción."
            )

            await waiting_message.edit_text(
                f"⚠️ {error}"
            )
            return

        payment_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💳 Pagar 20 USD con PayPal",
                        url=subscription["approval_url"],
                    )
                ]
            ]
        )

        await waiting_message.edit_text(
            "✅ Tu enlace de suscripción está listo.\n\n"
            "La membresía cuesta 20 USD al mes.\n"
            "Pulsa el botón para continuar en PayPal.\n\n"
            "Cuando termines, regresa al bot y pulsa "
            "«Solicitar acceso».",
            reply_markup=payment_keyboard,
        )
        return

    if query.data == "request_access":
        checking_message = await query.message.reply_text(
            "🔎 Comprobando tu acceso..."
        )

        try:
            user_data = await asyncio.to_thread(
                get_user,
                query.from_user.id,
            )

            if user_data and subscription_is_current(user_data):
                subscription_data = user_data
            else:
                subscription_data = await asyncio.to_thread(
                    check_user_subscription,
                    query.from_user.id,
                )

        except (requests.RequestException, RuntimeError) as error:
            await checking_message.edit_text(
                f"⚠️ {error}"
            )
            return

        if subscription_data.get("subscription_active"):
            access_keyboard = vip_join_keyboard()

            if not access_keyboard:
                await checking_message.edit_text(
                    "⚠️ El enlace del grupo no está configurado."
                )
                return

            await checking_message.edit_text(
                "✅ Pago confirmado.\n\n"
                "Tu suscripción está activa.\n"
                "Pulsa el botón para solicitar entrada al grupo VIP.",
                reply_markup=access_keyboard,
            )
            return

        await checking_message.edit_text(
            "❌ Acceso denegado.\n\n"
            "No tienes una suscripción activa.\n\n"
            "Pulsa «Comprar acceso» para suscribirte.",
            reply_markup=buy_access_keyboard(),
        )
        return

    if query.data == "cancel_subscription":
        try:
            user_data = await asyncio.to_thread(
                get_user,
                query.from_user.id,
            )

        except requests.RequestException:
            await query.message.reply_text(
                "⚠️ No pude consultar tu suscripción."
            )
            return

        if not user_data or not (
            user_data.get("subscription_active")
            or user_data.get("paypal_subscription_id")
        ):
            await query.message.reply_text(
                "❌ No tienes una suscripción para cancelar.",
                reply_markup=buy_access_keyboard(),
            )
            return

        expiration_text = format_expiration_date(
            user_data.get("subscription_expires_at")
        )

        if expiration_text:
            access_message = (
                f"Conservarás tu acceso hasta el "
                f"{expiration_text}."
            )
        else:
            access_message = (
                "Conservarás el acceso hasta terminar "
                "el periodo que ya pagaste."
            )

        await query.message.reply_text(
            "⚠️ ¿Quieres cancelar la renovación automática?\n\n"
            "No se realizarán más cobros mensuales.\n"
            f"{access_message}",
            reply_markup=cancellation_confirmation_keyboard(),
        )
        return

    if query.data == "confirm_cancel_subscription":
        waiting_message = await query.message.reply_text(
            "⏳ Cancelando tu renovación automática..."
        )

        try:
            result = await asyncio.to_thread(
                cancel_user_subscription,
                query.from_user.id,
            )

        except (requests.RequestException, RuntimeError) as error:
            logger.exception(
                "No se pudo cancelar la suscripción."
            )

            await waiting_message.edit_text(
                f"⚠️ No se pudo cancelar la suscripción:\n{error}"
            )
            return

        expiration_text = format_expiration_date(
            result.get("access_until")
        )

        if expiration_text:
            access_message = (
                f"Podrás seguir dentro del grupo VIP hasta "
                f"el {expiration_text}."
            )
        else:
            access_message = (
                "Podrás seguir dentro del grupo VIP hasta "
                "terminar el periodo que ya pagaste."
            )

        if result.get("status") == "already_cancelled":
            title = "ℹ️ La renovación ya estaba cancelada."
        else:
            title = "✅ Renovación automática cancelada."

        await waiting_message.edit_text(
            f"{title}\n\n"
            "PayPal no realizará nuevos cobros mensuales.\n"
            f"{access_message}\n\n"
            "No serás retirado inmediatamente del grupo."
        )
        return

    if query.data == "keep_subscription":
        await query.message.edit_text(
            "✅ Tu suscripción continúa activa.\n\n"
            "No hicimos ningún cambio y los cobros mensuales "
            "seguirán normalmente.",
            reply_markup=main_menu(),
        )
        return


async def handle_join_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    join_request = update.chat_join_request

    if not join_request:
        return

    user = join_request.from_user
    chat = join_request.chat

    if (
        TELEGRAM_VIP_CHAT_ID is not None
        and chat.id != TELEGRAM_VIP_CHAT_ID
    ):
        return

    try:
        user_data = await asyncio.to_thread(
            get_user,
            user.id,
        )

        if user_data and subscription_is_current(user_data):
            subscription_data = user_data
        else:
            subscription_data = await asyncio.to_thread(
                check_user_subscription,
                user.id,
            )

    except (requests.RequestException, RuntimeError):
        await context.bot.decline_chat_join_request(
            chat_id=chat.id,
            user_id=user.id,
        )
        return

    if subscription_data.get("subscription_active"):
        await context.bot.approve_chat_join_request(
            chat_id=chat.id,
            user_id=user.id,
        )

        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "✅ ¡Acceso aprobado!\n\n"
                    "Ya puedes disfrutar del grupo VIP."
                ),
            )
        except Exception:
            logger.exception(
                "No se pudo avisar al usuario aprobado."
            )

        return

    await context.bot.decline_chat_join_request(
        chat_id=chat.id,
        user_id=user.id,
    )

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "❌ Acceso denegado.\n\n"
                "No tienes una suscripción activa."
            ),
            reply_markup=buy_access_keyboard(),
        )
    except Exception:
        logger.exception(
            "No se pudo avisar al usuario rechazado."
        )



async def remove_expired_member(
    application: Application,
    telegram_id: int,
) -> bool:
    """
    Expulsa al usuario y lo desbloquea inmediatamente para que
    pueda solicitar entrada otra vez si vuelve a pagar.
    """

    if TELEGRAM_VIP_CHAT_ID is None:
        logger.error(
            "No se puede expulsar usuarios porque "
            "TELEGRAM_VIP_CHAT_ID no está configurado."
        )
        return False

    try:
        await application.bot.ban_chat_member(
            chat_id=TELEGRAM_VIP_CHAT_ID,
            user_id=telegram_id,
        )

        await application.bot.unban_chat_member(
            chat_id=TELEGRAM_VIP_CHAT_ID,
            user_id=telegram_id,
            only_if_banned=True,
        )

        logger.info(
            "Usuario %s retirado del grupo VIP por vencimiento.",
            telegram_id,
        )

        try:
            await application.bot.send_message(
                chat_id=telegram_id,
                text=(
                    "⌛ Tu periodo de acceso al grupo VIP terminó.\n\n"
                    "Fuiste retirado automáticamente del grupo. "
                    "Puedes volver a comprar una membresía desde el bot."
                ),
                reply_markup=buy_access_keyboard(),
            )
        except TelegramError:
            logger.warning(
                "El usuario %s fue retirado, pero no se le pudo "
                "enviar el aviso privado.",
                telegram_id,
            )

        return True

    except BadRequest as error:
        error_text = str(error).lower()

        if (
            "user not found" in error_text
            or "participant_id_invalid" in error_text
            or "not enough rights" in error_text
        ):
            logger.warning(
                "No se pudo retirar al usuario %s: %s",
                telegram_id,
                error,
            )
        else:
            logger.exception(
                "Telegram rechazó la expulsión del usuario %s.",
                telegram_id,
            )

    except Forbidden:
        logger.exception(
            "El bot no tiene permiso para expulsar usuarios "
            "del grupo VIP."
        )

    except TelegramError:
        logger.exception(
            "Error de Telegram expulsando al usuario %s.",
            telegram_id,
        )

    return False


async def expired_subscriptions_loop(
    application: Application,
) -> None:
    """Revisa periódicamente las suscripciones vencidas."""

    await asyncio.sleep(5)

    while True:
        try:
            result = await asyncio.to_thread(
                process_expired_subscriptions
            )

            expired_users = result.get("expired_users", [])

            if expired_users:
                logger.info(
                    "El backend encontró %s suscripción(es) vencida(s).",
                    len(expired_users),
                )

            for telegram_id in expired_users:
                try:
                    parsed_telegram_id = int(telegram_id)
                except (TypeError, ValueError):
                    logger.error(
                        "Telegram ID inválido recibido del backend: %r",
                        telegram_id,
                    )
                    continue

                await remove_expired_member(
                    application,
                    parsed_telegram_id,
                )

        except asyncio.CancelledError:
            logger.info(
                "Revisión automática de vencimientos detenida."
            )
            raise

        except (requests.RequestException, RuntimeError):
            logger.exception(
                "No se pudo revisar las suscripciones vencidas."
            )

        except Exception:
            logger.exception(
                "Error inesperado en la revisión de vencimientos."
            )

        await asyncio.sleep(
            EXPIRED_CHECK_INTERVAL_SECONDS
        )


async def post_init(
    application: Application,
) -> None:
    """Inicia la revisión automática al arrancar el bot."""

    if TELEGRAM_VIP_CHAT_ID is None:
        logger.warning(
            "La expulsión automática está desactivada porque "
            "TELEGRAM_VIP_CHAT_ID no está configurado."
        )
        return

    application.bot_data["expired_subscriptions_task"] = (
        asyncio.create_task(
            expired_subscriptions_loop(application)
        )
    )

    logger.info(
        "Revisión automática de vencimientos iniciada cada %s segundos.",
        EXPIRED_CHECK_INTERVAL_SECONDS,
    )


async def post_shutdown(
    application: Application,
) -> None:
    """Detiene limpiamente la tarea automática."""

    task = application.bot_data.get(
        "expired_subscriptions_task"
    )

    if task:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass




def create_telegram_application(
    include_lifecycle_hooks: bool = False,
) -> Application:
    """Construye la aplicación de Telegram y registra sus handlers."""

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "No se encontró TELEGRAM_BOT_TOKEN en las variables de entorno."
        )

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN)

    if include_lifecycle_hooks:
        builder = (
            builder
            .post_init(post_init)
            .post_shutdown(post_shutdown)
        )

    application = builder.build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_button))

    if TELEGRAM_VIP_CHAT_ID is not None:
        application.add_handler(
            ChatJoinRequestHandler(
                handle_join_request,
                chat_id=TELEGRAM_VIP_CHAT_ID,
            )
        )
    else:
        application.add_handler(
            ChatJoinRequestHandler(handle_join_request)
        )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_normal_message,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            handle_unknown_command,
        )
    )

    return application


# Una sola instancia compartida con FastAPI.
telegram_application = create_telegram_application()


async def start_telegram_application(webhook_url: str) -> None:
    """Inicia el bot y registra en Telegram la URL del webhook."""

    if telegram_application.running:
        return

    await telegram_application.initialize()
    await telegram_application.start()
    await post_init(telegram_application)

    await telegram_application.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )

    logger.info("Webhook de Telegram configurado: %s", webhook_url)


async def stop_telegram_application() -> None:
    """Detiene limpiamente el bot cuando Render reinicia el servicio."""

    if not telegram_application.running:
        return

    await post_shutdown(telegram_application)
    await telegram_application.stop()
    await telegram_application.shutdown()


async def process_telegram_webhook(payload: dict) -> None:
    """Convierte el JSON recibido en un Update y lo procesa."""

    update = Update.de_json(
        payload,
        telegram_application.bot,
    )
    await telegram_application.process_update(update)


def run_bot() -> None:
    """Modo local opcional. En Render se utiliza el webhook de FastAPI."""

    local_application = create_telegram_application(
        include_lifecycle_hooks=True
    )

    print(f"Bot local iniciado. Versión: {BOT_CODE_VERSION}")
    print(f"Archivo ejecutado: {os.path.abspath(__file__)}")
    print(f"Backend usado: {BACKEND_URL}")

    local_application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    run_bot()
