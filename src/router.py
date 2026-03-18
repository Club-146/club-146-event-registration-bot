from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    ReplyKeyboardRemove,
    Message,
)
from dotenv import load_dotenv
from loguru import logger
from textwrap import dedent
from typing import Dict, List, Optional

from src.app import App, RegisteredUser, GraduateType
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
    """Get display date from an event dict."""
    if event:
        return event.get("date_display", "дата неизвестна")
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
        # All registrations are for archived events
        await send_safe(
            message.chat.id,
            "У вас нет активных регистраций.\nИспользуйте /start для регистрации на новую встречу.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if len(registrations) > 1:
        # User has multiple registrations
        info_text = (
            "Вы зарегистрированы на встречи выпускников в нескольких городах:\n\n"
        )

        for reg in registrations:
            city = reg["target_city"]
            event = await app.get_event_for_registration(reg)
            graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)

            # Add payment status indicator
            payment_status = ""
            if not is_event_free(event, graduate_type):
                status = reg.get("payment_status", "не оплачено")
                status_emoji = (
                    "✅"
                    if status == "confirmed"
                    else "❌"
                    if status == "declined"
                    else "⏳"
                )
                payment_status = f" - {status_emoji} {status}"

            info_text += f"• {city} ({get_event_date_display(event)}){payment_status}\n"
            info_text += f"  ФИО: {reg['full_name']}\n"
            info_text += f"  Год выпуска: {reg['graduation_year']}, Класс: {reg['class_letter']}\n"
            reg_guests = reg.get("guests", [])
            if reg_guests:
                guest_names = ", ".join(g["name"] for g in reg_guests)
                info_text += f"  👥 Гости: {guest_names}\n"
            info_text += "\n"

        info_text += "Что вы хотите сделать?"

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
        else:  # "nothing"
            await send_safe(
                message.chat.id,
                "Отлично! Ваши регистрации в силе. До встречи!\n\n"
                "Используйте команду /info для получения подробной информации о встречах (дата, время, адрес).",
                reply_markup=ReplyKeyboardRemove(),
            )
    else:
        # User has only one registration
        reg = registrations[0]
        city = reg["target_city"]
        graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
        event = await app.get_event_for_registration(reg)

        # Check if payment is needed and not confirmed
        event_is_free = is_event_free(event, graduate_type)
        needs_payment = not event_is_free and reg.get("payment_status") != "confirmed"

        # Payment status display
        payment_status = ""
        if not event_is_free:
            status = reg.get("payment_status", "не оплачено")
            status_emoji = (
                "✅"
                if status == "confirmed"
                else "❌"
                if status == "declined"
                else "⏳"
            )
            payment_status = f"Статус оплаты: {status_emoji} {status}\n"

        info_text = dedent(
            f"""
            Вы зарегистрированы на встречу выпускников:

            ФИО: {reg["full_name"]}
            """
        )

        # Show different info based on graduate type
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

        # Show guest info
        reg_guests = reg.get("guests", [])
        if reg_guests:
            info_text += f"👥 Гости ({len(reg_guests)}):\n"
            for g in reg_guests:
                info_text += f"  • {g['name']}\n"

        info_text += payment_status
        info_text += "\nЧто вы хотите сделать?"

        choices = {}
        if needs_payment:
            choices["pay"] = "Оплатить участие"

        choices.update(
            {
                "register_another": "Зарегистрироваться в другом городе",
                "cancel": "Отменить регистрацию",
            }
        )

        choices["nothing"] = "Ничего, всё в порядке"

        response = await ask_user_choice(
            message.chat.id,
            info_text,
            choices=choices,
            state=state,
            timeout=None,
        )

        # Log single registration action choice
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
            from src.routers.payment import process_payment

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

        elif response == "register_another":
            await send_safe(
                message.chat.id, "Давайте зарегистрируемся в другом городе."
            )
            await register_user(message, state, app, reuse_info=registration)

        else:  # "nothing"
            await send_safe(
                message.chat.id,
                "Отлично! Ваша регистрация в силе. До встречи!\n\nИспользуйте команду /info для получения подробной информации о встречах (дата, время, адрес).",
                reply_markup=ReplyKeyboardRemove(),
            )


async def _edit_guests(
    message: Message, state: FSMContext, reg: Dict, event: Dict, app: App
):
    """Allow user to add/change/remove guests on an existing registration."""
    from src.user_interactions import ask_user_raw

    assert message.from_user is not None
    user_id = message.from_user.id
    username = message.from_user.username or ""
    city = reg["target_city"]
    reg_event_id = reg["event_id"]

    max_guests = event.get("max_guests_per_person", 3)
    existing_guests = reg.get("guests", [])

    # Build guest count choices
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
        # Remove all guests
        await app.save_registration_guests(user_id, reg_event_id, [])
        await send_safe(message.chat.id, "👥 Гости убраны.")
        await app.save_event_log(
            "edit_guests",
            {"action": "remove_all_guests", "city": city},
            user_id,
            username,
        )
        return

    # Calculate guest price
    graduation_year = reg.get("graduation_year", 2000)
    graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
    reg_amount, _, _, _ = app.calculate_event_payment(
        event, graduation_year, graduate_type
    )
    guest_price_regular, guest_price_discounted = app.calculate_guest_price(
        event, reg_amount
    )

    # Collect guest names
    guests = []
    for i in range(1, guest_count + 1):
        # Pre-fill with existing name if available
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

    # Save
    await app.save_registration_guests(user_id, reg_event_id, guests)

    # Show summary
    guest_summary = f"👥 Гости ({len(guests)}):\n"
    for i, g in enumerate(guests, 1):
        guest_summary += f"  {i}. {g['name']} — {g['price']}₽\n"
    guest_total = sum(g["price"] for g in guests)
    guest_total_discounted = sum(g.get("price_discounted", g["price"]) for g in guests)
    if guest_total != guest_total_discounted:
        guest_summary += (
            f"\nОбщая стоимость за гостей: {guest_total}₽"
            f"\nПри ранней регистрации: {guest_total_discounted}₽"
        )
    else:
        guest_summary += f"\nОбщая стоимость за гостей: {guest_total}₽"
    await send_safe(message.chat.id, guest_summary)

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


async def manage_registrations(
    message: Message, state: FSMContext, registrations, app: App
):
    """Allow user to manage multiple registrations"""
    assert message.from_user is not None

    # Create choices for each registration (keyed by event_id)
    choices = {}
    for reg in registrations:
        city = reg["target_city"]
        reg_eid = reg["event_id"]
        choices[reg_eid] = f"Управлять регистрацией в городе {city}"

    choices["all"] = "Отменить все регистрации"
    choices["back"] = "Вернуться назад"

    # Log entering registration management
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

    # Log button click
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

    elif response == "back":
        await handle_registered_user(message, state, registrations[0], app)

    else:
        # Manage specific registration by event_id
        selected_event_id = response
        assert selected_event_id is not None
        reg = next(r for r in registrations if r["event_id"] == selected_event_id)
        city = reg["target_city"]
        event = await app.get_event_for_registration(reg)

        # Show current guests in info
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
        # Add "edit guests" option if the event supports guests
        if event and event.get("guests_enabled"):
            if existing_guests:
                choices["guests"] = "👥 Изменить гостей"
            else:
                choices["guests"] = "👥 Добавить гостей"
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
                {
                    "button": action,
                    "context": "city_registration_management",
                    "city": city,
                },
                message.from_user.id,
                message.from_user.username,
            )

        if action == "guests":
            if event is None:
                await send_safe(
                    message.chat.id,
                    "Произошла ошибка: не удалось найти мероприятие.",
                )
                return
            await _edit_guests(message, state, reg, event, app)
            # Refresh registrations and return to management
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

        else:  # "back"
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

    # Initialize log messages list for this user if not exists
    if user_id not in log_messages:
        log_messages[user_id] = []

    # Initialize all variables that could be unbound
    full_name = None
    graduation_year = None
    class_letter = None
    location = None
    graduate_type = (
        GraduateType.GRADUATE
    )  # Default type - will be overridden in specific cases

    # Log registration start
    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Начало регистрации",
        f"Предвыбранный город: {preselected_city}, Повторное использование данных: {'Да' if reuse_info else 'Нет'}",
    )
    if log_msg:
        log_messages[user_id].append(log_msg)

    # Get existing registrations to avoid duplicates
    existing_registrations = await app.get_user_registrations(user_id)
    existing_event_ids = [
        reg["event_id"] for reg in existing_registrations if reg.get("event_id")
    ]

    # step 1 - greet user, ask location
    # Load available events from DB
    enabled_events = await app.get_enabled_events()
    # Build a map of event_id -> event for quick lookup
    event_map = {str(e["_id"]): e for e in enabled_events}

    selected_event = None

    if preselected_city:
        # Use preselected city if provided - find matching event
        location = preselected_city
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
            return

        # Log preselected city
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Выбор города",
            f"Предвыбранный город: {preselected_city}",
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

    if not selected_event and not location:
        # Filter available events: not passed, not already registered
        available_events = [
            e
            for e in enabled_events
            if not app.is_event_passed(e) and str(e["_id"]) not in existing_event_ids
        ]

        if not available_events:
            await send_safe(
                message.chat.id,
                "К сожалению, все встречи уже прошли или вы уже зарегистрированы во всех доступных городах.\n\n"
                "Следите за новостями в группе школы, чтобы не пропустить следующие встречи.",
                reply_markup=ReplyKeyboardRemove(),
            )
            log_msg = await app.log_registration_step(
                user_id,
                username,
                "Нет доступных городов",
                "Пользователь уже зарегистрирован во всех городах или все встречи прошли",
            )
            if log_msg:
                log_messages[user_id].append(log_msg)
            return

        # Build choices from available events
        available_cities = {}
        for e in available_events:
            eid = str(e["_id"])
            available_cities[eid] = f"{e['city']} ({e.get('date_display', '')})"
        available_cities["cancel"] = "Отменить регистрацию"

        question = dedent(
            """
            Выберите город, где планируете посетить встречу:
            """
        )

        response = await ask_user_choice(
            message.chat.id,
            question,
            choices=available_cities,
            state=state,
            timeout=None,
        )

        if await handle_cancel_option(response, message, state):
            return

        if response is None:
            await send_safe(
                message.chat.id,
                "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        # Response is an event_id
        selected_event = event_map.get(response)
        if selected_event:
            location = selected_event["city"]
        else:
            location = response

        city_name = (
            selected_event["city"]
            if selected_event
            else (location if location else response)
        )
        log_msg = await app.log_registration_step(
            user_id, username, "Выбор города", f"Выбранный город: {city_name}"
        )

        await app.save_event_log(
            "registration_step",
            {
                "step": "city_selection",
                "city": city_name,
                "event_id": str(selected_event["_id"]) if selected_event else None,
                "existing_event_ids": existing_event_ids,
            },
            user_id,
            username,
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

    # Determine the city name for display
    reg_city_name = (
        selected_event["city"] if selected_event else (location if location else "")
    )

    # If we have info to reuse, skip asking for name and class
    if reuse_info:
        full_name = reuse_info["full_name"]
        graduation_year = reuse_info["graduation_year"]
        class_letter = reuse_info["class_letter"]
        graduate_type = GraduateType(
            reuse_info.get("graduate_type", GraduateType.GRADUATE.value)
        )

        # Confirm reusing the information
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

        # Handle cancel
        if await handle_cancel_option(confirm, message, state):
            return

        # Log reuse decision
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Повторное использование данных",
            f"Решение: {'Использовать существующие данные' if confirm == 'yes' else 'Ввести новые данные'}",
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

        if confirm == "no":
            # User wants to enter new info
            reuse_info = None

    # If not reusing info, ask for it
    if not reuse_info:
        # Ask for full name with validation
        full_name = None
        while full_name is None:
            question = dedent(
                """
                Представьтесь, пожалуйста.
                Можно имя и фамилию, можно полные ФИО
                """
            )

            response = await ask_user(
                message.chat.id,
                question,
                state=state,
                timeout=None,
            )

            # Handle timeout/None response
            if response is None:
                await send_safe(
                    message.chat.id,
                    "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return

            # Validate full name
            valid, error = app.validate_full_name(response)
            if valid:
                full_name = response
            else:
                await send_safe(
                    message.chat.id, f"❌ {error} Пожалуйста, попробуйте еще раз."
                )

        # Log full name
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Ввод ФИО",
            f"ФИО: {full_name}",
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

        # Ask for graduation year and class letter with validation
        graduation_year = None
        class_letter = None

        while graduation_year is None or class_letter is None or not class_letter:
            if graduation_year is not None and class_letter is None:
                # We have a year but need a class letter
                question = "А букву класса?"
            else:
                question = dedent(
                    """
                    Пожалуйста, введите год выпуска и букву класса.
                    Например, "2003 Б".
                    
                    <tg-spoiler>Если вы учитель школы 146 (нынешний или бывший), нажмите: /i_am_a_teacher
                    Если вы не выпускник, но друг школы 146 - нажмите: /i_am_a_friend
                    Если вы организатор встречи - нажмите: /i_am_an_organizer</tg-spoiler>
                    """
                )

            response = await ask_user(
                message.chat.id,
                question,
                state=state,
                timeout=None,
            )

            # Handle timeout/None response
            if response is None:
                await send_safe(
                    message.chat.id,
                    "⏰ Время ожидания истекло. Пожалуйста, начните регистрацию заново с команды /start",
                    reply_markup=ReplyKeyboardRemove(),
                )
                return

            # Check for special commands
            if response == "/i_am_a_teacher":
                # User is a teacher
                graduation_year = 0  # Special value for teachers
                class_letter = "Т"  # "Т" for "Учитель"
                graduate_type = GraduateType.TEACHER

                # Log teacher status
                log_msg = await app.log_registration_step(
                    user_id,
                    username,
                    "Статус участника",
                    "Учитель",
                )
                if log_msg:
                    log_messages[user_id].append(log_msg)

                # await send_safe(
                #     message.chat.id,
                #     "Вы зарегистрированы как учитель. Участие для учителей бесплатное.",
                # )
                break

            elif response == "/i_am_a_friend":
                # User is not a graduate
                graduation_year = 2000  # Special value for non-graduates
                class_letter = "Н"  # "Н" for "Не выпускник"
                graduate_type = GraduateType.NON_GRADUATE

                # Log non-graduate status
                log_msg = await app.log_registration_step(
                    user_id,
                    username,
                    "Статус участника",
                    "Не выпускник",
                )
                if log_msg:
                    log_messages[user_id].append(log_msg)

                await send_safe(
                    message.chat.id, "Вы зарегистрированы как друг школы 146!"
                )
                break

            elif response == "/i_am_an_organizer":
                # User is an organizer
                graduation_year = 1000  # Special value for organizers
                class_letter = "О"  # "О" for "Организатор"
                graduate_type = GraduateType.ORGANIZER

                # Log organizer status
                log_msg = await app.log_registration_step(
                    user_id,
                    username,
                    "Статус участника",
                    "Организатор",
                )
                if log_msg:
                    log_messages[user_id].append(log_msg)

                await send_safe(
                    message.chat.id, "Вы зарегистрированы как организатор встречи!"
                )
                break

            # If we already have a year and just need the letter
            elif graduation_year is not None and class_letter is None:
                # Validate just the class letter
                class_letter = response.strip().split()[-1]
                valid, error = app.validate_class_letter(response)
                if valid:
                    class_letter = response.upper()
                else:
                    await send_safe(
                        message.chat.id, f"❌ {error} Пожалуйста, попробуйте еще раз."
                    )
            else:
                # Parse and validate both year and letter
                year, letter, error = app.parse_graduation_year_and_class_letter(
                    response
                )

                if error:
                    await send_safe(message.chat.id, f"❌ {error}")
                    # If we got a valid year but no letter, save the year
                    if year is not None and letter == "":
                        graduation_year = year
                else:
                    graduation_year = year
                    class_letter = letter
                    graduate_type = GraduateType.GRADUATE

        # Log graduation info
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Ввод года выпуска и класса",
            f"Год: {graduation_year}, Класс: {class_letter}",
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

    # Determine the target_city value
    target_city_value = ""
    if location:
        target_city_value = location
    elif selected_event:
        target_city_value = selected_event["city"]

    # Internal validation - log error but don't expose to user
    if not all([full_name, graduation_year is not None, class_letter, graduate_type]):
        logger.error(
            f"Registration validation failed - missing required fields: "
            f"full_name={full_name}, "
            f"graduation_year={graduation_year}, "
            f"class_letter={class_letter}, "
            f"graduate_type={graduate_type}"
        )

    # Save the registration with event_id
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
    await app.save_registered_user(
        registered_user,
        user_id=user_id,
        username=username,
    )

    # --- Guest step ---
    guests = []
    if selected_event and selected_event.get("guests_enabled"):
        max_guests = selected_event.get("max_guests_per_person", 3)

        # Build guest count choices
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
            int(guest_count_resp)
            if guest_count_resp and guest_count_resp.isdigit()
            else 0
        )

        if guest_count > 0:
            # Calculate guest price
            if selected_event:
                reg_amount, _, _, _ = app.calculate_event_payment(
                    selected_event, graduation_year, graduate_type.value
                )
            else:
                reg_amount = 0
            guest_price_regular, guest_price_discounted = app.calculate_guest_price(
                selected_event, reg_amount
            )

            for i in range(1, guest_count + 1):
                from src.user_interactions import ask_user_raw

                name_resp = await ask_user_raw(
                    message.chat.id,
                    f"Имя гостя {i}:",
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

            # Show guest summary
            guest_summary = f"👥 Гости ({len(guests)}):\n"
            for i, g in enumerate(guests, 1):
                guest_summary += f"  {i}. {g['name']} — {g['price']}₽\n"
            guest_total = sum(g["price"] for g in guests)
            guest_total_discounted = sum(
                g.get("price_discounted", g["price"]) for g in guests
            )
            if guest_total != guest_total_discounted:
                guest_summary += (
                    f"\nОбщая стоимость за гостей: {guest_total}₽"
                    f"\nПри ранней регистрации: {guest_total_discounted}₽"
                )
            else:
                guest_summary += f"\nОбщая стоимость за гостей: {guest_total}₽"
            await send_safe(message.chat.id, guest_summary)

        # Log guest step
        log_msg = await app.log_registration_step(
            user_id,
            username,
            "Гости",
            f"Количество: {guest_count}, Имена: {', '.join(g['name'] for g in guests) if guests else 'нет'}",
        )
        if log_msg:
            log_messages[user_id].append(log_msg)

    # Log registration completion
    log_msg = await app.log_registration_step(
        user_id,
        username,
        "Регистрация завершена",
        f"Город: {reg_city_name}, ФИО: {full_name}, Выпуск: {graduation_year} {class_letter}, Гости: {len(guests)}",
    )
    if log_msg:
        log_messages[user_id].append(log_msg)

    # Log to events chat
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

    # Clear log messages
    await delete_log_messages(user_id)

    # Determine event_id for DB operations
    event_id_for_db = str(selected_event["_id"]) if selected_event else ""

    # Save guest data to registration
    if guests:
        await app.save_registration_guests(user_id, event_id_for_db, guests)

    # Determine prepositional city name for confirmation
    city_prep = ""
    if selected_event:
        city_prep = selected_event.get("city_prepositional", reg_city_name)
    elif location:
        from src.app import CITY_PREPOSITIONAL_MAP

        city_prep = CITY_PREPOSITIONAL_MAP.get(location, location)

    date_display = get_event_date_display(selected_event) if selected_event else ""

    confirmation_msg = (
        f"Спасибо, {full_name}!\n"
        f"Вы зарегистрированы на встречу выпускников школы 146 "
        f"в {city_prep} {date_display}. "
    )
    if guests:
        confirmation_msg += f"\nС вами {len(guests)} гост{'ь' if len(guests) == 1 else 'ей' if len(guests) >= 5 else 'я'}. "

    # Check if event is free for this participant
    event_is_free = (
        is_event_free(selected_event, graduate_type.value) if selected_event else False
    )

    if event_is_free or graduate_type in (GraduateType.TEACHER, GraduateType.ORGANIZER):
        # Auto-confirm payment for free participants
        if graduate_type == GraduateType.TEACHER:
            comment = "Автоматически подтверждено (учитель)"
            confirmation_msg += (
                "\nДля учителей участие бесплатное. Спасибо за вашу работу!"
            )
        elif graduate_type == GraduateType.ORGANIZER:
            comment = "Автоматически подтверждено (организатор)"
            confirmation_msg += (
                "\nДля организаторов участие бесплатное. Спасибо за вашу помощь!"
            )
        else:
            comment = "Автоматически подтверждено (бесплатное мероприятие)"
            confirmation_msg += "\nДля этой встречи оплата не требуется. Все расходы участники несут самостоятельно."

        # For free registrants, guests still pay if guest_price_minimum > 0
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
                confirmation_msg + "\nСейчас пришлем информацию об оплате за гостей...",
            )

            from src.routers.payment import process_payment

            await state.update_data(
                original_user_id=user_id, original_username=username
            )
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

            await send_safe(
                message.chat.id,
                confirmation_msg,
                reply_markup=ReplyKeyboardRemove(),
            )

            await app.export_registered_users_to_google_sheets()
    else:
        # Regular flow - needs payment
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

        # Add guest totals to payment amounts
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

        confirmation_msg += "Сейчас пришлем информацию об оплате..."
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

    # Log the cancel registration command
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
        # User has only one registration, ask for confirmation
        reg = registrations[0]
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
            # Cancel registration
            await app.delete_user_registration(user_id, reg_event_id)

            # Log cancellation
            await app.log_registration_canceled(
                user_id,
                message.from_user.username or "",
                full_name,
                city,
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
    else:
        # User has multiple registrations, ask which one to cancel
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
            # Get user info for logging before deleting
            user_reg = registrations[0]
            full_name = user_reg.get("full_name", "Unknown")

            # Cancel all registrations
            await app.delete_user_registration(user_id)

            # Log cancellation
            await app.log_registration_canceled(
                user_id,
                message.from_user.username or "",
                full_name,
                None,  # Indicates all cities
            )

            await send_safe(
                message.chat.id,
                "Все ваши регистрации отменены. Если передумаете, используйте /start чтобы зарегистрироваться снова.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            # Cancel specific registration by event_id
            selected_eid = response
            reg = next(r for r in registrations if r["event_id"] == selected_eid)
            full_name = reg["full_name"]
            city = reg["target_city"]

            # Cancel registration
            await app.delete_user_registration(user_id, selected_eid)

            # Log cancellation
            await app.log_registration_canceled(
                user_id,
                message.from_user.username or "",
                full_name,
                city,
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

    # Log the info command
    await app.save_event_log(
        "command",
        {"command": "/info", "content": message.text, "chat_type": message.chat.type},
        message.from_user.id,
        message.from_user.username,
    )

    # Create info text with details from DB events
    info_text = "📅 <b>Информация о встречах выпускников 146</b>\n\n"

    # Get active (non-archived) events
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
            if venue:
                info_text += f"🏢 Место: {venue}\n"
            else:
                info_text += "🏢 Место: Уточняется\n"
            if address:
                info_text += f"📍 Адрес: {address}\n"
            else:
                info_text += "📍 Адрес: Уточняется\n"

        info_text += "\n"

    if has_upcoming:
        info_text += "Используйте /start для регистрации на встречу.\n"
        info_text += "Используйте /pay для оплаты участия после регистрации.\n"

    await send_safe(message.chat.id, info_text, parse_mode="HTML")


@commands_menu.add_command("status", "Статус регистрации")
@router.message(Command("status"))
async def status_handler(message: Message, state: FSMContext, app: App):
    """
    Show user registration status
    """
    if message.from_user is None:
        logger.error("Message from_user is None")
        return

    # Log the status command
    await app.save_event_log(
        "command",
        {"command": "/status", "content": message.text, "chat_type": message.chat.type},
        message.from_user.id,
        message.from_user.username,
    )

    user_id = message.from_user.id

    # Get active registrations only (exclude archived events)
    registrations = await app.get_user_active_registrations(user_id)

    if not registrations:
        # Check if there are any enabled upcoming events
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
                message.chat.id,
                upcoming_text,
                reply_markup=ReplyKeyboardRemove(),
            )
        return

    status_text = "📋 Ваши регистрации:\n\n"

    for reg in registrations:
        city = reg["target_city"]
        full_name = reg["full_name"]
        graduate_type = reg.get("graduate_type", GraduateType.GRADUATE.value)
        event = await app.get_event_for_registration(reg)

        # Add city and date information
        status_text += f"🏙️ Город: {city}"
        if event:
            if app.is_event_passed(event):
                status_text += (
                    f" ({get_event_date_display(event)} - встреча уже прошла)"
                )
            else:
                status_text += f" ({get_event_date_display(event)})"
        status_text += "\n"

        # Add personal information
        status_text += f"👤 ФИО: {full_name}\n"

        # Show different info based on graduate type
        if graduate_type == GraduateType.TEACHER.value:
            status_text += "👨‍🏫 Статус: Учитель\n"
        elif graduate_type == GraduateType.NON_GRADUATE.value:
            status_text += "👥 Статус: Не выпускник\n"
        elif graduate_type == GraduateType.ORGANIZER.value:
            status_text += "🛠️ Статус: Организатор\n"
        else:
            status_text += (
                f"🎓 Выпуск: {reg['graduation_year']} {reg['class_letter']}\n"
            )

        # Add payment status
        event_free = is_event_free(event, graduate_type)
        if event_free:
            if graduate_type == GraduateType.TEACHER.value:
                status_text += "💰 Оплата: Бесплатно (учитель)\n"
            elif graduate_type == GraduateType.ORGANIZER.value:
                status_text += "💰 Оплата: Бесплатно (организатор)\n"
            else:
                status_text += "💰 Оплата: За свой счет\n"
        else:
            payment_status = reg.get("payment_status", "не оплачено")
            status_emoji = (
                "✅"
                if payment_status == "confirmed"
                else "❌"
                if payment_status == "declined"
                else "⏳"
            )
            status_text += f"💰 Статус оплаты: {status_emoji} {payment_status}\n"

            if "payment_amount" in reg:
                status_text += f"💵 Сумма оплаты: {reg['payment_amount']} руб.\n"
            elif payment_status == "pending" and "discounted_payment_amount" in reg:
                status_text += (
                    f"💵 Ожидаемая сумма: {reg['discounted_payment_amount']} руб.\n"
                )

        status_text += "\n"

    # Check for upcoming events
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
    # Log the start command
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

    # Check for available events from DB
    enabled_events = await app.get_enabled_events()
    upcoming_events = [e for e in enabled_events if not app.is_event_passed(e)]

    if not upcoming_events:
        await send_safe(
            message.chat.id,
            "Все встречи выпускников уже прошли. Спасибо, что были с нами! 🎓\n\n"
            "Следите за новостями в группе школы, чтобы не пропустить следующие встречи.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    # Check if user has active registrations
    active_registrations = await app.get_user_active_registrations(message.from_user.id)

    if active_registrations:
        await handle_registered_user(message, state, active_registrations[0], app)
    else:
        # New user or user with only archived registrations
        # Get old registration data to pre-fill
        existing_registration = await app.get_user_registration(message.from_user.id)

        if len(upcoming_events) == 1:
            # Single event - show info and ask to register
            event = upcoming_events[0]
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
                choices={
                    "yes": "Да, зарегистрироваться",
                    "cancel": "Отмена",
                },
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
                message,
                state,
                app,
                preselected_city=event["city"],
                reuse_info=reuse_info,
            )
        else:
            # Multiple events - show list
            events_text = "👋 Добро пожаловать!\n\nБлижайшие встречи выпускников:\n\n"
            for event in upcoming_events:
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
                choices={
                    "yes": "Да, зарегистрироваться",
                    "cancel": "Отмена",
                },
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
