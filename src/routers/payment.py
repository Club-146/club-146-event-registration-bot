"""Payment router for the 146 Meetup Register Bot."""

import asyncio
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from datetime import datetime
from loguru import logger
from textwrap import dedent

from src.app import App, GraduateType
from src.router import is_admin, commands_menu, get_event_date_display
from src.routers.admin import PaymentInfo
from src.user_interactions import ask_user_raw, ask_user_choice, ask_user_choice_raw
from botspot.utils import send_safe

# Create router
router = Router()
app = App()

# Legacy city code mapping — only used for parsing old-format callback buttons
_LEGACY_CITY_CODES_REVERSE = {
    "MOSCOW": "Москва",
    "PERM": "Пермь",
    "SPB": "Санкт-Петербург",
    "BELGRADE": "Белград",
    "PERM_SUMMER": "Пермь (Летняя встреча 2025)",
}


def parse_payment_callback_data(callback_data: str) -> tuple[int, str, str | None]:
    """
    Parse payment callback data into user_id, event_id, and amount.

    New format: confirm_payment_{user_id}_{event_id}_{amount}
    event_id is a 24-char hex MongoDB ObjectId (no underscores).

    Old format (temporary fallback): confirm_payment_{user_id}_{CITY_CODE}_{amount}

    Returns:
        Tuple of (user_id, event_id, amount_str)
    """
    if not callback_data.startswith(("confirm_payment_", "decline_payment_")):
        raise ValueError("Invalid callback data format")

    # Remove the prefix
    if callback_data.startswith("confirm_payment_"):
        data = callback_data[len("confirm_payment_") :]
    else:
        data = callback_data[len("decline_payment_") :]

    # Split by underscore
    parts = data.split("_")
    if len(parts) < 2:
        raise ValueError("Invalid callback data structure")

    user_id = int(parts[0])

    # New format: {user_id}_{event_id} or {user_id}_{event_id}_{amount}
    # event_id is 24-char hex with no underscores
    if len(parts[1]) == 24 and all(c in "0123456789abcdef" for c in parts[1]):
        event_id = parts[1]
        amount_str = parts[2] if len(parts) >= 3 else None
        return user_id, event_id, amount_str

    # Old format fallback: city codes may contain underscores (e.g. PERM_SUMMER)
    logger.warning(f"Parsing old-format callback data: {callback_data}")
    if len(parts) >= 3:
        try:
            amount_str = parts[-1]
            int(amount_str)
            city_code = "_".join(parts[1:-1])
        except ValueError:
            city_code = "_".join(parts[1:])
            amount_str = None
    else:
        city_code = parts[1]
        amount_str = None

    # Return the city code as-is — callers handle legacy lookup
    return user_id, city_code, amount_str


async def process_payment(
    message: Message,
    state: FSMContext,
    event_id: str,
    graduation_year: int,
    skip_instructions=False,
    graduate_type: str = GraduateType.GRADUATE.value,
    guests: list | None = None,
):
    """Process payment for an event registration"""
    # Check if we have original user information in the state
    # This happens when the function is called from a callback handler
    state_data = await state.get_data()
    user_id = state_data.get("original_user_id")
    username = state_data.get("original_username", "")

    # Ensure user_id is an integer
    if user_id is not None:
        user_id = int(user_id)
    else:
        # Use message.from_user.id as fallback
        user_id = message.from_user.id if message.from_user else None

    # Ensure username is a string
    if username is None:
        username = message.from_user.username or "" if message.from_user else ""

    logger.info(f"Using original user information: ID={user_id}, username={username}")
    assert user_id is not None, "user_id must be resolved before processing payment"

    # Load event from registration
    registration_data = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id}
    )
    event = None
    if registration_data:
        event = await app.get_event_for_registration(registration_data)
        # Get graduate_type from the correct registration
        if "graduate_type" in registration_data:
            graduate_type = registration_data["graduate_type"]

    # Calculate payment amount from event config
    if event:
        regular_amount, discount, discounted_amount, formula_amount = (
            app.calculate_event_payment(event, graduation_year, graduate_type)
        )
    else:
        regular_amount, discount, discounted_amount, formula_amount = 0, 0, 0, 0

    # Get city display name from event or registration
    city = (
        event.get("city", "")
        if event
        else (registration_data.get("target_city", "") if registration_data else "")
    )

    # If guests provided, load from state or registration
    if guests is None:
        if registration_data:
            guests = registration_data.get("guests", [])
    guests = guests or []

    # Calculate guest totals (regular and discounted)
    guest_total = sum(g.get("price", 0) for g in guests)
    guest_total_discounted = sum(
        g.get("price_discounted", g.get("price", 0)) for g in guests
    )
    total_regular_with_guests = regular_amount + guest_total
    total_discounted_with_guests = discounted_amount + guest_total_discounted

    # Only show instructions if not skipped
    if not skip_instructions:
        from botspot.core.dependency_manager import get_dependency_manager

        deps = get_dependency_manager()
        await deps.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        await asyncio.sleep(3)  # 3 second delay

        # Prepare formula text for display
        if event:
            pricing_type = event.get("pricing_type", "formula")
            if pricing_type == "free":
                payment_formula = "за свой счет"
            elif pricing_type == "fixed_by_year":
                payment_formula = "фиксированная сумма по году выпуска"
            elif pricing_type == "formula":
                base = event.get("price_formula_base", 0)
                rate = event.get("price_formula_rate", 0)
                ref_year = event.get("price_formula_reference_year", 2026)
                step = event.get("price_formula_step", 1)
                if step > 1:
                    payment_formula = (
                        f"{base}р + {rate} × (({ref_year} − год выпуска) ÷ {step})"
                    )
                else:
                    payment_formula = f"{base}р + {rate} × ({ref_year} − год выпуска)"
            else:
                payment_formula = "за свой счет"
        else:
            payment_formula = "за свой счет"

        # only display formula if not a friend of school
        if graduate_type != GraduateType.NON_GRADUATE.value:
            payment_msg_part1 = dedent(
                f"""
                💰 Оплата мероприятия

                Для оплаты мероприятия используется следующая формула:

                {city} → {payment_formula}
            """
            )

            # Send part 1
            await send_safe(message.chat.id, payment_msg_part1)

            # Delay between messages
            await asyncio.sleep(5)

        price_label = (
            "Стоимость билета для вас"
            if graduate_type == GraduateType.NON_GRADUATE.value
            else "Стоимость билета для вашего года выпуска"
        )

        # Check early bird from event config
        early_bird_deadline = event.get("early_bird_deadline") if event else None
        early_bird_discount_amount = event.get("early_bird_discount", 0) if event else 0
        today = datetime.now()
        is_early = (
            early_bird_deadline
            and today.date() <= early_bird_deadline.date()
            and early_bird_discount_amount > 0
        )

        if is_early:
            # Format deadline for display
            assert early_bird_deadline is not None
            deadline_display = early_bird_deadline.strftime("%d.%m")
            payment_msg_part2 = dedent(
                f"""
                {price_label}: {regular_amount} руб.

                При ранней регистрации (до {deadline_display}) скидка {early_bird_discount_amount} руб!
                Стоимость билета при ранней регистрации - {discounted_amount} руб.

                Очень ждём вас на весенней встрече! 😊
                """
            )
        else:
            payment_msg_part2 = dedent(
                f"""
                {price_label}: {regular_amount} руб.

                Очень ждём вас на весенней встрече! 😊
                """
            )

        # Send part 2
        await send_safe(message.chat.id, payment_msg_part2)

        # Show guest breakdown if applicable
        if guests:
            guest_msg = f"\n👥 Гости ({len(guests)}):\n"
            for i, g in enumerate(guests, 1):
                guest_msg += f"  {i}. {g['name']} — {g['price']} руб.\n"
            if is_early and total_regular_with_guests != total_discounted_with_guests:
                guest_msg += (
                    f"\n💰 Итого с гостями: {total_regular_with_guests} руб."
                    f"\n💰 При ранней регистрации: {total_discounted_with_guests} руб."
                )
            else:
                guest_msg += f"\n💰 Итого с гостями: {total_regular_with_guests} руб."
            await send_safe(message.chat.id, guest_msg)
            await asyncio.sleep(2)

        # Delay between messages
        await asyncio.sleep(3)

        # Prepare part 3 with payment details
        payment_msg_part3 = dedent(
            f"""
            Реквизиты для оплаты:
            В Сбербанк по номеру телефона
            Номер телефона - {app.settings.payment_phone_number}
            Получатель - {app.settings.payment_name}
            """
        )

        # Send part 3
        await send_safe(message.chat.id, payment_msg_part3)

        # Delay between messages
        await asyncio.sleep(3)

    # Create choices for the user
    choices = {
        "pay_later": "Оплачу позже",
        "too_expensive": "Ой, нет, что-то слишком дорого, я передумал",
    }

    # Wait for response using ask_user_choice_raw (either screenshot or choice)
    # Log payment proof request
    await app.save_event_log(
        "payment_action",
        {
            "action": "request_payment_proof",
            "city": city,
            "amount": discounted_amount,
            "regular_amount": regular_amount,
            "graduate_type": graduate_type,
        },
        user_id,
        username,
    )

    response = await ask_user_choice_raw(
        message.chat.id,
        "Пожалуйста, отправьте скриншот подтверждения оплаты (фото или PDF) или выберите опцию ниже:",
        choices=choices,
        state=state,
        timeout=3600,
    )

    if response is None:
        # No response received
        await send_safe(
            message.chat.id,
            "⏰ Не получен ответ в течение 20 минут. Пожалуйста, используйте команду /pay для оплаты.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Check if response is a string (meaning it's a choice selection)
    if isinstance(response, str):
        if response == "pay_later":
            # User clicked "Pay Later"
            await send_safe(
                message.chat.id,
                "Хорошо! Вы можете оплатить позже, используя команду /pay",
                reply_markup=ReplyKeyboardRemove(),
            )

            # Log to chat log
            await app.log_registration_step(
                user_id=user_id, username=username, step="Нажал 'Оплачу позже'"
            )

            # Log to event logs
            await app.save_event_log(
                "payment_action",
                {
                    "action": "pay_later_selected",
                    "city": city,
                    "amount": discounted_amount,
                    "regular_amount": regular_amount,
                    "graduate_type": graduate_type,
                },
                user_id,
                username,
            )

            # Save payment info with pending status
            await app.save_payment_info(
                user_id,
                event_id=event_id,
                discounted_amount=discounted_amount,
                regular_amount=regular_amount,
                formula_amount=formula_amount,
            )
            return False
        elif response == "too_expensive":
            # User clicked "Too expensive, changed my mind"
            # Log to chat log
            await app.log_registration_step(
                user_id=user_id,
                username=username,
                step="Отказ от оплаты: слишком дорого",
            )

            # Log to event logs
            await app.save_event_log(
                "payment_action",
                {
                    "action": "too_expensive_selected",
                    "city": city,
                    "amount": discounted_amount,
                    "regular_amount": regular_amount,
                    "graduate_type": graduate_type,
                },
                user_id,
                username,
            )

            # Get all user registrations
            registrations = await app.get_user_registrations(user_id)
            # Find the registration for this event
            registration = next(
                (reg for reg in registrations if reg.get("event_id") == event_id), None
            )

            if registration:
                full_name = registration.get("full_name", "Unknown")
                # Delete the registration for this event
                await app.delete_user_registration(user_id, event_id)

                # Log cancellation
                await app.log_registration_canceled(
                    user_id,
                    username,
                    full_name,
                    city,
                )

                await send_safe(
                    message.chat.id,
                    "Понимаем! Ваша регистрация отменена. Если передумаете, вы всегда можете зарегистрироваться снова с помощью команды /start",
                    reply_markup=ReplyKeyboardRemove(),
                )
            else:
                await send_safe(
                    message.chat.id,
                    "Что-то пошло не так. Пожалуйста, используйте команду /cancel_registration для отмены регистрации.",
                    reply_markup=ReplyKeyboardRemove(),
                )

            return False

    # Otherwise, it's a message with photo or document
    # Check if response has photo or document (PDF)
    has_photo = hasattr(response, "photo") and response.photo
    has_pdf = (
        hasattr(response, "document")
        and response.document
        and response.document.mime_type == "application/pdf"
    )

    if has_photo or has_pdf:
        # Log payment proof submission
        await app.save_event_log(
            "payment_action",
            {
                "action": "payment_proof_submitted",
                "city": city,
                "amount": discounted_amount,
                "proof_type": "photo" if has_photo else "pdf",
                "graduate_type": graduate_type,
            },
            user_id,
            username,
        )

        # Save payment info with pending status
        await app.save_payment_info(
            user_id,
            event_id=event_id,
            discounted_amount=discounted_amount,
            regular_amount=regular_amount,
            screenshot_message_id=response.message_id,
            formula_amount=formula_amount,
            username=username,
        )

        # Forward screenshot to events chat (which is used as validation chat)
        try:
            logger.info(
                f"Starting payment proof forwarding for user {user_id}, city: {city}"
            )

            # Get events chat ID from settings
            events_chat_id = app.settings.events_chat_id
            logger.info(f"Events chat ID: {events_chat_id}")

            # Show discounted or regular amount based on early bird
            if discount > 0:
                needs_to_pay = (
                    f"{discounted_amount} руб (без скидки — {regular_amount} руб)"
                )
            else:
                needs_to_pay = f"{regular_amount} руб"

            # Get user info for the message
            user_info = f"👤 Пользователь: {username or ''} (ID: {user_id})\n"
            user_info += f"📍 Город: {city}\n"
            if guests:
                user_info += f"💰 Сумма (регистрант): {needs_to_pay}\n"
                user_info += f"👥 Гости ({len(guests)}):\n"
                for g in guests:
                    user_info += f"  • {g['name']} — {g['price']} руб.\n"
                user_info += f"💰 Итого: {total_regular_with_guests} руб.\n"
            else:
                user_info += f"💰 Сумма к оплате: {needs_to_pay}\n"

            # Get user registration for additional info (use event-specific lookup)
            user_registration = await app.collection.find_one(
                {"user_id": user_id, "event_id": event_id}
            )
            if user_registration:
                user_info += (
                    f"👤 ФИО: {user_registration.get('full_name', 'Неизвестно')}\n"
                )

                # Add graduate type info
                graduate_type = user_registration.get(
                    "graduate_type", GraduateType.GRADUATE.value
                )
                if graduate_type == GraduateType.TEACHER.value:
                    user_info += "👨‍🏫 Статус: Учитель (бесплатно)\n"
                elif graduate_type == GraduateType.NON_GRADUATE.value:
                    user_info += "👥 Статус: Друг школы (не выпускник)\n"
                else:
                    user_info += f"🎓 Выпуск: {user_registration.get('graduation_year', 'Неизвестно')} {user_registration.get('class_letter', '')}\n"

            # Get bot instance
            from botspot.core.dependency_manager import get_dependency_manager

            deps = get_dependency_manager()
            bot = deps.bot
            logger.info(f"Got bot instance: {bot}")

            # Try to parse payment amount from the screenshot/PDF
            logger.info(
                f"Parsing payment info from response: has_photo={has_photo}, has_pdf={has_pdf}"
            )
            payment_info = await parse_payment_info(
                response, bool(has_photo), bool(has_pdf), deps.bot
            )

            # Create validation buttons
            validation_buttons = []

            # Use event_id for callback data (24-char hex, no underscores)
            logger.info(
                f"Creating validation buttons for user {user_id}, city: {city}, event_id: {event_id}"
            )

            # If we successfully parsed a valid amount, show simplified buttons
            if payment_info.is_valid:
                # Add parsed amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"✅ {payment_info.amount} руб. - Подтвердить распознанную сумму",
                            callback_data=f"confirm_payment_{user_id}_{event_id}_{payment_info.amount}",
                        )
                    ]
                )

                # Add custom amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить другую сумму",
                            callback_data=f"confirm_payment_{user_id}_{event_id}_custom",
                        )
                    ]
                )
            else:
                # Add standard buttons for different amounts
                if discount > 0:
                    validation_buttons.append(
                        [
                            InlineKeyboardButton(
                                text=f"✅ {discounted_amount} руб. - Подтвердить оплату со скидкой",
                                callback_data=f"confirm_payment_{user_id}_{event_id}_{discounted_amount}",
                            )
                        ]
                    )

                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"✅ {regular_amount} руб. - Подтвердить оплату",
                            callback_data=f"confirm_payment_{user_id}_{event_id}_{regular_amount}",
                        )
                    ]
                )

                if formula_amount > regular_amount:
                    validation_buttons.append(
                        [
                            InlineKeyboardButton(
                                text=f"✅ {formula_amount} руб. - Подтвердить оплату по формуле",
                                callback_data=f"confirm_payment_{user_id}_{event_id}_{formula_amount}",
                            )
                        ]
                    )

                # Add custom amount button
                validation_buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить другую сумму",
                            callback_data=f"confirm_payment_{user_id}_{event_id}_custom",
                        )
                    ]
                )

            # Add decline button
            validation_buttons.append(
                [
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"decline_payment_{user_id}_{event_id}",
                    )
                ]
            )

            validation_markup = InlineKeyboardMarkup(inline_keyboard=validation_buttons)
            logger.info(
                f"Created validation markup with {len(validation_buttons)} buttons"
            )

            # Send the photo or document with caption containing user info
            if has_photo:
                # Get the photo file_id from the message
                assert response.photo, "has_photo is truthy but response.photo is empty"
                photo = response.photo[-1]  # Get the largest photo
                logger.info(f"Sending photo with file_id: {photo.file_id}")

                # Send the photo with caption
                forwarded_msg = await bot.send_photo(
                    chat_id=events_chat_id,
                    photo=photo.file_id,
                    caption=user_info,
                    reply_markup=validation_markup,
                )
                logger.info(
                    f"Successfully sent photo to validation chat, message_id: {forwarded_msg.message_id}"
                )
            else:  # has_pdf
                # Send the PDF document with caption
                assert response.document is not None, (
                    "has_pdf is truthy but response.document is None"
                )
                logger.info(f"Sending PDF with file_id: {response.document.file_id}")
                forwarded_msg = await bot.send_document(
                    chat_id=events_chat_id,
                    document=response.document.file_id,
                    caption=user_info,
                    reply_markup=validation_markup,
                )
                logger.info(
                    f"Successfully sent PDF to validation chat, message_id: {forwarded_msg.message_id}"
                )

            # Save the screenshot message ID for reference
            await app.save_payment_info(
                user_id,
                event_id=event_id,
                discounted_amount=discounted_amount,
                regular_amount=regular_amount,
                screenshot_message_id=forwarded_msg.message_id,
                formula_amount=formula_amount,
            )

            logger.info(
                f"Payment proof from user {user_id} sent to validation chat with caption"
            )
        except Exception as e:
            logger.error(f"Error forwarding payment proof to validation chat: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            # Re-raise the exception to preserve botspot error handling
            raise

        # Notify user
        await send_safe(
            message.chat.id,
            "Спасибо за подтверждение оплаты! Ваш платеж находится на проверке. Мы уведомим вас, когда он будет подтвержден.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        # No screenshot received
        await send_safe(
            message.chat.id,
            "Хорошо! Вы можете оплатить позже, используя команду /pay",
            reply_markup=ReplyKeyboardRemove(),
        )

        # Save payment info with pending status
        await app.save_payment_info(
            user_id,
            event_id=event_id,
            discounted_amount=discounted_amount,
            regular_amount=regular_amount,
        )

    # Return True if payment was processed (screenshot or PDF received)
    return has_photo or has_pdf


async def parse_payment_info(
    response, has_photo: bool, has_pdf: bool, bot
) -> PaymentInfo:
    from src.routers.admin import extract_payment_from_image

    # Get the file
    if has_photo:
        file_id = response.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        return await extract_payment_from_image(file_bytes.read(), "image/jpeg")
    elif has_pdf:
        assert response.document is not None
        file_id = response.document.file_id
        file = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file.file_path)
        return await extract_payment_from_image(file_bytes.read(), "application/pdf")
    else:
        return PaymentInfo(amount=None, is_valid=False)


# Add payment command handler
@commands_menu.add_command("pay", "Оплатить участие")
@router.message(Command("pay"))
async def pay_handler(message: Message, state: FSMContext):
    """Handle payment for registered users"""
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    # Log the pay command
    await app.save_event_log(
        "command",
        {"command": "/pay", "content": message.text, "chat_type": message.chat.type},
        message.from_user.id,
        message.from_user.username,
    )

    user_id = message.from_user.id

    # Check if user is registered
    registrations = await app.get_user_active_registrations(user_id)

    if not registrations:
        await send_safe(
            message.chat.id,
            "Вы еще не зарегистрированы на встречу. Используйте /start для регистрации.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Filter registrations that require payment using event data
    from src.router import is_event_free

    payment_registrations = []
    for reg in registrations:
        event = await app.get_event_for_registration(reg)
        graduate_type_val = reg.get("graduate_type", GraduateType.GRADUATE.value)
        if not is_event_free(event, graduate_type_val):
            payment_registrations.append(reg)

    if not payment_registrations:
        await send_safe(
            message.chat.id,
            "У вас нет регистраций, требующих оплаты.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # If user has multiple registrations requiring payment, ask which one to pay for
    if len(payment_registrations) > 1:
        choices = {}
        for reg in payment_registrations:
            eid = reg["event_id"]
            city = reg.get("target_city", "")
            event = await app.get_event_for_registration(reg)
            status = reg.get("payment_status", "не оплачено")
            status_emoji = (
                "✅"
                if status == "confirmed"
                else "❌"
                if status == "declined"
                else "⏳"
            )
            date_str = get_event_date_display(event)
            choices[eid] = f"{city} ({date_str}) - {status_emoji} {status}"

        response = await ask_user_choice(
            message.chat.id,
            "У вас несколько регистраций. Для какого города вы хотите оплатить участие?",
            choices=choices,
            state=state,
            timeout=None,
        )

        # Log the payment event choice
        await app.save_event_log(
            "button_click",
            {
                "button": response,
                "context": "payment_event_selection",
                "available_event_ids": list(choices.keys()),
            },
            message.from_user.id,
            message.from_user.username,
        )

        # Find the selected registration
        selected_reg = next(
            (reg for reg in payment_registrations if reg["event_id"] == response),
            None,
        )
    else:
        # Only one registration requiring payment
        selected_reg = payment_registrations[0]

    if selected_reg:
        # Check if user has already seen payment instructions
        # We'll use payment_status to determine this - if it's set, they've seen instructions
        skip_instructions = selected_reg.get("payment_status") is not None

        # Store the original user information in the state
        await state.update_data(
            original_user_id=user_id, original_username=message.from_user.username
        )

        # Get graduate_type if available
        graduate_type = selected_reg.get("graduate_type", GraduateType.GRADUATE.value)

        # Process payment for the selected registration
        await process_payment(
            message,
            state,
            selected_reg["event_id"],
            selected_reg["graduation_year"],
            skip_instructions,
            graduate_type=graduate_type,
        )
    else:
        await send_safe(
            message.chat.id,
            "Произошла ошибка при выборе регистрации. Пожалуйста, попробуйте еще раз.",
            reply_markup=ReplyKeyboardRemove(),
        )


# Define payment states
class PaymentStates(StatesGroup):
    waiting_for_confirm_amount = State()
    waiting_for_decline_reason = State()


# Add callback handlers for payment confirmation/decline buttons
@router.callback_query(lambda c: c.data and c.data.startswith("confirm_payment_"))
async def confirm_payment_callback(callback_query: CallbackQuery, state: FSMContext):
    """Confirm a payment"""
    # Check if any state is set (meaning some operation is in progress)
    current_state = await state.get_state()
    if current_state is not None:
        # If any operation is in progress, ignore this callback
        await callback_query.answer("Дождитесь завершения текущей операции...")
        return

    # Extract user_id, event_id and amount from callback data
    assert callback_query.data is not None
    try:
        user_id, event_id, amount_str = parse_payment_callback_data(callback_query.data)
    except ValueError as e:
        await callback_query.answer(f"Invalid callback data: {e}")
        return

    # Handle legacy city-code format
    if event_id in _LEGACY_CITY_CODES_REVERSE:
        city = _LEGACY_CITY_CODES_REVERSE[event_id]
        logger.warning(f"Legacy callback format, resolved city: {city}")
        # Find all registrations for user+city, prefer non-archived event
        cursor = app.collection.find({"user_id": user_id, "target_city": city})
        candidates = await cursor.to_list(length=None)
        registration = None
        for candidate in candidates:
            evt = await app.get_event_for_registration(candidate)
            if evt and evt.get("status") != "archived":
                registration = candidate
                break
        if not registration and candidates:
            registration = candidates[0]
    else:
        logger.info(
            f"Processing payment confirmation: user_id={user_id}, event_id={event_id}"
        )
        registration = await app.collection.find_one(
            {"user_id": user_id, "event_id": event_id}
        )

    city = registration.get("target_city", "") if registration else ""
    if not registration:
        await callback_query.answer("Registration not found")
        return

    username = registration.get("username", user_id)
    full_name = registration.get("full_name", "Неизвестно")

    # Get graduate type for information
    graduate_type = registration.get("graduate_type", GraduateType.GRADUATE.value)
    graduate_type_info = ""
    if graduate_type == GraduateType.TEACHER.value:
        graduate_type_info = "👨‍🏫 Учитель (бесплатно)"
    elif graduate_type == GraduateType.NON_GRADUATE.value:
        graduate_type_info = "👥 Друг школы (не выпускник)"
    else:
        graduation_year = registration.get("graduation_year", "Неизвестно")
        class_letter = registration.get("class_letter", "")
        graduate_type_info = f"🎓 Выпускник {graduation_year} {class_letter}"

    assert callback_query.message is not None, "callback_query.message is None"
    chat_id = callback_query.message.chat.id

    # Immediately remove inline buttons to prevent duplicate clicks
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        # If buttons are already removed, this is a duplicate click — ignore
        logger.warning(
            f"Duplicate payment confirmation attempt for user {user_id}, event {event_id}"
        )
        await callback_query.answer("Этот платеж уже обрабатывается")
        return

    # Handle different amount cases
    if amount_str == "custom" or not amount_str:
        # Ask for payment amount directly using ask_user_raw
        amount_response = await ask_user_raw(
            chat_id,
            f"Укажите сумму платежа для пользователя {username} ({full_name})\n"
            f"Город: {city}\n"
            f"Статус: {graduate_type_info}",
            state=state,
            timeout=300,
        )

        if amount_response is None or amount_response.text is None:
            await send_safe(chat_id, "Время ожидания истекло. Операция отменена.")
            # Log the timeout event
            logger.warning(
                f"Payment amount input timeout for user {user_id} in city {city}"
            )
            return

        # Try to parse the amount
        try:
            payment_amount = int(amount_response.text)
        except ValueError:
            await send_safe(
                chat_id,
                "Некорректная сумма платежа. Пожалуйста, используйте команду снова.",
            )
            return
    else:
        # Use the amount from callback data
        try:
            payment_amount = int(amount_str)
        except ValueError:
            await callback_query.answer("Invalid amount in callback data")
            return

    # Update payment status
    event_id_for_update = registration.get("event_id", event_id)
    await app.update_payment_status(
        user_id,
        event_id=event_id_for_update,
        status="confirmed",
        payment_amount=payment_amount,
    )

    # Get updated registration with total payment amount
    updated_registration = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id_for_update}
    )
    assert updated_registration is not None, (
        "Registration not found after payment update"
    )
    total_payment = updated_registration.get("payment_amount", payment_amount)

    # Check if this was an additional payment
    is_additional_payment = total_payment != payment_amount

    # Get the discounted amount to check against
    discounted_amount = registration.get("discounted_payment_amount", 0)
    regular_amount = registration.get("regular_payment_amount", 0)

    # Determine the relevant recommendation amount
    # Use discounted amount if a discount was applied (i.e., discounted < regular)
    recommended_amount = (
        discounted_amount
        if discounted_amount and discounted_amount < regular_amount
        else regular_amount
    )

    # Check if the total payment amount is less than the recommended amount
    payment_message = ""

    if is_additional_payment:
        payment_message = f"✅ Ваш дополнительный платеж на сумму {payment_amount} руб. подтвержден!\n"
        payment_message += (
            f"Общая сумма внесенных платежей: {total_payment} руб. Спасибо за оплату."
        )
    else:
        payment_message = f"✅ Ваш платеж для участия во встрече в городе {city} подтвержден! Сумма: {payment_amount} руб. Спасибо за оплату."

    if total_payment < recommended_amount:
        shortfall = recommended_amount - total_payment
        payment_message += f"\n\nОбратите внимание, что ваш общий взнос на {shortfall} руб. меньше рекомендуемой суммы ({recommended_amount} руб.). "
        payment_message += "Если у вас будет возможность, вы можете доплатить эту сумму позже, используя команду /pay."

    # Notify user
    await send_safe(
        user_id,
        payment_message,
    )

    # Update callback message
    if callback_query.message:
        # Get user info for the confirmation message
        user_info = f"{registration.get('username', user_id)} ({registration.get('full_name', 'Неизвестно')})"

        # Update the message text or caption
        if is_additional_payment:
            payment_status = f"✅ ДОПОЛНИТЕЛЬНЫЙ ПЛАТЕЖ ПОДТВЕРЖДЕН\nСумма: {payment_amount} руб.\nВсего оплачено: {total_payment} руб."
        else:
            payment_status = f"✅ ПЛАТЕЖ ПОДТВЕРЖДЕН\nСумма: {payment_amount} руб."

        # Add note about payment being less than recommended if applicable
        if total_payment < recommended_amount:
            payment_status += f"\n⚠️ На {recommended_amount - total_payment} руб. меньше рекомендуемой суммы!"

        # Add payment history if available
        payment_history = updated_registration.get("payment_history", [])
        if len(payment_history) > 1:
            payment_status += "\n\nИстория платежей:"
            for i, payment in enumerate(payment_history):
                payment_status += (
                    f"\n{i + 1}. {payment['amount']} руб. ({payment['timestamp'][:10]})"
                )

        if callback_query.message.caption:  # type: ignore[union-attr]
            caption = callback_query.message.caption  # type: ignore[union-attr]
            new_caption = f"{caption}\n\n{payment_status}"

            # Limit caption length
            if len(new_caption) > 1024:
                new_caption = new_caption[-1024:]

            await callback_query.message.edit_caption(  # type: ignore[union-attr]
                caption=new_caption
            )
        else:
            text = callback_query.message.text or ""  # type: ignore[union-attr]
            new_text = f"{text}\n\n{payment_status} для {user_info}"

            await callback_query.message.edit_text(text=new_text)  # type: ignore[union-attr]

    # Confirm to admin with a brief notification
    await callback_query.answer("Платеж подтвержден")

    # Auto-export to sheets after payment confirmation
    await app.export_registered_users_to_google_sheets()


@router.callback_query(lambda c: c.data and c.data.startswith("decline_payment_"))
async def decline_payment_callback(callback_query: CallbackQuery, state: FSMContext):
    """Ask for decline reason"""
    # Check if any state is set (meaning some operation is in progress)
    current_state = await state.get_state()
    if current_state is not None:
        # If any operation is in progress, ignore this callback
        await callback_query.answer("Дождитесь завершения текущей операции...")
        return

    # Extract user_id and event_id from callback data
    assert callback_query.data is not None
    try:
        user_id, event_id, _ = parse_payment_callback_data(callback_query.data)
    except ValueError as e:
        await callback_query.answer(f"Invalid callback data: {e}")
        return

    # Handle legacy city-code format
    if event_id in _LEGACY_CITY_CODES_REVERSE:
        city = _LEGACY_CITY_CODES_REVERSE[event_id]
        logger.warning(f"Legacy callback format, resolved city: {city}")
        # Find all registrations for user+city, prefer non-archived event
        cursor = app.collection.find({"user_id": user_id, "target_city": city})
        candidates = await cursor.to_list(length=None)
        registration = None
        for candidate in candidates:
            evt = await app.get_event_for_registration(candidate)
            if evt and evt.get("status") != "archived":
                registration = candidate
                break
        if not registration and candidates:
            registration = candidates[0]
        event_id = registration.get("event_id", event_id) if registration else event_id
    else:
        logger.info(
            f"Processing payment decline: user_id={user_id}, event_id={event_id}"
        )

    if not event_id:
        await callback_query.answer("Missing event information")
        return

    # Save data for the next step
    await state.set_state(PaymentStates.waiting_for_decline_reason)
    await state.update_data(
        decline_user_id=user_id,
        decline_event_id=event_id,
        callback_message=callback_query.message,
    )

    # Ask for decline reason by editing the original message
    if callback_query.message:
        # Keep the original caption/text but add a prompt
        if callback_query.message.caption:  # type: ignore[union-attr]
            caption = callback_query.message.caption  # type: ignore[union-attr]
            new_caption = f"{caption}\n\n⚠️ Укажите причину отклонения платежа в ответном сообщении:"

            # Limit caption length
            if len(new_caption) > 1024:
                new_caption = new_caption[-1024:]

            await callback_query.message.edit_caption(  # type: ignore[union-attr]
                caption=new_caption, reply_markup=None
            )
        else:
            text = callback_query.message.text or ""  # type: ignore[union-attr]
            new_text = (
                f"{text}\n\n⚠️ Укажите причину отклонения платежа в ответном сообщении:"
            )

            await callback_query.message.edit_text(text=new_text, reply_markup=None)  # type: ignore[union-attr]
    else:
        # Fallback if message is not available
        await callback_query.answer("Укажите причину отклонения в следующем сообщении")


@router.message(PaymentStates.waiting_for_decline_reason)
async def payment_decline_reason_handler(message: Message, state: FSMContext):
    """Handle payment decline reason"""
    # Check if user is admin
    if not message.from_user or not is_admin(message.from_user):
        return

    # Get data from state
    data = await state.get_data()
    user_id = data.get("decline_user_id")
    event_id = data.get("decline_event_id")
    callback_message = data.get("callback_message")

    if not user_id or not event_id:
        await message.reply("Ошибка: не найдена информация о платеже")
        await state.clear()
        return

    # Get decline reason
    decline_reason = message.text or "Причина не указана"

    # Update payment status
    await app.update_payment_status(
        user_id, event_id=event_id, status="declined", admin_comment=decline_reason
    )

    # Get registration
    registration = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id}
    )
    if not registration:
        await message.reply(f"Регистрация не найдена для пользователя {user_id}")
        await state.clear()
        return

    # Notify user
    city = registration.get("target_city", "") if registration else ""
    await send_safe(
        user_id,
        f"❌ Ваш платеж для участия во встрече в городе {city} отклонен.\n\nПричина: {decline_reason}\n\nПожалуйста, используйте команду /pay для повторной оплаты.",
    )

    # Update the original callback message if available
    if callback_message:
        # Get user info for the decline message
        user_info = f"{registration.get('username', user_id)} ({registration.get('full_name', 'Неизвестно')})"

        try:
            # Update the message text or caption
            if hasattr(callback_message, "caption") and callback_message.caption:
                caption = callback_message.caption
                # Remove the prompt if it exists
                caption = caption.split("\n\n⚠️ Укажите причину")[0]
                new_caption = (
                    f"{caption}\n\n❌ ПЛАТЕЖ ОТКЛОНЕН\nПричина: {decline_reason}"
                )

                # Limit caption length
                if len(new_caption) > 1024:
                    new_caption = new_caption[-1024:]

                await callback_message.edit_caption(
                    caption=new_caption, reply_markup=None
                )
            elif hasattr(callback_message, "text"):
                text = callback_message.text or ""
                # Remove the prompt if it exists
                text = text.split("\n\n⚠️ Укажите причину")[0]
                new_text = f"{text}\n\n❌ ПЛАТЕЖ ОТКЛОНЕН для {user_info}\nПричина: {decline_reason}"

                await callback_message.edit_text(text=new_text, reply_markup=None)
        except Exception as e:
            logger.error(f"Error updating callback message: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            # Re-raise the exception to preserve botspot error handling
            raise

    # Confirm to admin with a brief reply
    await message.reply("❌ Платеж отклонен")

    # Clear state
    await state.clear()
