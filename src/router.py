from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    ReplyKeyboardRemove,
    Message,
)
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger
from textwrap import dedent
from typing import Dict, List, Optional

from src.app import App, RegisteredUser, GraduateType
from src.event_images import send_event_image
from src.routers.admin import admin_handler
from botspot import commands_menu
from src.user_interactions import ask_user, ask_user_choice
from botspot.utils import send_safe, is_admin

router = Router()

# Load environment variables
load_dotenv()

# Dictionary to track log messages for each user
log_messages: Dict[int, List[Message]] = {}


# ---- Helper functions to get event data ----


def get_event_date_display(event: Optional[Dict]) -> str:
    """Get display date from an event dict. Auto-appends year for non-current-year events."""
    if event:
        display = event.get("date_display", "дата неизвестна")
        if event.get("date") and event["date"].year != datetime.now().year:
            display += f" {event['date'].year}"
        return display
    return "дата неизвестна"


def get_event_city(event: Optional[Dict]) -> str:
    """Get city name from an event dict."""
    if event:
        return event.get("city", "")
    return ""


def is_event_free(
    event: Optional[Dict], graduate_type: str = GraduateType.GRADUATE.value
) -> bool:
    """Check if an event is free for a given graduate type."""
    if not event:
        return False
    if event.get("pricing_type") == "free":
        return True
    if graduate_type in event.get("free_for_types", []):
        return True
    return False


# ---- Shared formatting helpers ----


def _payment_status_emoji(status: str) -> str:
    if status == "confirmed":
        return "✅"
    if status == "declined":
        return "❌"
    return "⏳"


def _format_guest_summary(guests: List[Dict]) -> str:
    summary = f"👥 Гости ({len(guests)}):\n"
    for i, g in enumerate(guests, 1):
        summary += f"  {i}. {g['name']} — {g['price']}₽\n"
    guest_total = sum(g["price"] for g in guests)
    guest_total_discounted = sum(g.get("price_discounted", g["price"]) for g in guests)
    if guest_total != guest_total_discounted:
        summary += (
            f"\nОбщая стоимость за гостей: {guest_total}₽"
            f"\nПри ранней регистрации: {guest_total_discounted}₽"
        )
    else:
        summary += f"\nОбщая стоимость за гостей: {guest_total}₽"
    return summary


async def _append_log(user_id: int, log_msg) -> None:
    if log_msg:
        log_messages[user_id].append(log_msg)


# ---- handle_registered_user helpers ----


async def _format_multi_reg_info(registrations: List[Dict], app: App) -> str:
    info_text = "Вы зарегистрированы на встречи выпускников в нескольких городах:\n\n"
    for reg in registrations:
        city = reg["target_city"]
        event = await app.get_event_for_registration(reg)
        graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
        payment_status = ""
        if not is_event_free(event, graduate_type):
            status = reg.get("payment_status")
            if status is None:
                status = "не оплачено"
            payment_status = f" - {_payment_status_emoji(status)} {status}"
        info_text += f"• {city} ({get_event_date_display(event)}){payment_status}\n"
        info_text += f"  ФИО: {reg['full_name']}\n"
        info_text += (
            f"  Год выпуска: {reg['graduation_year']}, Класс: {reg['class_letter']}\n"
        )
        reg_guests = reg.get("guests", [])
        if reg_guests:
            guest_names = ", ".join(g["name"] for g in reg_guests)
            info_text += f"  👥 Гости: {guest_names}\n"
        info_text += "\n"
    info_text += "Что вы хотите сделать?"
    return info_text


async def _format_single_reg_info(reg: Dict, app: App) -> str:
    graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
    event = await app.get_event_for_registration(reg)
    event_is_free = is_event_free(event, graduate_type)
    city = reg["target_city"]

    payment_status_line = ""
    if not event_is_free:
        status = reg.get("payment_status")
        if status is None:
            status = "не оплачено"
        payment_status_line = (
            f"Статус оплаты: {_payment_status_emoji(status)} {status}\n"
        )

    info_text = dedent(
        f"""
        Вы зарегистрированы на встречу выпускников:

        ФИО: {reg["full_name"]}
        """
    )
    if graduate_type == GraduateType.TEACHER.value:
        info_text += "Статус: Учитель\n"
    elif graduate_type == GraduateType.NON_GRADUATE.value:
        info_text += "Статус: Не выпускник\n"
    elif graduate_type == GraduateType.ORGANIZER.value:
        info_text += "Статус: Организатор\n"
    else:
        info_text += f"Год выпуска: {reg['graduation_year']}\n"
        info_text += f"Класс: {reg['class_letter']}\n"

    info_text += f"Город: {city} ({get_event_date_display(event)})\n"

    reg_guests = reg.get("guests", [])
    if reg_guests:
        info_text += f"👥 Гости ({len(reg_guests)}):\n"
        for g in reg_guests:
            info_text += f"  • {g['name']}\n"

    info_text += payment_status_line
    info_text += "\nЧто вы хотите сделать?"
    return info_text, event_is_free


async def _show_past_events_history(message: Message, app: App, user_id: int):
    """Show all past events with participant counts and user attendance."""
    all_events = await app.get_all_events()
    past_events = [e for e in all_events if e.get("status") in ("passed", "archived")]

    if not past_events:
        await send_safe(
            message.chat.id,
            "Пока нет прошедших встреч.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Get user's registrations to check attendance
    user_regs = await app.get_user_registrations(user_id)
    user_event_ids = {reg.get("event_id") for reg in user_regs}

    info_text = "📅 История встреч выпускников:\n\n"
    for event in past_events:
        eid = str(event["_id"])
        date_str = get_event_date_display(event)
        city = event.get("city", "?")
        count = await app.get_registration_count_for_event(eid)
        attended = "✅" if eid in user_event_ids else "—"
        info_text += f"{attended} {city} ({date_str}) — {count} чел.\n"

    info_text += "\nСледите за новостями — будем рады видеть вас на следующих встречах!"
    await send_safe(message.chat.id, info_text, reply_markup=ReplyKeyboardRemove())


async def handle_registered_user(
    message: Message, state: FSMContext, registration, app: App
):
    """Handle interaction with already registered user"""
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    # Get active registrations only (exclude archived events)
    registrations = await app.get_user_active_registrations(message.from_user.id)

    if not registrations:
        await send_safe(
            message.chat.id,
            "У вас нет активных регистраций.\nИспользуйте /start для регистрации на новую встречу.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Split into future and past
    future_regs = []
    past_regs = []
    for reg in registrations:
        event = await app.get_event_for_registration(reg)
        if event and app.is_event_passed(event):
            past_regs.append(reg)
        else:
            future_regs.append(reg)

    if not future_regs:
        # Only past registrations — show history
        await _show_past_events_history(message, app, message.from_user.id)
        return

    if len(future_regs) > 1:
        await _handle_multi_registrations(
            message, state, future_regs, registration, app
        )
    else:
        await _handle_single_registration(
            message, state, future_regs[0], registration, app
        )


async def _handle_multi_registrations(
    message: Message, state: FSMContext, registrations, registration, app: App
):
    info_text = await _format_multi_reg_info(registrations, app)
    response = await ask_user_choice(
        message.chat.id,
        info_text,
        choices={
            "register_another": "Зарегистрироваться в другом городе",
            "manage": "Управлять регистрациями",
            "nothing": "Ничего, всё в порядке",
        },
        state=state,
        timeout=None,
    )
    if response == "register_another":
        await register_user(message, state, app, reuse_info=registration)
    elif response == "manage":
        await manage_registrations(message, state, registrations, app)
    else:
        await send_safe(
            message.chat.id,
            "Отлично! Ваши регистрации в силе. До встречи!\n\n"
            "Используйте команду /info для получения подробной информации о встречах (дата, время, адрес).",
            reply_markup=ReplyKeyboardRemove(),
        )


async def _handle_single_registration(
    message: Message, state: FSMContext, reg, registration, app: App
):
    info_text, event_is_free = await _format_single_reg_info(reg, app)
    city = reg["target_city"]
    needs_payment = not event_is_free and reg.get("payment_status") != "confirmed"

    choices = {"nothing": "Ничего, всё в порядке"}
    if needs_payment:
        choices["pay"] = "Оплатить участие"
    choices.update(
        {
            "register_another": "Зарегистрироваться в другом городе",
            "cancel": "Отменить регистрацию",
        }
    )

    response = await ask_user_choice(
        message.chat.id,
        info_text,
        choices=choices,
        state=state,
        timeout=None,
    )

    if message.from_user:
        await app.save_event_log(
            "button_click",
            {
                "button": response,
                "context": "single_registration_menu",
                "city": city,
                "needs_payment": needs_payment,
                "payment_status": reg.get("payment_status"),
            },
            message.from_user.id,
            message.from_user.username,
        )

    if response == "cancel":
        await cancel_registration_handler(message, state, app)
    elif response == "pay":
        await _pay_existing_registration(message, state, reg, app)
    elif response == "register_another":
        await send_safe(message.chat.id, "Давайте зарегистрируемся в другом городе.")
        await register_user(message, state, app, reuse_info=registration)
    else:
        await send_safe(
            message.chat.id,
            "Отлично! Ваша регистрация в силе. До встречи!\n\nИспользуйте команду /info для получения подробной информации о встречах (дата, время, адрес).",
            reply_markup=ReplyKeyboardRemove(),
        )


async def _pay_existing_registration(
    message: Message, state: FSMContext, reg, app: App
):
    from src.routers.payment import process_payment

    assert message.from_user is not None
    await state.update_data(
        original_user_id=message.from_user.id,
        original_username=message.from_user.username,
    )
    graduation_year = reg["graduation_year"]
    graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
    skip_instructions = reg.get("payment_status") is not None
    await process_payment(
        message,
        state,
        reg["event_id"],
        graduation_year,
        skip_instructions,
        graduate_type=graduate_type,
    )


async def _edit_guests(
    message: Message, state: FSMContext, reg: Dict, event: Dict, app: App
):
    """Allow user to add/change/remove guests on an existing registration."""

    assert message.from_user is not None
    user_id = message.from_user.id
    username = message.from_user.username or ""
    city = reg["target_city"]
    reg_event_id = reg["event_id"]

    max_guests = event.get("max_guests_per_person", 3)
    existing_guests = reg.get("guests", [])

    guest_choices = {"0": "Убрать всех гостей" if existing_guests else "Нет гостей"}
    for i in range(1, max_guests + 1):
        label = f"+{i}"
        if i == len(existing_guests):
            label += " (текущее)"
        guest_choices[str(i)] = label

    guest_count_resp = await ask_user_choice(
        message.chat.id,
        f"👥 Сейчас гостей: {len(existing_guests)}. Сколько гостей вы хотите?",
        choices=guest_choices,
        state=state,
        timeout=None,
    )

    guest_count = (
        int(guest_count_resp) if guest_count_resp and guest_count_resp.isdigit() else 0
    )

    if guest_count == 0:
        await app.save_registration_guests(user_id, reg_event_id, [])
        await send_safe(message.chat.id, "👥 Гости убраны.")
        await app.save_event_log(
            "edit_guests",
            {"action": "remove_all_guests", "city": city},
            user_id,
            username,
        )
        return

    graduation_year = reg.get("graduation_year", 2000)
    graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
    reg_amount, _, _, _ = app.calculate_event_payment(
        event, graduation_year, graduate_type
    )
    guest_price_regular, guest_price_discounted = app.calculate_guest_price(
        event, reg_amount
    )

    guests = await _collect_guest_names(
        message,
        state,
        guest_count,
        existing_guests,
        guest_price_regular,
        guest_price_discounted,
    )

    await app.save_registration_guests(user_id, reg_event_id, guests)
    await send_safe(message.chat.id, _format_guest_summary(guests))

    await app.save_event_log(
        "edit_guests",
        {
            "action": "update_guests",
            "city": city,
            "guest_count": len(guests),
            "guests": [g["name"] for g in guests],
        },
        user_id,
        username,
    )


async def _collect_guest_names(
    message: Message,
    state: FSMContext,
    guest_count: int,
    existing_guests: List[Dict],
    guest_price_regular: int,
    guest_price_discounted: int,
) -> List[Dict]:
    from src.user_interactions import ask_user_raw

    guests = []
    for i in range(1, guest_count + 1):
        default_hint = ""
        if i <= len(existing_guests):
            default_hint = f" (было: {existing_guests[i - 1]['name']})"
        name_resp = await ask_user_raw(
            message.chat.id,
            f"Имя гостя {i}{default_hint}:",
            state=state,
            timeout=None,
        )
        guest_name = ""
        if name_resp and name_resp.text:
            guest_name = name_resp.text.strip()
        if len(guest_name) < 2:
            guest_name = f"Гость {i}"
        guests.append(
            {
                "name": guest_name,
                "price": guest_price_regular,
                "price_discounted": guest_price_discounted,
            }
        )
    return guests


async def manage_registrations(
    message: Message, state: FSMContext, registrations, app: App
):
    """Allow user to manage multiple registrations"""
    assert message.from_user is not None

    choices = {}
    for reg in registrations:
        city = reg["target_city"]
        reg_eid = reg["event_id"]
        choices[reg_eid] = f"Управлять регистрацией в городе {city}"
    choices["all"] = "Отменить все регистрации"
    choices["back"] = "Вернуться назад"

    if message.from_user:
        await app.save_event_log(
            "navigation",
            {
                "action": "enter_registration_management",
                "cities": [reg["target_city"] for reg in registrations],
            },
            message.from_user.id,
            message.from_user.username,
        )

    response = await ask_user_choice(
        message.chat.id,
        "Выберите регистрацию для управления:",
        choices=choices,
        state=state,
        timeout=None,
    )

    if message.from_user:
        await app.save_event_log(
            "button_click",
            {
                "button": response,
                "context": "registration_management",
                "cities": [reg["target_city"] for reg in registrations],
            },
            message.from_user.id,
            message.from_user.username,
        )

    if response == "all":
        await _handle_cancel_all_registrations(message, state, registrations, app)
    elif response == "back":
        await handle_registered_user(message, state, registrations[0], app)
    else:
        await _handle_single_reg_management(
            message, state, registrations, response, app
        )


async def _handle_cancel_all_registrations(
    message: Message, state: FSMContext, registrations, app: App
):
    assert message.from_user is not None
    confirm = await ask_user_choice(
        message.chat.id,
        "Вы уверены, что хотите отменить ВСЕ регистрации?",
        choices={"yes": "Да, отменить все", "no": "Нет, вернуться назад"},
        state=state,
        timeout=None,
    )
    if message.from_user:
        await app.save_event_log(
            "button_click",
            {"button": confirm, "context": "confirm_delete_all_registrations"},
            message.from_user.id,
            message.from_user.username,
        )
    if confirm == "yes":
        await app.delete_user_registration(message.from_user.id)
        user_reg = await app.get_user_registration(message.from_user.id)
        full_name = user_reg.get("full_name", "Unknown") if user_reg else "Unknown"
        await app.log_registration_canceled(
            message.from_user.id,
            message.from_user.username or "",
            full_name,
            "все города",
        )
        await send_safe(
            message.chat.id,
            "Все ваши регистрации отменены. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await manage_registrations(message, state, registrations, app)


async def _handle_single_reg_management(
    message: Message, state: FSMContext, registrations, selected_event_id: str, app: App
):
    assert message.from_user is not None
    reg = next(r for r in registrations if r["event_id"] == selected_event_id)
    city = reg["target_city"]
    event = await app.get_event_for_registration(reg)

    existing_guests = reg.get("guests", [])
    guests_info = ""
    if existing_guests:
        guests_info = f"\n            Гости ({len(existing_guests)}): {', '.join(g['name'] for g in existing_guests)}"

    info_text = dedent(
        f"""
        Регистрация в городе {city}:

        ФИО: {reg["full_name"]}
        Год выпуска: {reg["graduation_year"]}
        Класс: {reg["class_letter"]}
        Дата: {get_event_date_display(event)}{guests_info}

        Что вы хотите сделать?
        """
    )

    choices = {}
    if event and event.get("guests_enabled"):
        choices["guests"] = (
            "👥 Изменить гостей" if existing_guests else "👥 Добавить гостей"
        )
    choices["cancel"] = "Отменить регистрацию"
    choices["back"] = "Вернуться назад"

    action = await ask_user_choice(
        message.chat.id,
        info_text,
        choices=choices,
        state=state,
        timeout=None,
    )

    if message.from_user:
        await app.save_event_log(
            "button_click",
            {"button": action, "context": "city_registration_management", "city": city},
            message.from_user.id,
            message.from_user.username,
        )

    if action == "guests":
        if event is None:
            await send_safe(
                message.chat.id, "Произошла ошибка: не удалось найти мероприятие."
            )
            return
        await _edit_guests(message, state, reg, event, app)
        remaining = await app.get_user_active_registrations(message.from_user.id)
        if remaining:
            await manage_registrations(message, state, remaining, app=app)
        return

    if action == "cancel":
        await app.delete_user_registration(message.from_user.id, selected_event_id)
        await app.log_registration_canceled(
            message.from_user.id,
            message.from_user.username or "",
            reg.get("full_name", "Unknown"),
            city,
        )
        remaining = await app.get_user_active_registrations(message.from_user.id)
        if remaining:
            await send_safe(
                message.chat.id,
                f"Регистрация в городе {city} отменена. У вас остались другие регистрации.",
            )
            await handle_registered_user(message, state, remaining[0], app)
        else:
            await send_safe(
                message.chat.id,
                "Ваша регистрация отменена. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
                reply_markup=ReplyKeyboardRemove(),
            )
    else:
        await manage_registrations(message, state, registrations, app=app)


async def handle_cancel_option(response, message: Message, state: FSMContext) -> bool:
    """Helper function to handle cancel option in user interactions"""
    if response == "cancel":
        await send_safe(
            message.chat.id,
            "Регистрация отменена. Если передумаете, используйте /start чтобы начать заново.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return True
    return False


# ---- register_user step helpers ----


async def _select_preselected_city(
    message: Message,
    app: App,
    enabled_events: list,
    preselected_city: str,
    user_id: int,
    username,
):
    """Handle the preselected-city branch. Returns (event, city) or (None, None)."""
    selected_event = next(
        (
            e
            for e in enabled_events
            if e["city"] == preselected_city or e["name"] == preselected_city
        ),
        None,
    )
    if selected_event and app.is_event_passed(selected_event):
        await send_safe(
            message.chat.id,
            f"К сожалению, встреча в городе {preselected_city} уже прошла.\n\n"
            "Вы можете:\n"
            "1. Выбрать другой город, если там встреча еще не прошла\n"
            "2. Следить за новостями в группе школы, чтобы не пропустить следующие встречи",
            reply_markup=ReplyKeyboardRemove(),
        )
        return None, None
    log_msg = await app.log_registration_step(
        user_id, username, "Выбор города", f"Предвыбранный город: {preselected_city}"
    )
    await _append_log(user_id, log_msg)
    return selected_event, preselected_city


async def _ask_city_choice(
    message: Message,
    state: FSMContext,
    app: App,
    enabled_events: list,
    existing_event_ids: List[str],
    event_map: dict,
    user_id: int,
    username,
):
    """Ask the user to choose a city. Returns (event, location) or (None, None)."""
    available_events = [
        e for e in enabled_events if str(e["_id"]) not in existing_event_ids
    ]

    if not available_events:
        await send_safe(
            message.chat.id,
            "К сожалению, вы уже зарегистрированы во всех доступных городах.\n\n"
            "Следите за новостями в группе школы, чтобы не пропустить следующие встречи.",
            reply_markup=ReplyKeyboardRemove(),
        )
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Нет доступных городов",
            "Пользователь уже зарегистрирован во всех городах",
        )
        await _append_log(user_id, log_msg)
        return None, None

    available_cities = {}
    for e in available_events:
        eid = str(e["_id"])
        label = f"{e['city']} ({get_event_date_display(e)})"
        if app.is_event_passed(e):
            label += " — уже прошла"
        available_cities[eid] = label
    available_cities["cancel"] = "Отменить регистрацию"

    response = await ask_user_choice(
        message.chat.id,
        dedent("Выберите город, где планируете посетить встречу:"),
        choices=available_cities,
        state=state,
        timeout=None,
    )

    if await handle_cancel_option(response, message, state):
        return None, None

    if response is None:
        await send_safe(
            message.chat.id,
            "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
            reply_markup=ReplyKeyboardRemove(),
        )
        return None, None

    selected_event = event_map.get(response)

    # Warn if user picks a passed event
    if selected_event and app.is_event_passed(selected_event):
        from src.user_interactions import ask_user_confirmation

        confirmed = await ask_user_confirmation(
            message.chat.id,
            f"Встреча в {selected_event['city']} ({get_event_date_display(selected_event)}) уже прошла.\n"
            "Вы регистрируете оплату постфактум?",
            state=state,
        )
        if not confirmed:
            return None, None
    location = selected_event["city"] if selected_event else response

    log_msg = await app.log_registration_step(
        user_id, username, "Выбор города", f"Выбранный город: {location}"
    )
    await app.save_event_log(
        "registration_step",
        {
            "step": "city_selection",
            "city": location,
            "event_id": str(selected_event["_id"]) if selected_event else None,
            "existing_event_ids": existing_event_ids,
        },
        user_id,
        username,
    )
    await _append_log(user_id, log_msg)
    return selected_event, location


async def _select_event_for_registration(
    message: Message,
    state: FSMContext,
    app: App,
    preselected_city,
    existing_event_ids: List[str],
    user_id: int,
    username,
):
    """
    Returns (selected_event, location) or (None, None) on cancel/timeout.
    If preselected_city is given, validates and returns early.
    """
    enabled_events = await app.get_enabled_events()
    # Also include passed (not archived) events for post-factum registration
    all_events = await app.get_all_events()
    passed_events = [
        e
        for e in all_events
        if e.get("status") == "passed"
        and str(e["_id"]) not in {str(x["_id"]) for x in enabled_events}
    ]
    all_selectable = enabled_events + passed_events
    event_map = {str(e["_id"]): e for e in all_selectable}

    if preselected_city:
        return await _select_preselected_city(
            message, app, enabled_events, preselected_city, user_id, username
        )

    return await _ask_city_choice(
        message,
        state,
        app,
        all_selectable,
        existing_event_ids,
        event_map,
        user_id,
        username,
    )


async def _collect_full_name(
    message: Message, state: FSMContext, app: App, user_id: int, username
) -> Optional[str]:
    full_name = None
    while full_name is None:
        question = dedent("""
            Представьтесь, пожалуйста.
            Можно имя и фамилию, можно полные ФИО
            """)
        response = await ask_user(message.chat.id, question, state=state, timeout=None)
        if response is None:
            await send_safe(
                message.chat.id,
                "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
                reply_markup=ReplyKeyboardRemove(),
            )
            return None
        valid, error = app.validate_full_name(response)
        if valid:
            full_name = response
        else:
            await send_safe(
                message.chat.id, f"❌ {error} Пожалуйста, попробуйте еще раз."
            )
    log_msg = await app.log_registration_step(
        user_id, username, "Ввод ФИО", f"ФИО: {full_name}"
    )
    await _append_log(user_id, log_msg)
    return full_name


async def _collect_graduation_info(
    message: Message, state: FSMContext, app: App, user_id: int, username
):
    """
    Returns (graduation_year, class_letter, graduate_type) or (None, None, None) on timeout.
    """
    graduation_year = None
    class_letter = None
    graduate_type = GraduateType.GRADUATE

    while graduation_year is None or class_letter is None or not class_letter:
        if graduation_year is not None and class_letter is None:
            question = "А букву класса?"
        else:
            question = dedent("""
                Пожалуйста, введите год выпуска и букву класса.
                Например, "2003 Б".

                <tg-spoiler>Если вы учитель школы 146 (нынешний или бывший), нажмите: /i_am_a_teacher
                Если вы не выпускник, но друг школы 146 - нажмите: /i_am_a_friend
                Если вы организатор встречи - нажмите: /i_am_an_organizer</tg-spoiler>
                """)

        response = await ask_user(message.chat.id, question, state=state, timeout=None)
        if response is None:
            await send_safe(
                message.chat.id,
                "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
                reply_markup=ReplyKeyboardRemove(),
            )
            return None, None, None

        result = await _handle_graduation_response(
            message, app, user_id, username, response, graduation_year
        )
        if result is None:
            return None, None, None
        graduation_year, class_letter, graduate_type, done = result
        if done:
            break

    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Ввод года выпуска и класса",
        f"Год: {graduation_year}, Класс: {class_letter}",
    )
    await _append_log(user_id, log_msg)
    return graduation_year, class_letter, graduate_type


async def _handle_graduation_response(
    message: Message, app: App, user_id: int, username, response: str, graduation_year
):
    """
    Returns (graduation_year, class_letter, graduate_type, done) or None on timeout signal.
    done=True means break out of the while loop.
    """
    if response == "/i_am_a_teacher":
        log_msg = await app.log_registration_step(
            user_id, username, "Статус участника", "Учитель"
        )
        await _append_log(user_id, log_msg)
        return 0, "Т", GraduateType.TEACHER, True

    if response == "/i_am_a_friend":
        log_msg = await app.log_registration_step(
            user_id, username, "Статус участника", "Не выпускник"
        )
        await _append_log(user_id, log_msg)
        await send_safe(message.chat.id, "Вы зарегистрированы как друг школы 146!")
        return 2000, "Н", GraduateType.NON_GRADUATE, True

    if response == "/i_am_an_organizer":
        log_msg = await app.log_registration_step(
            user_id, username, "Статус участника", "Организатор"
        )
        await _append_log(user_id, log_msg)
        await send_safe(message.chat.id, "Вы зарегистрированы как организатор встречи!")
        return 1000, "О", GraduateType.ORGANIZER, True

    if graduation_year is not None:
        # Only class letter needed
        valid, error = app.validate_class_letter(response)
        if valid:
            return graduation_year, response.upper(), GraduateType.GRADUATE, False
        else:
            await send_safe(
                message.chat.id, f"❌ {error} Пожалуйста, попробуйте еще раз."
            )
            return graduation_year, None, GraduateType.GRADUATE, False

    # Parse both year and letter
    year, letter, error = app.parse_graduation_year_and_class_letter(response)
    if error:
        await send_safe(message.chat.id, f"❌ {error}")
        if year is not None and letter == "":
            return year, None, GraduateType.GRADUATE, False
        return graduation_year, None, GraduateType.GRADUATE, False

    return year, letter, GraduateType.GRADUATE, False


async def _collect_user_info(
    message: Message,
    state: FSMContext,
    app: App,
    user_id: int,
    username,
    reuse_info,
    reg_city_name: str,
):
    """
    Returns (full_name, graduation_year, class_letter, graduate_type) or None tuple on abort.
    Handles reuse_info confirmation and fresh data collection.
    """
    if reuse_info:
        full_name = reuse_info["full_name"]
        graduation_year = reuse_info["graduation_year"]
        class_letter = reuse_info["class_letter"]
        graduate_type = GraduateType(
            reuse_info.get("graduate_type", GraduateType.GRADUATE.value)
        )

        confirm_text = dedent(
            f"""
            Хотите использовать те же данные для регистрации в городе {reg_city_name}?

            ФИО: {full_name}
            Год выпуска: {graduation_year}
            Класс: {class_letter}
            """
        )
        confirm = await ask_user_choice(
            message.chat.id,
            confirm_text,
            choices={
                "yes": "Да, использовать эти данные",
                "no": "Нет, ввести новые данные",
                "cancel": "Отменить регистрацию",
            },
            state=state,
            timeout=None,
        )
        if await handle_cancel_option(confirm, message, state):
            return None, None, None, None

        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Повторное использование данных",
            f"Решение: {'Использовать существующие данные' if confirm == 'yes' else 'Ввести новые данные'}",
        )
        await _append_log(user_id, log_msg)

        if confirm == "yes":
            return full_name, graduation_year, class_letter, graduate_type
        # confirm == "no": fall through to fresh collection

    full_name = await _collect_full_name(message, state, app, user_id, username)
    if full_name is None:
        return None, None, None, None

    graduation_year, class_letter, graduate_type = await _collect_graduation_info(
        message, state, app, user_id, username
    )
    if graduation_year is None:
        return None, None, None, None

    return full_name, graduation_year, class_letter, graduate_type


async def _collect_guests_step(
    message: Message,
    state: FSMContext,
    app: App,
    selected_event,
    graduation_year: int,
    graduate_type: GraduateType,
    user_id: int,
    username,
) -> List[Dict]:
    """Returns list of guest dicts (may be empty)."""
    if not (selected_event and selected_event.get("guests_enabled")):
        return []

    max_guests = selected_event.get("max_guests_per_person", 3)
    guest_choices = {"0": "Нет, только я"}
    for i in range(1, max_guests + 1):
        guest_choices[str(i)] = f"+{i}"

    guest_count_resp = await ask_user_choice(
        message.chat.id,
        "👥 Хотите зарегистрировать кого-то с собой?",
        choices=guest_choices,
        state=state,
        timeout=None,
    )
    guest_count = (
        int(guest_count_resp) if guest_count_resp and guest_count_resp.isdigit() else 0
    )

    if guest_count == 0:
        log_msg = await app.log_registration_step(
            user_id, username, "Гости", "Количество: 0, Имена: нет"
        )
        await _append_log(user_id, log_msg)
        return []

    reg_amount, _, _, _ = app.calculate_event_payment(
        selected_event, graduation_year, graduate_type.value
    )
    guest_price_regular, guest_price_discounted = app.calculate_guest_price(
        selected_event, reg_amount
    )

    guests = await _collect_guest_names(
        message, state, guest_count, [], guest_price_regular, guest_price_discounted
    )

    await send_safe(message.chat.id, _format_guest_summary(guests))

    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Гости",
        f"Количество: {guest_count}, Имена: {', '.join(g['name'] for g in guests)}",
    )
    await _append_log(user_id, log_msg)
    return guests


async def _finalize_free_registration(
    message: Message,
    state: FSMContext,
    app: App,
    user_id: int,
    username,
    full_name: str,
    graduate_type: GraduateType,
    graduation_year: int,
    event_id_for_db: str,
    city_prep: str,
    date_display: str,
    guests: List[Dict],
):
    confirmation_msg = (
        f"Спасибо, {full_name}!\n"
        f"Вы зарегистрированы на встречу выпускников школы 146 "
        f"в {city_prep} {date_display}. "
    )
    if guests:
        confirmation_msg += f"\nС вами {len(guests)} гост{'ь' if len(guests) == 1 else 'ей' if len(guests) >= 5 else 'я'}. "

    if graduate_type == GraduateType.TEACHER:
        comment = "Автоматически подтверждено (учитель)"
        confirmation_msg += "\nДля учителей участие бесплатное. Спасибо за вашу работу!"
    elif graduate_type == GraduateType.ORGANIZER:
        comment = "Автоматически подтверждено (организатор)"
        confirmation_msg += (
            "\nДля организаторов участие бесплатное. Спасибо за вашу помощь!"
        )
    else:
        comment = "Автоматически подтверждено (бесплатное мероприятие)"
        confirmation_msg += "\nДля этой встречи оплата не требуется. Все расходы участники несут самостоятельно."

    guest_total = sum(g["price"] for g in guests) if guests else 0
    if guest_total > 0:
        comment += f" (гости: {guest_total}₽)"
        confirmation_msg += f"\n\n💰 Оплата за гостей: {guest_total}₽"
        await app.update_payment_status(
            user_id=user_id,
            event_id=event_id_for_db,
            status="not paid",
            payment_amount=0,
        )
        await send_safe(
            message.chat.id,
            confirmation_msg
            + "\nСейчас пришлем информацию об оплате за гостей...\n\nЕсли передумаете — используйте /cancel_registration для отмены.",
        )
        from src.routers.payment import process_payment

        await state.update_data(original_user_id=user_id, original_username=username)
        await process_payment(
            message,
            state,
            event_id_for_db,
            graduation_year,
            graduate_type=graduate_type.value,
            guests=guests,
        )
    else:
        await app.update_payment_status(
            user_id=user_id,
            event_id=event_id_for_db,
            status="confirmed",
            admin_comment=comment,
            payment_amount=0,
        )
        confirmation_msg += (
            "\n\nЕсли передумаете — используйте /cancel_registration для отмены."
        )
        await send_safe(
            message.chat.id, confirmation_msg, reply_markup=ReplyKeyboardRemove()
        )
        await app.export_registered_users_to_google_sheets()


async def _finalize_paid_registration(
    message: Message,
    state: FSMContext,
    app: App,
    user_id: int,
    username,
    full_name: str,
    graduate_type: GraduateType,
    graduation_year: int,
    selected_event,
    event_id_for_db: str,
    city_prep: str,
    date_display: str,
    guests: List[Dict],
):
    if not selected_event:
        logger.error(f"No event found for registration: user_id={user_id}")
        await send_safe(
            message.chat.id,
            "Произошла ошибка: не удалось найти мероприятие. Пожалуйста, попробуйте ещё раз.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    regular_amount, discount, discounted_amount, formula_amount = (
        app.calculate_event_payment(
            selected_event, graduation_year, graduate_type.value
        )
    )

    if guests:
        guest_total_regular = sum(g["price"] for g in guests)
        guest_total_discounted = sum(
            g.get("price_discounted", g["price"]) for g in guests
        )
        regular_amount += guest_total_regular
        discounted_amount += guest_total_discounted
        formula_amount += guest_total_regular

    await app.save_payment_info(
        user_id=user_id,
        event_id=event_id_for_db,
        discounted_amount=discounted_amount,
        regular_amount=regular_amount,
        formula_amount=formula_amount,
        username=username,
        payment_status="not paid",
    )

    confirmation_msg = (
        f"Спасибо, {full_name}!\n"
        f"Вы зарегистрированы на встречу выпускников школы 146 "
        f"в {city_prep} {date_display}. "
    )
    if guests:
        confirmation_msg += f"\nС вами {len(guests)} гост{'ь' if len(guests) == 1 else 'ей' if len(guests) >= 5 else 'я'}. "
    confirmation_msg += "Сейчас пришлем информацию об оплате...\n\nЕсли передумаете — используйте /cancel_registration для отмены."
    await send_safe(message.chat.id, confirmation_msg)

    from src.routers.payment import process_payment

    await state.update_data(original_user_id=user_id, original_username=username)
    await process_payment(
        message,
        state,
        event_id_for_db,
        graduation_year,
        graduate_type=graduate_type.value,
        guests=guests,
    )


def _get_city_prepositional(selected_event, location: str, reg_city_name: str) -> str:
    if selected_event:
        return selected_event.get("city_prepositional", reg_city_name)
    if location:
        from src.app import CITY_PREPOSITIONAL_MAP

        return CITY_PREPOSITIONAL_MAP.get(location, location)
    return ""


async def _save_and_log_registration(
    app: App,
    message: Message,
    state: FSMContext,
    user_id: int,
    username,
    full_name: str,
    graduation_year: int,
    class_letter: str,
    graduate_type,
    selected_event,
    location: str,
    reg_city_name: str,
    target_city_value: str,
    guests: List[Dict],
):
    """Save the registered user record, guests, and all completion logs."""
    event_id_for_db = str(selected_event["_id"]) if selected_event else ""

    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Регистрация завершена",
        f"Город: {reg_city_name}, ФИО: {full_name}, Выпуск: {graduation_year} {class_letter}, Гости: {len(guests)}",
    )
    await _append_log(user_id, log_msg)

    await app.log_registration_completed(
        user_id,
        username or "",
        full_name,
        graduation_year,
        class_letter,
        reg_city_name,
        graduate_type.value,
        guests=guests,
    )

    await delete_log_messages(user_id)

    if guests:
        await app.save_registration_guests(user_id, event_id_for_db, guests)

    city_prep = _get_city_prepositional(selected_event, location, reg_city_name)
    date_display = get_event_date_display(selected_event) if selected_event else ""
    event_is_free = (
        is_event_free(selected_event, graduate_type.value) if selected_event else False
    )

    if event_is_free or graduate_type in (GraduateType.TEACHER, GraduateType.ORGANIZER):
        await _finalize_free_registration(
            message,
            state,
            app,
            user_id,
            username,
            full_name,
            graduate_type,
            graduation_year,
            event_id_for_db,
            city_prep,
            date_display,
            guests,
        )
    else:
        await _finalize_paid_registration(
            message,
            state,
            app,
            user_id,
            username,
            full_name,
            graduate_type,
            graduation_year,
            selected_event,
            event_id_for_db,
            city_prep,
            date_display,
            guests,
        )


async def register_user(
    message: Message,
    state: FSMContext,
    app: App,
    preselected_city=None,
    reuse_info=None,
):
    """Register a user for an event"""
    assert message.from_user is not None
    user_id = message.from_user.id
    username = message.from_user.username

    if user_id not in log_messages:
        log_messages[user_id] = []

    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Начало регистрации",
        f"Предвыбранный город: {preselected_city}, Повторное использование данных: {'Да' if reuse_info else 'Нет'}",
    )
    await _append_log(user_id, log_msg)

    existing_registrations = await app.get_user_registrations(user_id)
    existing_event_ids = [
        reg["event_id"] for reg in existing_registrations if reg.get("event_id")
    ]

    selected_event, location = await _select_event_for_registration(
        message, state, app, preselected_city, existing_event_ids, user_id, username
    )
    if selected_event is None and location is None:
        return

    reg_city_name = (
        selected_event["city"] if selected_event else (location if location else "")
    )

    full_name, graduation_year, class_letter, graduate_type = await _collect_user_info(
        message, state, app, user_id, username, reuse_info, reg_city_name
    )
    if full_name is None:
        return

    target_city_value = (
        location if location else (selected_event["city"] if selected_event else "")
    )

    if not all([full_name, graduation_year is not None, class_letter, graduate_type]):
        logger.error(
            f"Registration validation failed - missing required fields: "
            f"full_name={full_name}, "
            f"graduation_year={graduation_year}, "
            f"class_letter={class_letter}, "
            f"graduate_type={graduate_type}"
        )

    event_id = str(selected_event["_id"]) if selected_event else ""
    assert full_name is not None, "full_name must be set by this point"
    assert graduation_year is not None, "graduation_year must be set by this point"
    assert class_letter is not None, "class_letter must be set by this point"
    registered_user = RegisteredUser(
        full_name=full_name,
        graduation_year=graduation_year,
        class_letter=class_letter,
        target_city=target_city_value,
        event_id=event_id,
        graduate_type=graduate_type,
    )
    await app.save_registered_user(registered_user, user_id=user_id, username=username)

    guests = await _collect_guests_step(
        message,
        state,
        app,
        selected_event,
        graduation_year,
        graduate_type,
        user_id,
        username,
    )

    await _save_and_log_registration(
        app,
        message,
        state,
        user_id,
        username,
        full_name,
        graduation_year,
        class_letter,
        graduate_type,
        selected_event,
        location,
        reg_city_name,
        target_city_value,
        guests,
    )


# Add this function to delete log messages
async def delete_log_messages(user_id: int) -> None:
    """Delete all log messages for a user"""
    if user_id not in log_messages:
        return

    from botspot.core.dependency_manager import get_dependency_manager

    deps = get_dependency_manager()
    bot = deps.bot

    for msg in log_messages[user_id]:
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except Exception as e:
            logger.error(f"Failed to delete log message: {e}")

    # Clear the list
    log_messages[user_id] = []


@commands_menu.add_command("cancel_registration", "Отменить регистрацию")
@router.message(Command("cancel_registration"))
async def cancel_registration_handler(message: Message, state: FSMContext, app: App):
    """
    Cancel user registration command handler.
    """
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    await app.save_event_log(
        "command",
        {
            "command": "/cancel_registration",
            "content": message.text,
            "chat_type": message.chat.type,
        },
        message.from_user.id,
        message.from_user.username,
    )

    user_id = message.from_user.id
    registrations = await app.get_user_registrations(user_id)

    if not registrations:
        await send_safe(
            message.chat.id,
            "У вас нет активных регистраций. Используйте /start для регистрации на встречу.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if len(registrations) == 1:
        await _cancel_single_registration(message, state, app, registrations[0])
    else:
        await _cancel_one_of_many_registrations(message, state, app, registrations)


async def _cancel_single_registration(
    message: Message, state: FSMContext, app: App, reg
):
    assert message.from_user is not None
    user_id = message.from_user.id
    city = reg["target_city"]
    reg_event_id = reg["event_id"]
    full_name = reg["full_name"]
    event = await app.get_event_for_registration(reg)

    confirm_text = dedent(
        f"""
        Вы уверены, что хотите отменить регистрацию на встречу в городе {city}?

        ФИО: {full_name}
        Год выпуска: {reg["graduation_year"]}
        Класс: {reg["class_letter"]}
        Город: {city} ({get_event_date_display(event)})
        """
    )
    response = await ask_user_choice(
        message.chat.id,
        confirm_text,
        choices={"yes": "Да, отменить", "no": "Нет, сохранить"},
        state=state,
        timeout=None,
    )
    if response == "yes":
        await app.delete_user_registration(user_id, reg_event_id)
        await app.log_registration_canceled(
            user_id, message.from_user.username or "", full_name, city
        )
        await send_safe(
            message.chat.id,
            "Ваша регистрация отменена. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await send_safe(
            message.chat.id,
            "Отмена регистрации отменена. Ваша регистрация сохранена.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def _cancel_one_of_many_registrations(
    message: Message, state: FSMContext, app: App, registrations
):
    assert message.from_user is not None
    user_id = message.from_user.id

    choices = {}
    for reg in registrations:
        city = reg["target_city"]
        eid = reg["event_id"]
        event = await app.get_event_for_registration(reg)
        choices[eid] = f"{city} ({get_event_date_display(event)})"
    choices["all"] = "Отменить все регистрации"
    choices["cancel"] = "Ничего не отменять"

    response = await ask_user_choice(
        message.chat.id,
        "Выберите, какую регистрацию вы хотите отменить:",
        choices=choices,
        state=state,
        timeout=None,
    )

    if response == "cancel":
        await send_safe(
            message.chat.id,
            "Отмена операции. Ваши регистрации сохранены.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if response == "all":
        full_name = registrations[0].get("full_name", "Unknown")
        await app.delete_user_registration(user_id)
        await app.log_registration_canceled(
            user_id, message.from_user.username or "", full_name, None
        )
        await send_safe(
            message.chat.id,
            "Все ваши регистрации отменены. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        reg = next(r for r in registrations if r["event_id"] == response)
        full_name = reg["full_name"]
        city = reg["target_city"]
        await app.delete_user_registration(user_id, response)
        await app.log_registration_canceled(
            user_id, message.from_user.username or "", full_name, city
        )
        await send_safe(
            message.chat.id,
            f"Ваша регистрация в городе {city} отменена. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
            reply_markup=ReplyKeyboardRemove(),
        )


@commands_menu.add_command("info", "Информация о встречах")
@router.message(Command("info"))
async def info_handler(message: Message, state: FSMContext, app: App):
    """
    Show detailed information about events in all cities
    """
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    await app.save_event_log(
        "command",
        {"command": "/info", "content": message.text, "chat_type": message.chat.type},
        message.from_user.id,
        message.from_user.username,
    )

    info_text = "📅 <b>Информация о встречах выпускников 146</b>\n\n"
    active_events = await app.get_active_events()

    if not active_events:
        info_text += (
            "Все встречи выпускников уже прошли. Спасибо, что были с нами! 🎓\n\n"
        )
        info_text += "Следите за новостями в группе школы, чтобы не пропустить следующие встречи."
        await send_safe(message.chat.id, info_text, parse_mode="HTML")
        return

    has_upcoming = False
    for event in active_events:
        await send_event_image(
            message.chat.id,
            event,
            caption=event.get("name", event.get("city", "")),
        )
        info_text += f"<b>🏙️ {event.get('name', event.get('city', ''))}</b>\n"
        if app.is_event_passed(event):
            info_text += (
                f"📆 Дата: {event.get('date_display', '')} (встреча уже прошла)\n"
            )
        else:
            has_upcoming = True
            info_text += f"📆 Дата: {event.get('date_display', '')}\n"
            info_text += f"⏰ Время: {event.get('time_display', 'Уточняется')}\n"
            venue = event.get("venue")
            address = event.get("address")
            info_text += f"🏢 Место: {venue}\n" if venue else "🏢 Место: Уточняется\n"
            info_text += (
                f"📍 Адрес: {address}\n" if address else "📍 Адрес: Уточняется\n"
            )
        info_text += "\n"

    if has_upcoming:
        info_text += "Используйте /start для регистрации на встречу.\n"
        info_text += "Используйте /pay для оплаты участия после регистрации.\n"

    await send_safe(message.chat.id, info_text, parse_mode="HTML")


def _format_graduate_type_line(graduate_type: str) -> str:
    if graduate_type == GraduateType.TEACHER.value:
        return "👨‍🏫 Статус: Учитель\n"
    if graduate_type == GraduateType.NON_GRADUATE.value:
        return "👥 Статус: Не выпускник\n"
    if graduate_type == GraduateType.ORGANIZER.value:
        return "🛠️ Статус: Организатор\n"
    return ""


def _format_payment_status_line(reg: Dict, event, graduate_type: str) -> str:
    event_free = is_event_free(event, graduate_type)
    if event_free:
        if graduate_type == GraduateType.TEACHER.value:
            return "💰 Оплата: Бесплатно (учитель)\n"
        if graduate_type == GraduateType.ORGANIZER.value:
            return "💰 Оплата: Бесплатно (организатор)\n"
        return "💰 Оплата: За свой счет\n"

    payment_status = reg.get("payment_status")
    if payment_status is None:
        payment_status = "не оплачено"
    line = (
        f"💰 Статус оплаты: {_payment_status_emoji(payment_status)} {payment_status}\n"
    )
    if "payment_amount" in reg:
        line += f"💵 Сумма оплаты: {reg['payment_amount']} руб.\n"
    elif payment_status == "pending" and "discounted_payment_amount" in reg:
        line += f"💵 Ожидаемая сумма: {reg['discounted_payment_amount']} руб.\n"
    return line


def _format_registration_status_text(registrations, events_by_reg, app: App) -> str:
    status_text = "📋 Ваши регистрации:\n\n"
    for reg, event in zip(registrations, events_by_reg):
        city = reg["target_city"]
        full_name = reg["full_name"]
        graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)

        status_text += f"🏙️ Город: {city}"
        if event:
            suffix = " - встреча уже прошла" if app.is_event_passed(event) else ""
            status_text += f" ({get_event_date_display(event)}{suffix})"
        status_text += "\n"
        status_text += f"👤 ФИО: {full_name}\n"

        grad_line = _format_graduate_type_line(graduate_type)
        if grad_line:
            status_text += grad_line
        else:
            status_text += (
                f"🎓 Выпуск: {reg['graduation_year']} {reg['class_letter']}\n"
            )

        status_text += _format_payment_status_line(reg, event, graduate_type)
        status_text += "\n"
    return status_text


@commands_menu.add_command("status", "Статус регистрации")
@router.message(Command("status"))
async def status_handler(message: Message, state: FSMContext, app: App):
    """
    Show user registration status
    """
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    await app.save_event_log(
        "command",
        {"command": "/status", "content": message.text, "chat_type": message.chat.type},
        message.from_user.id,
        message.from_user.username,
    )

    user_id = message.from_user.id
    registrations = await app.get_user_active_registrations(user_id)

    if not registrations:
        await _status_no_registrations(message, app)
        return

    events_by_reg = [await app.get_event_for_registration(reg) for reg in registrations]
    status_text = _format_registration_status_text(registrations, events_by_reg, app)

    enabled_events = await app.get_enabled_events()
    upcoming = [e for e in enabled_events if not app.is_event_passed(e)]
    if upcoming:
        status_text += "Доступные команды:\n"
        status_text += (
            "- /info - подробная информация о встречах (дата, время, адрес)\n"
        )
        status_text += "- /start - управление регистрациями\n"
        status_text += "- /pay - оплатить участие\n"
        status_text += "- /cancel_registration - отменить регистрацию\n"
    else:
        status_text += "Все встречи уже прошли. Спасибо, что были с нами! 🎓\n\n"
        status_text += "Следите за новостями в группе школы, чтобы не пропустить следующие встречи."

    await send_safe(message.chat.id, status_text, reply_markup=ReplyKeyboardRemove())


async def _status_no_registrations(message: Message, app: App):
    enabled_events = await app.get_enabled_events()
    upcoming = [e for e in enabled_events if not app.is_event_passed(e)]
    if not upcoming:
        await send_safe(
            message.chat.id,
            "Все встречи выпускников уже прошли. Спасибо, что были с нами! 🎓\n\n"
            "Следите за новостями в группе школы, чтобы не пропустить следующие встречи.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        upcoming_text = "У вас нет активных регистраций.\n\n📅 Ближайшие встречи:\n"
        for e in upcoming:
            upcoming_text += f"- {e['city']} ({e.get('date_display', '')})\n"
        upcoming_text += "\nИспользуйте /start для регистрации на встречу."
        await send_safe(
            message.chat.id, upcoming_text, reply_markup=ReplyKeyboardRemove()
        )


async def _show_single_event_welcome(
    message: Message, state: FSMContext, app: App, event, existing_registration
):
    await send_event_image(
        message.chat.id,
        event,
        caption=event.get("name", event.get("city", "")),
    )
    venue = event.get("venue") or "Уточняется"
    address = event.get("address") or "Уточняется"
    event_info = f"""
👋 Добро пожаловать!

В ближайшее время клуб друзей школы 146 проводит встречу:

📅 Дата: {event.get("date_display", "")}
⏰ Время: {event.get("time_display", "Уточняется")}
📍 Место: {venue}
🗺️ Адрес: {address}

Хотите зарегистрироваться на эту встречу?
    """
    response = await ask_user_choice(
        message.chat.id,
        event_info.strip(),
        choices={"yes": "Да, зарегистрироваться", "cancel": "Отмена"},
        state=state,
        timeout=None,
    )
    if response == "cancel" or response is None:
        await send_safe(
            message.chat.id,
            "Регистрация отменена. Если передумаете, просто напишите боту снова!",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    reuse_info = existing_registration if existing_registration else None
    await register_user(
        message, state, app, preselected_city=event["city"], reuse_info=reuse_info
    )


async def _show_multi_event_welcome(
    message: Message,
    state: FSMContext,
    app: App,
    upcoming_events,
    existing_registration,
):
    events_text = "👋 Добро пожаловать!\n\nБлижайшие встречи выпускников:\n\n"
    for event in upcoming_events:
        await send_event_image(
            message.chat.id,
            event,
            caption=event.get("name", event.get("city", "")),
        )
        venue = event.get("venue") or "Уточняется"
        address = event.get("address") or ""
        venue_line = venue
        if address:
            venue_line += f", {address}"
        events_text += (
            f"🏙️ {event['city']} ({event.get('date_display', '')})\n"
            f"   📍 {venue_line}\n\n"
        )
    events_text += "Хотите зарегистрироваться?"

    response = await ask_user_choice(
        message.chat.id,
        events_text,
        choices={"yes": "Да, зарегистрироваться", "cancel": "Отмена"},
        state=state,
        timeout=None,
    )
    if response == "cancel" or response is None:
        await send_safe(
            message.chat.id,
            "Регистрация отменена. Если передумаете, просто напишите боту снова!",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    reuse_info = existing_registration if existing_registration else None
    await register_user(message, state, app, reuse_info=reuse_info)


@commands_menu.add_command("start", "Start the bot")
@router.message(CommandStart())
@router.message(
    F.text, F.chat.type == "private", ~F.text.startswith("/")
)  # only handle private messages that are not commands
async def start_handler(message: Message, state: FSMContext, app: App):
    """
    Main scenario flow.
    """
    assert message.from_user is not None
    if message.from_user:
        await app.save_event_log(
            "command",
            {
                "command": "/start",
                "content": message.text,
                "chat_type": message.chat.type,
            },
            message.from_user.id,
            message.from_user.username,
        )

    if is_admin(message.from_user):
        result = await admin_handler(message, state, app=app)
        if result != "register":
            return

    enabled_events = await app.get_enabled_events()
    upcoming_events = [e for e in enabled_events if not app.is_event_passed(e)]

    if not upcoming_events:
        # Check for passed events that allow post-factum registration
        all_events = await app.get_all_events()
        passed_events = [e for e in all_events if e.get("status") == "passed"]
        if passed_events:
            # Show history + offer registration
            await _show_past_events_history(message, app, message.from_user.id)
            await send_safe(
                message.chat.id,
                "Если хотите зарегистрировать оплату за прошедшую встречу — используйте кнопку ниже.",
            )
            from src.user_interactions import ask_user_confirmation

            want_register = await ask_user_confirmation(
                message.chat.id,
                "Зарегистрироваться на прошедшую встречу?",
                state=state,
            )
            if want_register:
                existing_registration = await app.get_user_registration(
                    message.from_user.id
                )
                await register_user(
                    message, state, app, reuse_info=existing_registration
                )
            return
        await _show_past_events_history(message, app, message.from_user.id)
        return

    active_registrations = await app.get_user_active_registrations(message.from_user.id)

    if active_registrations:
        await handle_registered_user(message, state, active_registrations[0], app)
    else:
        existing_registration = await app.get_user_registration(message.from_user.id)
        if len(upcoming_events) == 1:
            await _show_single_event_welcome(
                message, state, app, upcoming_events[0], existing_registration
            )
        else:
            await _show_multi_event_welcome(
                message, state, app, upcoming_events, existing_registration
            )


async def _handle_admin_forwarded_payment(
    message: Message, state: FSMContext, app: App
):
    """Admin forwarded a photo/PDF from a user — process as their payment proof."""
    from aiogram.types import MessageOriginUser
    from src.routers.payment import process_payment

    origin = message.forward_origin

    if isinstance(origin, MessageOriginUser):
        sender_id = origin.sender_user.id
        sender_username = origin.sender_user.username or ""
    else:
        # Hidden user — can't identify
        sender_name = getattr(origin, "sender_user_name", "неизвестно")
        await send_safe(
            message.chat.id,
            f"Не удалось определить отправителя (скрытый профиль: {sender_name}).\n"
            "Используйте /start → «Отметить оплату» для ручного подтверждения.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Find unpaid registration for this user
    registrations = await app.get_user_active_registrations(sender_id)
    payment_regs = []
    for reg in registrations:
        event = await app.get_event_for_registration(reg)
        graduate_type_val = reg.get("graduate_type", GraduateType.GRADUATE.value)
        if (
            not is_event_free(event, graduate_type_val)
            and reg.get("payment_status") != "confirmed"
        ):
            payment_regs.append(reg)

    if not payment_regs:
        await send_safe(
            message.chat.id,
            f"У пользователя @{sender_username or sender_id} нет неоплаченных регистраций.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    selected_reg = payment_regs[0]
    await state.update_data(
        original_user_id=sender_id, original_username=sender_username
    )

    await send_safe(
        message.chat.id,
        f"Обрабатываю платёж от @{sender_username or sender_id} ({selected_reg.get('full_name', '?')})...",
    )

    await process_payment(
        message,
        state,
        selected_reg["event_id"],
        selected_reg["graduation_year"],
        skip_instructions=True,
        graduate_type=selected_reg.get("graduate_type", GraduateType.GRADUATE.value),
        pre_uploaded_response=message,
    )


@router.message(F.photo, F.chat.type == "private")
@router.message(F.document, F.chat.type == "private")
async def photo_document_handler(message: Message, state: FSMContext, app: App):
    """Auto-treat photos and PDFs as payment proof without requiring /pay first."""
    if message.from_user is None:
        return

    # Only handle PDFs for documents, ignore other file types
    if message.document and (
        not message.document.mime_type
        or message.document.mime_type != "application/pdf"
    ):
        return

    # Admin forwarded a message from a user — treat as their payment proof
    if is_admin(message.from_user) and message.forward_origin:
        await _handle_admin_forwarded_payment(message, state, app)
        return

    user_id = message.from_user.id

    registrations = await app.get_user_active_registrations(user_id)
    if not registrations:
        await send_safe(
            message.chat.id,
            "Вы еще не зарегистрированы на встречу. Используйте /start для регистрации.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    from src.routers.payment import process_payment

    # Find registrations that need payment
    payment_registrations = []
    for reg in registrations:
        event = await app.get_event_for_registration(reg)
        graduate_type_val = reg.get("graduate_type", GraduateType.GRADUATE.value)
        if (
            not is_event_free(event, graduate_type_val)
            and reg.get("payment_status") != "confirmed"
        ):
            payment_registrations.append(reg)

    if not payment_registrations:
        await send_safe(
            message.chat.id,
            "У вас нет регистраций, требующих оплаты. Если хотите что-то другое — используйте /start.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Use the first unpaid registration
    selected_reg = payment_registrations[0]
    await state.update_data(
        original_user_id=user_id, original_username=message.from_user.username
    )

    await process_payment(
        message,
        state,
        selected_reg["event_id"],
        selected_reg["graduation_year"],
        skip_instructions=True,
        graduate_type=selected_reg.get("graduate_type", GraduateType.GRADUATE.value),
        pre_uploaded_response=message,
    )
