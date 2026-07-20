"""Payment router for the 146 Events Register Bot."""

import asyncio
from html import escape
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
from src import templates
from src.app import App, GraduateType
from src.router import is_admin, commands_menu, get_event_date_display
from src.routers.admin import PaymentInfo
from src.ticket_cards import send_paid_ticket_card
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


async def _resolve_user_identity(
    message: Message, state: FSMContext
) -> tuple[int, str]:
    state_data = await state.get_data()
    user_id = state_data.get("original_user_id")
    username = state_data.get("original_username", "")

    if user_id is not None:
        user_id = int(user_id)
    else:
        user_id = message.from_user.id if message.from_user else None

    if username is None:
        username = message.from_user.username or "" if message.from_user else ""

    logger.info(f"Using original user information: ID={user_id}, username={username}")
    assert user_id is not None, "user_id must be resolved before processing payment"
    return user_id, username


async def _load_registration_and_event(user_id: int, event_id: str):
    registration_data = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id}
    )
    event = None
    if registration_data:
        event = await app.get_event_for_registration(registration_data)
    return registration_data, event


def _get_payment_amounts(event, graduation_year: int, graduate_type: str):
    if event:
        return app.calculate_event_payment(event, graduation_year, graduate_type)
    return 0, 0, 0, 0


def _get_city(event, registration_data) -> str:
    if event:
        return event.get("city", "")
    if registration_data:
        return registration_data.get("target_city", "")
    return ""


def _get_guests(registration_data, guests) -> list:
    if guests is None and registration_data:
        guests = registration_data.get("guests", [])
    return guests or []


def _calc_guest_totals(guests: list, regular_amount: int, discounted_amount: int):
    guest_total = sum(g.get("price", 0) for g in guests)
    guest_total_discounted = sum(
        g.get("price_discounted", g.get("price", 0)) for g in guests
    )
    return (
        regular_amount + guest_total,
        discounted_amount + guest_total_discounted,
    )


def _build_payment_formula(event) -> str:
    if not event:
        return "за свой счет"
    pricing_type = event.get("pricing_type", "formula")
    if pricing_type == "free":
        return "за свой счет"
    if pricing_type == "fixed_by_year":
        return "фиксированная сумма по году выпуска"
    if pricing_type == "formula":
        base = event.get("price_formula_base", 0)
        rate = event.get("price_formula_rate", 0)
        ref_year = event.get("price_formula_reference_year", 2026)
        step = event.get("price_formula_step", 1)
        if step > 1:
            return f"{base}р + {rate} × (({ref_year} − год выпуска) ÷ {step})"
        return f"{base}р + {rate} × ({ref_year} − год выпуска)"
    return "за свой счет"


def _season_adjective(event) -> str:
    """Season adjective for 'на ... встрече', derived from the event date."""
    date = event.get("date") if event else None
    if not date:
        return "ближайшей"
    return {
        12: "зимней",
        1: "зимней",
        2: "зимней",
        3: "весенней",
        4: "весенней",
        5: "весенней",
        6: "летней",
        7: "летней",
        8: "летней",
        9: "осенней",
        10: "осенней",
        11: "осенней",
    }[date.month]


def _check_early_bird(event) -> tuple[bool, object, int]:
    early_bird_deadline = event.get("early_bird_deadline") if event else None
    early_bird_discount_amount = event.get("early_bird_discount", 0) if event else 0
    today = datetime.now()
    is_early = (
        early_bird_deadline
        and today.date() <= early_bird_deadline.date()
        and early_bird_discount_amount > 0
    )
    return bool(is_early), early_bird_deadline, early_bird_discount_amount


async def _send_payment_info_messages(
    message: Message,
    city: str,
    event,
    graduate_type: str,
    regular_amount: int,
    discounted_amount: int,
    guests: list,
    total_regular_with_guests: int,
    total_discounted_with_guests: int,
    full_name: str = "",
    graduation_year: int | str | None = None,
):
    """Send payment info in two user-visible messages (price+guests, then how to pay)."""
    from botspot.core.dependency_manager import get_dependency_manager
    from src.pay_url import build_pay_url

    deps = get_dependency_manager()
    await deps.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    await asyncio.sleep(1)

    payment_formula = _build_payment_formula(event)
    chunks: list[str] = []

    if graduate_type != GraduateType.NON_GRADUATE.value:
        chunks.append(
            templates.render(
                event, "payment_intro", {"city": city, "formula": payment_formula}
            ).strip()
        )

    price_label = (
        "Минимальный взнос для вас"
        if graduate_type == GraduateType.NON_GRADUATE.value
        else "Минимальный взнос для вашего года выпуска"
    )

    is_early, early_bird_deadline, early_bird_discount_amount = _check_early_bird(event)
    season = _season_adjective(event)

    if is_early:
        assert early_bird_deadline is not None
        deadline_display = early_bird_deadline.strftime("%d.%m")
        chunks.append(
            templates.render(
                event,
                "payment_price_early",
                {
                    "price_label": price_label,
                    "regular_amount": regular_amount,
                    "deadline": deadline_display,
                    "discount": early_bird_discount_amount,
                    "discounted_amount": discounted_amount,
                    "season": season,
                },
            ).strip()
        )
    else:
        chunks.append(
            templates.render(
                event,
                "payment_price_regular",
                {
                    "price_label": price_label,
                    "regular_amount": regular_amount,
                    "season": season,
                },
            ).strip()
        )

    if guests:
        guest_msg = f"👥 Гости ({len(guests)}):\n"
        for i, g in enumerate(guests, 1):
            guest_name = escape(str(g["name"]), quote=True)
            guest_msg += f"  {i}. {guest_name} — {g['price']} руб.\n"
        if is_early and total_regular_with_guests != total_discounted_with_guests:
            guest_msg += (
                f"\n💰 Итого с гостями: {total_regular_with_guests} руб."
                f"\n💰 <b>При ранней регистрации: {total_discounted_with_guests} руб.</b>"
            )
        else:
            guest_msg += (
                f"\n💰 <b>Итого с гостями: {total_regular_with_guests} руб.</b>"
            )
        chunks.append(guest_msg.strip())

    await send_safe(message.chat.id, "\n\n".join(chunks))
    await asyncio.sleep(1)

    # Same total the user is told to pay (early-bird + guests when applicable).
    pay_amount = total_discounted_with_guests if is_early else total_regular_with_guests
    pay_url = build_pay_url(
        app.settings.payment_site_base_url,
        pay_amount,
        full_name=full_name or "",
        graduation_year=graduation_year,
    )
    payment_msg_part3 = templates.render(
        event,
        "payment_details",
        {
            "pay_url": pay_url,
            "phone": app.settings.payment_phone_number,
            "name": app.settings.payment_name,
        },
    )
    await send_safe(message.chat.id, payment_msg_part3)
    await asyncio.sleep(1)


async def _handle_pay_later(
    message: Message,
    user_id: int,
    username: str,
    city: str,
    event_id: str,
    discounted_amount: int,
    regular_amount: int,
    formula_amount: int,
    graduate_type: str,
    event: dict | None = None,
):
    from src.payment_timeline import pay_later_message

    if event is None:
        event = await app.get_event_by_id(event_id) or {}

    await send_safe(
        message.chat.id,
        pay_later_message(event),
        reply_markup=ReplyKeyboardRemove(),
    )
    await app.log_registration_step(
        user_id=user_id, username=username, step="Нажал 'Оплачу позже'"
    )
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
    await app.save_payment_info(
        user_id,
        event_id=event_id,
        discounted_amount=discounted_amount,
        regular_amount=regular_amount,
        formula_amount=formula_amount,
        payment_status="not paid",
    )
    # Mark for reminder job (not yet reminded).
    try:
        coll = app.collection
        update = coll.update_one(
            {"user_id": user_id, "event_id": event_id},
            {
                "$set": {
                    "pay_later_selected": True,
                    "payment_reminder_d4_sent": False,
                    "payment_reminder_d2_sent": False,
                }
            },
        )
        if hasattr(update, "__await__"):
            await update
    except Exception as e:
        logger.warning(f"Could not set pay_later flags for {user_id}: {e}")


async def _handle_too_expensive(
    message: Message,
    user_id: int,
    username: str,
    city: str,
    event_id: str,
    discounted_amount: int,
    regular_amount: int,
    graduate_type: str,
    state: FSMContext | None = None,
):
    from src.payment_timeline import too_expensive_cancel_message

    await app.log_registration_step(
        user_id=user_id,
        username=username,
        step="Отказ от оплаты: слишком дорого",
    )
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

    registrations = await app.get_user_registrations(user_id)
    registration = next(
        (reg for reg in registrations if reg.get("event_id") == event_id), None
    )

    if registration:
        full_name = registration.get("full_name", "Unknown")
        # Soft-delete keeps the row in deleted_users so /start can reuse profile.
        await app.delete_user_registration(
            user_id, event_id, username=username, full_name=full_name
        )
        await app.log_registration_canceled(user_id, username, full_name, city)
        await send_safe(
            message.chat.id,
            too_expensive_cancel_message(),
            reply_markup=ReplyKeyboardRemove(),
        )
        if state is not None:
            interest = await ask_user_choice(
                message.chat.id,
                "Хотите, чтобы мы отметили интерес к волонтёрству "
                "(Мария @mariikors свяжется / учтёт)?",
                choices={
                    "volunteer_yes": "Да, интересно волонтёрство",
                    "volunteer_no": "Нет, спасибо",
                },
                state=state,
                timeout=300,
            )
            if interest == "volunteer_yes":
                await app.save_event_log(
                    "payment_action",
                    {
                        "action": "volunteer_interest_after_too_expensive",
                        "city": city,
                        "full_name": full_name,
                        "amount": discounted_amount,
                    },
                    user_id,
                    username,
                )
                try:
                    await app.log_to_chat(
                        f"🙋 Волонтёрский интерес (после «дорого»)\n"
                        f"{full_name} (@{username or '—'})\n"
                        f"user_id={user_id} · {city} · взнос был {discounted_amount}₽\n"
                        f"→ @mariikors",
                        "events",
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not log volunteer interest to events chat: {e}"
                    )
                await send_safe(
                    message.chat.id,
                    "Отметили интерес. Напишите @mariikors — она решает "
                    "скидку / бесплатный вход / задачи.",
                )
    else:
        await send_safe(
            message.chat.id,
            "Что-то пошло не так. Пожалуйста, используйте команду /cancel_registration для отмены регистрации.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def _handle_paid_await_proof(
    message: Message,
    state: FSMContext,
    user_id: int,
    username: str,
    city: str,
    event_id: str,
    guests: list,
    discount: int,
    discounted_amount: int,
    regular_amount: int,
    formula_amount: int,
    graduate_type: str,
    payment_method: str,
) -> bool:
    """User said they already paid (site or Maria) — ask for screenshot/PDF proof.

    payment_method: "on_site" | "to_maria" (matches choice keys paid_on_site / paid_to_maria).
    """
    method_label = "сайт" if payment_method == "on_site" else "Маша"
    await app.log_registration_step(
        user_id=user_id,
        username=username,
        step=f"Оплатил(а) ({method_label}) — ждём скриншот",
    )
    await app.save_event_log(
        "payment_action",
        {
            "action": f"paid_{payment_method}_selected",
            "city": city,
            "amount": discounted_amount,
            "regular_amount": regular_amount,
            "graduate_type": graduate_type,
            "payment_method": payment_method,
        },
        user_id,
        username,
    )

    if payment_method == "on_site":
        proof_prompt = (
            "Пожалуйста, отправьте скриншот или PDF подтверждения оплаты "
            "(транзакции с сайта)."
        )
    else:
        proof_prompt = (
            "Пожалуйста, отправьте скриншот или PDF подтверждения перевода Маше."
        )

    proof_response = await ask_user_raw(
        message.chat.id,
        proof_prompt,
        state=state,
        timeout=3600,
    )

    if proof_response is None:
        await send_safe(
            message.chat.id,
            "⏰ Не получен ответ в течение часа. Пожалуйста, используйте команду /pay "
            "и пришлите скриншот или PDF подтверждения оплаты.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return False

    return await _handle_screenshot_upload(
        message,
        proof_response,
        user_id,
        username,
        city,
        event_id,
        guests,
        discount,
        discounted_amount,
        regular_amount,
        formula_amount,
        graduate_type,
    )


def _build_user_info_text(
    user_id: int,
    username: str,
    city: str,
    guests: list,
    needs_to_pay: str,
    total_regular_with_guests: int,
    user_registration,
    graduate_type: str,
) -> str:
    user_info = f"👤 Пользователь: {username or ''} (ID: {user_id})\n"
    user_info += f"📍 Город: {city}\n"
    if guests:
        user_info += f"💰 Сумма (регистрант): {needs_to_pay}\n"
        user_info += f"👥 Гости ({len(guests)}):\n"
        for g in guests:
            guest_name = escape(str(g["name"]), quote=True)
            user_info += f"  • {guest_name} — {g['price']} руб.\n"
        user_info += f"💰 Итого: {total_regular_with_guests} руб.\n"
    else:
        user_info += f"💰 Сумма к оплате: {needs_to_pay}\n"

    if user_registration:
        user_info += f"👤 ФИО: {user_registration.get('full_name', 'Неизвестно')}\n"
        reg_graduate_type = user_registration.get(
            "graduate_type", GraduateType.GRADUATE.value
        )
        if reg_graduate_type == GraduateType.TEACHER.value:
            user_info += "👨‍🏫 Статус: Учитель (бесплатно)\n"
        elif reg_graduate_type == GraduateType.NON_GRADUATE.value:
            user_info += "👥 Статус: Друг школы (не выпускник)\n"
        else:
            user_info += f"🎓 Выпуск: {user_registration.get('graduation_year', 'Неизвестно')} {user_registration.get('class_letter', '')}\n"

    return user_info


def _build_validation_buttons(
    user_id: int,
    event_id: str,
    payment_info: PaymentInfo,
    discount: int,
    discounted_amount: int,
    regular_amount: int,
    formula_amount: int,
) -> list:
    validation_buttons = []

    if payment_info.is_valid:
        validation_buttons.append(
            [
                InlineKeyboardButton(
                    text=f"✅ {payment_info.amount} руб. - Подтвердить распознанную сумму",
                    callback_data=f"confirm_payment_{user_id}_{event_id}_{payment_info.amount}",
                )
            ]
        )
        validation_buttons.append(
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить другую сумму",
                    callback_data=f"confirm_payment_{user_id}_{event_id}_custom",
                )
            ]
        )
    else:
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

        validation_buttons.append(
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить другую сумму",
                    callback_data=f"confirm_payment_{user_id}_{event_id}_custom",
                )
            ]
        )

    validation_buttons.append(
        [
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"decline_payment_{user_id}_{event_id}",
            )
        ]
    )
    return validation_buttons


async def _forward_proof_to_events_chat(
    response,
    has_photo: bool,
    user_id: int,
    event_id: str,
    city: str,
    guests: list,
    discount: int,
    discounted_amount: int,
    regular_amount: int,
    formula_amount: int,
    username: str,
) -> None:
    logger.info(f"Starting payment proof forwarding for user {user_id}, city: {city}")
    events_chat_id = app.settings.events_chat_id
    logger.info(f"Events chat ID: {events_chat_id}")

    if discount > 0:
        needs_to_pay = f"{discounted_amount} руб (без скидки — {regular_amount} руб)"
    else:
        needs_to_pay = f"{regular_amount} руб"

    # Calc guest totals for display
    guest_total = sum(g.get("price", 0) for g in guests)
    total_regular_with_guests = regular_amount + guest_total

    user_registration = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id}
    )
    # graduate_type only needed for user_info string (visual purposes)
    graduate_type = (
        user_registration.get("graduate_type", GraduateType.GRADUATE.value)
        if user_registration
        else GraduateType.GRADUATE.value
    )

    user_info = _build_user_info_text(
        user_id,
        username,
        city,
        guests,
        needs_to_pay,
        total_regular_with_guests,
        user_registration,
        graduate_type,
    )

    from botspot.core.dependency_manager import get_dependency_manager

    deps = get_dependency_manager()
    bot = deps.bot
    logger.info(f"Got bot instance: {bot}")

    has_pdf = not has_photo
    logger.info(
        f"Parsing payment info from response: has_photo={has_photo}, has_pdf={has_pdf}"
    )
    payment_info = await parse_payment_info(response, has_photo, has_pdf, bot)

    logger.info(
        f"Creating validation buttons for user {user_id}, city: {city}, event_id: {event_id}"
    )
    validation_buttons = _build_validation_buttons(
        user_id,
        event_id,
        payment_info,
        discount,
        discounted_amount,
        regular_amount,
        formula_amount,
    )
    validation_markup = InlineKeyboardMarkup(inline_keyboard=validation_buttons)
    logger.info(f"Created validation markup with {len(validation_buttons)} buttons")

    if has_photo:
        assert response.photo, "has_photo is truthy but response.photo is empty"
        photo = response.photo[-1]
        logger.info(f"Sending photo with file_id: {photo.file_id}")
        forwarded_msg = await bot.send_photo(
            chat_id=events_chat_id,
            photo=photo.file_id,
            caption=user_info,
            reply_markup=validation_markup,
        )
        logger.info(
            f"Successfully sent photo to validation chat, message_id: {forwarded_msg.message_id}"
        )
    else:
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

    await app.save_payment_info(
        user_id,
        event_id=event_id,
        discounted_amount=discounted_amount,
        regular_amount=regular_amount,
        screenshot_message_id=forwarded_msg.message_id,
        formula_amount=formula_amount,
        payment_status="pending",
    )
    logger.info(
        f"Payment proof from user {user_id} sent to validation chat with caption"
    )


async def _handle_screenshot_upload(
    message: Message,
    response,
    user_id: int,
    username: str,
    city: str,
    event_id: str,
    guests: list,
    discount: int,
    discounted_amount: int,
    regular_amount: int,
    formula_amount: int,
    graduate_type: str,
) -> bool:
    has_photo = hasattr(response, "photo") and response.photo
    has_pdf = (
        hasattr(response, "document")
        and response.document
        and response.document.mime_type == "application/pdf"
    )

    if not (has_photo or has_pdf):
        from src.payment_timeline import pay_later_message

        event = await app.get_event_by_id(event_id) or {}
        await send_safe(
            message.chat.id,
            pay_later_message(event),
            reply_markup=ReplyKeyboardRemove(),
        )
        await app.save_payment_info(
            user_id,
            event_id=event_id,
            discounted_amount=discounted_amount,
            regular_amount=regular_amount,
            payment_status="not paid",
        )
        return False

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

    await app.save_payment_info(
        user_id,
        event_id=event_id,
        discounted_amount=discounted_amount,
        regular_amount=regular_amount,
        screenshot_message_id=response.message_id,
        formula_amount=formula_amount,
        username=username,
        payment_status="pending",
    )

    try:
        await _forward_proof_to_events_chat(
            response,
            bool(has_photo),
            user_id,
            event_id,
            city,
            guests,
            discount,
            discounted_amount,
            regular_amount,
            formula_amount,
            username,
        )
    except Exception as e:
        logger.error(f"Error forwarding payment proof to validation chat: {e}")
        logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
        raise

    await send_safe(
        message.chat.id,
        "Спасибо за подтверждение оплаты! Ваш платеж находится на проверке. Мы уведомим вас, когда он будет подтвержден.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return True


async def process_payment(
    message: Message,
    state: FSMContext,
    event_id: str,
    graduation_year: int,
    skip_instructions=False,
    graduate_type: str = GraduateType.GRADUATE.value,
    guests: list | None = None,
    pre_uploaded_response: Message | None = None,
):
    """Process payment for an event registration.

    If pre_uploaded_response is provided (photo/PDF already sent by user),
    skip the prompt and process it directly as payment proof.
    """
    user_id, username = await _resolve_user_identity(message, state)

    registration_data, event = await _load_registration_and_event(user_id, event_id)
    if registration_data and "graduate_type" in registration_data:
        graduate_type = registration_data["graduate_type"]

    regular_amount, discount, discounted_amount, formula_amount = _get_payment_amounts(
        event, graduation_year, graduate_type
    )
    city = _get_city(event, registration_data)
    guests = _get_guests(registration_data, guests)
    total_regular_with_guests, total_discounted_with_guests = _calc_guest_totals(
        guests, regular_amount, discounted_amount
    )

    # If user already sent a photo/PDF, skip prompts and process directly
    if pre_uploaded_response is not None:
        await app.save_event_log(
            "payment_action",
            {
                "action": "auto_payment_proof",
                "city": city,
                "amount": discounted_amount,
                "regular_amount": regular_amount,
                "graduate_type": graduate_type,
            },
            user_id,
            username,
        )
        return await _handle_screenshot_upload(
            message,
            pre_uploaded_response,
            user_id,
            username,
            city,
            event_id,
            guests,
            discount,
            discounted_amount,
            regular_amount,
            formula_amount,
            graduate_type,
        )

    if not skip_instructions:
        full_name = (registration_data or {}).get("full_name", "") or ""
        await _send_payment_info_messages(
            message,
            city,
            event,
            graduate_type,
            regular_amount,
            discounted_amount,
            guests,
            total_regular_with_guests,
            total_discounted_with_guests,
            full_name=full_name,
            graduation_year=graduation_year,
        )

    # 1–2: already paid → ask for proof; 3–4: defer / cancel (existing).
    choices = {
        "paid_on_site": "Оплатил(а) на сайте",
        "paid_to_maria": "Оплатил(а) Маше",
        "pay_later": "Оплачу позже",
        "too_expensive": "Ой, нет, что-то слишком дорого, я передумал",
    }

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
        "Выберите опцию ниже — или сразу отправьте скриншот/PDF подтверждения оплаты:",
        choices=choices,
        state=state,
        timeout=3600,
    )

    if response is None:
        await send_safe(
            message.chat.id,
            "⏰ Не получен ответ в течение часа. Пожалуйста, используйте команду /pay для оплаты.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return False

    if isinstance(response, str):
        if response == "paid_on_site":
            return await _handle_paid_await_proof(
                message,
                state,
                user_id,
                username,
                city,
                event_id,
                guests,
                discount,
                discounted_amount,
                regular_amount,
                formula_amount,
                graduate_type,
                payment_method="on_site",
            )
        if response == "paid_to_maria":
            return await _handle_paid_await_proof(
                message,
                state,
                user_id,
                username,
                city,
                event_id,
                guests,
                discount,
                discounted_amount,
                regular_amount,
                formula_amount,
                graduate_type,
                payment_method="to_maria",
            )
        if response == "pay_later":
            await _handle_pay_later(
                message,
                user_id,
                username,
                city,
                event_id,
                discounted_amount,
                regular_amount,
                formula_amount,
                graduate_type,
                event=event,
            )
            return False
        if response == "too_expensive":
            await _handle_too_expensive(
                message,
                user_id,
                username,
                city,
                event_id,
                discounted_amount,
                regular_amount,
                graduate_type,
                state=state,
            )
            return False

    # Direct photo/PDF without pressing a button (shortcut).
    return await _handle_screenshot_upload(
        message,
        response,
        user_id,
        username,
        city,
        event_id,
        guests,
        discount,
        discounted_amount,
        regular_amount,
        formula_amount,
        graduate_type,
    )


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


async def _resolve_registration_from_callback(user_id: int, event_id: str):
    if event_id in _LEGACY_CITY_CODES_REVERSE:
        city = _LEGACY_CITY_CODES_REVERSE[event_id]
        logger.warning(f"Legacy callback format, resolved city: {city}")
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
    return registration


def _get_graduate_type_info(registration: dict) -> str:
    graduate_type = registration.get("graduate_type", GraduateType.GRADUATE.value)
    if graduate_type == GraduateType.TEACHER.value:
        return "👨‍🏫 Учитель (бесплатно)"
    if graduate_type == GraduateType.NON_GRADUATE.value:
        return "👥 Друг школы (не выпускник)"
    graduation_year = registration.get("graduation_year", "Неизвестно")
    class_letter = registration.get("class_letter", "")
    return f"🎓 Выпускник {graduation_year} {class_letter}"


async def _resolve_payment_amount(
    amount_str: str | None,
    chat_id: int,
    username,
    full_name: str,
    city: str,
    graduate_type_info: str,
    state: FSMContext,
    callback_query: CallbackQuery,
) -> int | None:
    if amount_str == "custom" or not amount_str:
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
            logger.warning(f"Payment amount input timeout for user in city {city}")
            return None
        try:
            return int(amount_response.text)
        except ValueError:
            await send_safe(
                chat_id,
                "Некорректная сумма платежа. Пожалуйста, используйте команду снова.",
            )
            return None
    else:
        try:
            return int(amount_str)
        except ValueError:
            await callback_query.answer("Invalid amount in callback data")
            return None


def _build_payment_status_text(
    payment_amount: int,
    total_payment: int,
    city: str,
    is_additional_payment: bool,
    recommended_amount: int,
    payment_history: list,
) -> str:
    if is_additional_payment:
        status = f"✅ ДОПОЛНИТЕЛЬНЫЙ ПЛАТЕЖ ПОДТВЕРЖДЕН\nСумма: {payment_amount} руб.\nВсего оплачено: {total_payment} руб."
    else:
        status = f"✅ ПЛАТЕЖ ПОДТВЕРЖДЕН\nСумма: {payment_amount} руб."

    if total_payment < recommended_amount:
        status += f"\n⚠️ На {recommended_amount - total_payment} руб. меньше рекомендуемой суммы!"

    if len(payment_history) > 1:
        status += "\n\nИстория платежей:"
        for i, payment in enumerate(payment_history):
            status += (
                f"\n{i + 1}. {payment['amount']} руб. ({payment['timestamp'][:10]})"
            )
    return status


async def _update_callback_message_confirmed(
    callback_query: CallbackQuery,
    payment_status: str,
    user_info: str,
):
    assert callback_query.message is not None
    if callback_query.message.caption:  # type: ignore[union-attr]
        caption = callback_query.message.caption  # type: ignore[union-attr]
        new_caption = f"{caption}\n\n{payment_status}"
        if len(new_caption) > 1024:
            new_caption = new_caption[-1024:]
        await callback_query.message.edit_caption(  # type: ignore[union-attr]
            caption=new_caption
        )
    else:
        text = callback_query.message.text or ""  # type: ignore[union-attr]
        new_text = f"{text}\n\n{payment_status} для {user_info}"
        await callback_query.message.edit_text(text=new_text)  # type: ignore[union-attr]


# Add callback handlers for payment confirmation/decline buttons
@router.callback_query(lambda c: c.data and c.data.startswith("confirm_payment_"))
async def confirm_payment_callback(callback_query: CallbackQuery, state: FSMContext):
    """Confirm a payment"""
    current_state = await state.get_state()
    if current_state is not None:
        await callback_query.answer("Дождитесь завершения текущей операции...")
        return

    assert callback_query.data is not None
    try:
        user_id, event_id, amount_str = parse_payment_callback_data(callback_query.data)
    except ValueError as e:
        await callback_query.answer(f"Invalid callback data: {e}")
        return

    registration = await _resolve_registration_from_callback(user_id, event_id)
    city = registration.get("target_city", "") if registration else ""
    if not registration:
        await callback_query.answer("Registration not found")
        return

    username = registration.get("username", user_id)
    full_name = registration.get("full_name", "Неизвестно")
    graduate_type_info = _get_graduate_type_info(registration)

    assert callback_query.message is not None, "callback_query.message is None"
    chat_id = callback_query.message.chat.id

    # Immediately remove inline buttons to prevent duplicate clicks
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.warning(
            f"Duplicate payment confirmation attempt for user {user_id}, event {event_id}"
        )
        await callback_query.answer("Этот платеж уже обрабатывается")
        return

    payment_amount = await _resolve_payment_amount(
        amount_str,
        chat_id,
        username,
        full_name,
        city,
        graduate_type_info,
        state,
        callback_query,
    )
    if payment_amount is None:
        return

    event_id_for_update = registration.get("event_id", event_id)
    await app.update_payment_status(
        user_id,
        event_id=event_id_for_update,
        status="confirmed",
        payment_amount=payment_amount,
    )

    updated_registration = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id_for_update}
    )
    assert updated_registration is not None, (
        "Registration not found after payment update"
    )
    total_payment = updated_registration.get("payment_amount", payment_amount)
    is_additional_payment = total_payment != payment_amount

    discounted_amount = registration.get("discounted_payment_amount", 0)
    regular_amount = registration.get("regular_payment_amount", 0)
    recommended_amount = (
        discounted_amount
        if discounted_amount and discounted_amount < regular_amount
        else regular_amount
    )

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

    await send_safe(user_id, payment_message)

    event = await app.get_event_for_registration(updated_registration)
    await send_paid_ticket_card(user_id, updated_registration, event)

    if callback_query.message:
        user_info = f"{registration.get('username', user_id)} ({registration.get('full_name', 'Неизвестно')})"
        payment_history = updated_registration.get("payment_history", [])
        payment_status = _build_payment_status_text(
            payment_amount,
            total_payment,
            city,
            is_additional_payment,
            recommended_amount,
            payment_history,
        )
        await _update_callback_message_confirmed(
            callback_query, payment_status, user_info
        )

    await callback_query.answer("Платеж подтвержден")
    await app.export_registered_users_to_google_sheets()


@router.callback_query(lambda c: c.data and c.data.startswith("decline_payment_"))
async def decline_payment_callback(callback_query: CallbackQuery, state: FSMContext):
    """Ask for decline reason"""
    current_state = await state.get_state()
    if current_state is not None:
        await callback_query.answer("Дождитесь завершения текущей операции...")
        return

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

    await state.set_state(PaymentStates.waiting_for_decline_reason)
    await state.update_data(
        decline_user_id=user_id,
        decline_event_id=event_id,
        callback_message=callback_query.message,
    )

    if callback_query.message:
        if callback_query.message.caption:  # type: ignore[union-attr]
            caption = callback_query.message.caption  # type: ignore[union-attr]
            new_caption = f"{caption}\n\n⚠️ Укажите причину отклонения платежа в ответном сообщении:"
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
        await callback_query.answer("Укажите причину отклонения в следующем сообщении")


@router.message(PaymentStates.waiting_for_decline_reason)
async def payment_decline_reason_handler(message: Message, state: FSMContext):
    """Handle payment decline reason"""
    if not message.from_user or not is_admin(message.from_user):
        return

    data = await state.get_data()
    user_id = data.get("decline_user_id")
    event_id = data.get("decline_event_id")
    callback_message = data.get("callback_message")

    if not user_id or not event_id:
        await message.reply("Ошибка: не найдена информация о платеже")
        await state.clear()
        return

    decline_reason = message.text or "Причина не указана"

    await app.update_payment_status(
        user_id, event_id=event_id, status="declined", admin_comment=decline_reason
    )

    registration = await app.collection.find_one(
        {"user_id": user_id, "event_id": event_id}
    )
    if not registration:
        await message.reply(f"Регистрация не найдена для пользователя {user_id}")
        await state.clear()
        return

    city = registration.get("target_city", "") if registration else ""
    await send_safe(
        user_id,
        f"❌ Ваш платеж для участия во встрече в городе {city} отклонен.\n\nПричина: {decline_reason}\n\nПожалуйста, используйте команду /pay для повторной оплаты.",
    )

    if callback_message:
        user_info = f"{registration.get('username', user_id)} ({registration.get('full_name', 'Неизвестно')})"
        try:
            if hasattr(callback_message, "caption") and callback_message.caption:
                caption = callback_message.caption
                caption = caption.split("\n\n⚠️ Укажите причину")[0]
                new_caption = (
                    f"{caption}\n\n❌ ПЛАТЕЖ ОТКЛОНЕН\nПричина: {decline_reason}"
                )
                if len(new_caption) > 1024:
                    new_caption = new_caption[-1024:]
                await callback_message.edit_caption(
                    caption=new_caption, reply_markup=None
                )
            elif hasattr(callback_message, "text"):
                text = callback_message.text or ""
                text = text.split("\n\n⚠️ Укажите причину")[0]
                new_text = f"{text}\n\n❌ ПЛАТЕЖ ОТКЛОНЕН для {user_info}\nПричина: {decline_reason}"
                await callback_message.edit_text(text=new_text, reply_markup=None)
        except Exception as e:
            logger.error(f"Error updating callback message: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            raise

    await message.reply("❌ Платеж отклонен")
    await state.clear()
