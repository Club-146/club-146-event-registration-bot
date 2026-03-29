"""Admin event management router: /create_event and /manage_events commands."""

from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from src.app import (
    App,
    CITY_PREPOSITIONAL_MAP,
    EventStatus,
    PricingType,
)
from botspot import commands_menu
from botspot.components.qol.bot_commands_menu import Visibility
from src.user_interactions import ask_user_choice, ask_user_confirmation, ask_user_raw
from botspot.utils import send_safe
from botspot.utils.admin_filter import AdminFilter

events_router = Router()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEASON_NAMES = {
    (3, 5): "Весенняя встреча",
    (6, 8): "Летняя встреча",
    (9, 11): "Осенняя встреча",
    (12, 2): "Зимняя встреча",
}


def _suggest_event_name(city: str, date: datetime) -> str:
    month = date.month
    for (start, end), name in SEASON_NAMES.items():
        if start <= end:
            if start <= month <= end:
                return f"{city} ({name} {date.year})"
        else:
            if month >= start or month <= end:
                return f"{city} ({name} {date.year})"
    return f"{city} (Встреча {date.year})"


def _format_pricing(event: dict) -> str:
    pricing_type = event.get("pricing_type", "free")
    if pricing_type == PricingType.FREE:
        return "Бесплатно"
    elif pricing_type == PricingType.FORMULA:
        base = event.get("price_formula_base", 0)
        rate = event.get("price_formula_rate", 0)
        ref = event.get("price_formula_reference_year", datetime.now().year)
        step = event.get("price_formula_step", 1)
        if step > 1:
            return f"{base} + {rate} × (({ref} − год выпуска) ÷ {step})"
        return f"{base} + {rate} × ({ref} − год выпуска)"
    elif pricing_type == PricingType.FIXED_BY_YEAR:
        return "Фиксированная по годам"
    return "Неизвестно"


def _format_event_summary(event: dict, reg_count: int = 0) -> str:
    lines = []
    lines.append(f"📋 <b>{event.get('name', 'Без названия')}</b>")
    lines.append(f"🏙️ Город: {event.get('city', '?')}")
    lines.append(f"📆 Дата: {event.get('date_display', '?')}")
    lines.append(f"🕐 Время: {event.get('time_display', '?')}")
    venue = event.get("venue") or "Не указано"
    address = event.get("address") or "Не указано"
    lines.append(f"📍 Место: {venue}")
    lines.append(f"📍 Адрес: {address}")
    lines.append(f"💰 Оплата: {_format_pricing(event)}")

    free_for = event.get("free_for_types", [])
    if free_for:
        type_names = {"TEACHER": "Учителя", "ORGANIZER": "Организаторы"}
        names = [type_names.get(t, t) for t in free_for]
        lines.append(
            f"🎓 Бесплатно для: {', '.join(n for n in names if n is not None)}"
        )

    # Early bird info
    eb_discount = event.get("early_bird_discount", 0)
    eb_deadline = event.get("early_bird_deadline")
    if eb_discount > 0:
        deadline_str = eb_deadline.strftime("%d.%m.%Y") if eb_deadline else "не указан"
        lines.append(f"🐦 Ранняя регистрация: скидка {eb_discount}₽ до {deadline_str}")

    # Guest settings
    if event.get("guests_enabled"):
        max_g = event.get("max_guests_per_person", 3)
        min_p = event.get("guest_price_minimum", 0)
        guest_info = f"до {max_g} чел."
        if min_p > 0:
            guest_info += f", мин. {min_p}₽"
        else:
            guest_info += ", цена = как у регистранта"
        lines.append(f"👥 Гости: {guest_info}")
    else:
        lines.append("👥 Гости: Нет")

    status_map = {
        "upcoming": "Открыта для регистрации",
        "registration_closed": "Регистрация закрыта",
        "passed": "Прошла",
        "archived": "В архиве",
    }
    status = event.get("status", "upcoming")
    enabled = event.get("enabled", False)
    status_text = status_map.get(status, status)
    if status == "upcoming" and not enabled:
        status_text = "Регистрация приостановлена"
    lines.append(f"📊 Статус: {status_text}")

    if reg_count > 0:
        lines.append(f"👥 Регистраций: {reg_count}")

    return "\n".join(lines)


MONTH_NAMES_RU = {
    1: "Января",
    2: "Февраля",
    3: "Марта",
    4: "Апреля",
    5: "Мая",
    6: "Июня",
    7: "Июля",
    8: "Августа",
    9: "Сентября",
    10: "Октября",
    11: "Ноября",
    12: "Декабря",
}

DAY_OF_WEEK_RU = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


def _make_date_display(dt: datetime) -> str:
    day_name = DAY_OF_WEEK_RU.get(dt.weekday(), "")
    month_name = MONTH_NAMES_RU.get(dt.month, "")
    return f"{dt.day} {month_name}, {day_name}"


# ---------------------------------------------------------------------------
# create_event helpers
# ---------------------------------------------------------------------------


async def _collect_city(chat_id: int, state: FSMContext) -> tuple[str, str] | None:
    """Ask for city name and its prepositional form. Returns (city, city_prep) or None."""
    city_resp = await ask_user_raw(
        chat_id,
        '🏙️ В каком городе будет встреча?\nВведите название города (например, "Москва"):',
        state=state,
        timeout=None,
    )
    if not city_resp or not city_resp.text:
        return None
    city = city_resp.text.strip()

    city_prep = CITY_PREPOSITIONAL_MAP.get(city)
    if not city_prep:
        prep_resp = await ask_user_raw(
            chat_id,
            f'Не могу автоматически просклонять "{city}".\n'
            f'Как сказать "в ___"? (например, для Москвы → "Москве")',
            state=state,
            timeout=None,
        )
        if not prep_resp or not prep_resp.text:
            return None
        city_prep = prep_resp.text.strip()

    return city, city_prep


async def _collect_date_and_name(
    chat_id: int, state: FSMContext, city: str
) -> tuple[datetime, str, str] | None:
    """Ask for date, time, and name. Returns (event_date, time_display, event_name) or None."""
    date_resp = await ask_user_raw(
        chat_id,
        "🗓️ Укажите дату встречи (ДД.ММ.ГГГГ):",
        state=state,
        timeout=None,
    )
    if not date_resp or not date_resp.text:
        return None

    try:
        event_date = datetime.strptime(date_resp.text.strip(), "%d.%m.%Y")
    except ValueError:
        await send_safe(chat_id, "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
        return None

    suggested_name = _suggest_event_name(city, event_date)
    name_resp = await ask_user_raw(
        chat_id,
        f'📝 Как назвать встречу?\nПредлагаю: "{suggested_name}"\n'
        f'Нажмите Enter или введите своё название (или отправьте "ок" чтобы принять):',
        state=state,
        timeout=None,
    )
    if not name_resp or not name_resp.text:
        event_name = suggested_name
    else:
        text = name_resp.text.strip()
        if text.lower() in ("ок", "ok", "да", ""):
            event_name = suggested_name
        else:
            event_name = text

    time_resp = await ask_user_raw(
        chat_id,
        '🕐 Укажите время начала (например, "18:00" или "18:00-24:00"):',
        state=state,
        timeout=None,
    )
    if not time_resp or not time_resp.text:
        return None
    time_display = time_resp.text.strip()

    try:
        hour = int(time_display.split(":")[0])
        minute = (
            int(time_display.split(":")[1].split("-")[0]) if ":" in time_display else 0
        )
        event_date = event_date.replace(hour=hour, minute=minute)
    except (ValueError, IndexError):
        pass

    return event_date, time_display, event_name


async def _collect_venue_info(
    chat_id: int, state: FSMContext
) -> tuple[str | None, str | None]:
    """Ask for venue and address. Returns (venue, address)."""
    venue_resp = await ask_user_raw(
        chat_id,
        '📍 Укажите место проведения (или "пропустить"):',
        state=state,
        timeout=None,
    )
    venue = None
    if venue_resp and venue_resp.text:
        text = venue_resp.text.strip()
        if text.lower() not in ("пропустить", "skip", "-"):
            venue = text

    address_resp = await ask_user_raw(
        chat_id,
        '📍 Укажите адрес (или "пропустить"):',
        state=state,
        timeout=None,
    )
    address = None
    if address_resp and address_resp.text:
        text = address_resp.text.strip()
        if text.lower() not in ("пропустить", "skip", "-"):
            address = text

    return venue, address


async def _collect_pricing_config(
    chat_id: int, state: FSMContext, event_date: datetime
) -> dict | None:
    """Ask for pricing type and formula params. Returns partial event_data dict or None on cancel."""
    pricing_choice = await ask_user_choice(
        chat_id,
        "💰 Выберите тип оплаты:",
        choices={
            "formula": "Формула",
            "free": "Бесплатно",
        },
        state=state,
        timeout=None,
    )

    if pricing_choice != "formula":
        return {"pricing_type": PricingType.FREE}

    base_resp = await ask_user_raw(
        chat_id,
        "💰 Укажите базовую стоимость (в рублях):",
        state=state,
        timeout=None,
    )
    if not base_resp or not base_resp.text:
        return None
    try:
        price_base = int(base_resp.text.strip())
    except ValueError:
        await send_safe(chat_id, "❌ Введите число.")
        return None

    rate_resp = await ask_user_raw(
        chat_id,
        "💰 Укажите надбавку за каждый год выпуска:",
        state=state,
        timeout=None,
    )
    if not rate_resp or not rate_resp.text:
        return None
    try:
        price_rate = int(rate_resp.text.strip())
    except ValueError:
        await send_safe(chat_id, "❌ Введите число.")
        return None

    step_resp = await ask_user_raw(
        chat_id,
        "💰 Шаг группировки по годам (1 = каждый год, 3 = по 3 года). По умолчанию 1:",
        state=state,
        timeout=None,
    )
    price_step = 1
    if step_resp and step_resp.text:
        try:
            price_step = max(1, int(step_resp.text.strip()))
        except ValueError:
            price_step = 1

    return {
        "pricing_type": PricingType.FORMULA,
        "price_formula_base": price_base,
        "price_formula_rate": price_rate,
        "price_formula_reference_year": event_date.year,
        "price_formula_step": price_step,
    }


async def _collect_free_for_types(chat_id: int, state: FSMContext) -> list[str]:
    """Ask which participant types get free entry. Returns list of type strings."""
    free_choice = await ask_user_choice(
        chat_id,
        "🎓 Для каких типов участников бесплатно?",
        choices={
            "teachers_organizers": "Учителя + Организаторы",
            "teachers": "Только учителя",
            "nobody": "Никто (все платят)",
        },
        state=state,
        timeout=None,
    )
    if free_choice == "teachers_organizers":
        return ["TEACHER", "ORGANIZER"]
    elif free_choice == "teachers":
        return ["TEACHER"]
    return []


async def _collect_early_bird(
    chat_id: int, state: FSMContext, pricing_choice: str
) -> dict:
    """Ask for early bird discount settings. Returns partial event_data dict."""
    if pricing_choice != "formula":
        return {"early_bird_discount": 0, "early_bird_deadline": None}

    eb_resp = await ask_user_raw(
        chat_id,
        "🐦 Скидка за раннюю регистрацию (в рублях, 0 = без скидки):",
        state=state,
        timeout=None,
    )
    early_bird_discount = 0
    if eb_resp and eb_resp.text:
        try:
            early_bird_discount = max(0, int(eb_resp.text.strip()))
        except ValueError:
            early_bird_discount = 0

    if early_bird_discount <= 0:
        return {"early_bird_discount": 0, "early_bird_deadline": None}

    deadline_resp = await ask_user_raw(
        chat_id,
        "🐦 Дедлайн ранней регистрации (ДД.ММ.ГГГГ):",
        state=state,
        timeout=None,
    )
    early_bird_deadline = None
    if deadline_resp and deadline_resp.text:
        try:
            early_bird_deadline = datetime.strptime(
                deadline_resp.text.strip(), "%d.%m.%Y"
            )
        except ValueError:
            await send_safe(
                chat_id,
                "⚠️ Неверный формат даты, скидка будет без дедлайна.",
            )

    return {
        "early_bird_discount": early_bird_discount,
        "early_bird_deadline": early_bird_deadline,
    }


async def _collect_guest_settings(chat_id: int, state: FSMContext) -> dict:
    """Ask for guest settings. Returns partial event_data dict."""
    guests_choice = await ask_user_choice(
        chat_id,
        "👥 Разрешить участникам приводить гостей (+1)?",
        choices={
            "yes": "Да",
            "no": "Нет",
        },
        state=state,
        timeout=None,
    )

    if guests_choice != "yes":
        return {
            "guests_enabled": False,
            "max_guests_per_person": 3,
            "guest_price_minimum": 0,
        }

    max_guests_resp = await ask_user_raw(
        chat_id,
        "Максимальное количество гостей на человека (по умолчанию 3):",
        state=state,
        timeout=None,
    )
    max_guests = 3
    if max_guests_resp and max_guests_resp.text:
        try:
            max_guests = max(1, int(max_guests_resp.text.strip()))
        except ValueError:
            max_guests = 3

    min_price_resp = await ask_user_raw(
        chat_id,
        "Минимальная цена за гостя в рублях (0 = такая же, как у регистранта):",
        state=state,
        timeout=None,
    )
    min_price = 0
    if min_price_resp and min_price_resp.text:
        try:
            min_price = max(0, int(min_price_resp.text.strip()))
        except ValueError:
            min_price = 0

    return {
        "guests_enabled": True,
        "max_guests_per_person": max_guests,
        "guest_price_minimum": min_price,
    }


# ---------------------------------------------------------------------------
# /create_event
# ---------------------------------------------------------------------------


@commands_menu.add_command(
    "create_event", "Создать новую встречу", visibility=Visibility.ADMIN_ONLY
)
@events_router.message(Command("create_event"), AdminFilter())
async def create_event_handler(message: Message, state: FSMContext, app: App):
    """Guided event creation flow (admin only)."""
    if not message.from_user:
        return

    city_result = await _collect_city(message.chat.id, state)
    if not city_result:
        await send_safe(message.chat.id, "Операция отменена.")
        return
    city, city_prep = city_result

    date_result = await _collect_date_and_name(message.chat.id, state, city)
    if not date_result:
        await send_safe(message.chat.id, "Операция отменена.")
        return
    event_date, time_display, event_name = date_result

    venue, address = await _collect_venue_info(message.chat.id, state)

    pricing_data = await _collect_pricing_config(message.chat.id, state, event_date)
    if pricing_data is None:
        await send_safe(message.chat.id, "Операция отменена.")
        return

    pricing_choice = (
        "formula" if pricing_data.get("pricing_type") == PricingType.FORMULA else "free"
    )

    event_data = {
        "name": event_name,
        "city": city,
        "city_prepositional": city_prep,
        "date": event_date,
        "date_display": _make_date_display(event_date),
        "time_display": time_display,
        "venue": venue,
        "address": address,
        "status": EventStatus.UPCOMING,
        "enabled": True,
        "free_for_types": [],
    }
    event_data.update(pricing_data)

    event_data["free_for_types"] = await _collect_free_for_types(message.chat.id, state)

    early_bird_data = await _collect_early_bird(message.chat.id, state, pricing_choice)
    event_data.update(early_bird_data)

    guest_data = await _collect_guest_settings(message.chat.id, state)
    event_data.update(guest_data)

    summary = _format_event_summary(event_data)
    confirm = await ask_user_confirmation(
        message.chat.id,
        f"Создать встречу?\n\n{summary}",
        state=state,
    )

    if not confirm:
        await send_safe(message.chat.id, "Операция отменена.")
        return

    event_id = await app.create_event(event_data)
    logger.info(
        f"Admin {message.from_user.id} created event: {event_name} (id={event_id})"
    )

    await app.save_event_log(
        event_type="admin_event_action",
        data={
            "action": "create_event",
            "event_id": event_id,
            "event_name": event_name,
        },
        user_id=message.from_user.id,
        username=message.from_user.username,
    )

    await send_safe(message.chat.id, f"✅ Встреча создана!\n\n{summary}")


# ---------------------------------------------------------------------------
# manage_events helpers
# ---------------------------------------------------------------------------


async def _handle_toggle_event(
    chat_id: int,
    state: FSMContext,
    app: App,
    event: dict,
    event_id: str,
    user_id: int,
    username: str,
) -> None:
    new_enabled = not event.get("enabled", False)
    await app.update_event(event_id, {"enabled": new_enabled})
    status_text = "включена" if new_enabled else "выключена"
    await send_safe(chat_id, f"Регистрация {status_text}.")
    await app.save_event_log(
        event_type="admin_event_action",
        data={
            "action": "toggle_registration",
            "event_id": event_id,
            "event_name": event.get("name"),
            "new_enabled": new_enabled,
        },
        user_id=user_id,
        username=username,
    )


async def _handle_archive_event(
    chat_id: int,
    state: FSMContext,
    app: App,
    event: dict,
    event_id: str,
    reg_count: int,
    user_id: int,
    username: str,
) -> bool:
    """Returns False if user cancelled, True otherwise."""
    if reg_count > 0:
        confirm = await ask_user_confirmation(
            chat_id,
            f"⚠️ У этой встречи {reg_count} регистраций. "
            f"После архивации они не будут видны пользователям. Продолжить?",
            state=state,
        )
        if not confirm:
            return False

    await app.update_event(
        event_id,
        {"status": EventStatus.ARCHIVED, "enabled": False},
    )
    await send_safe(chat_id, "Встреча архивирована.")
    await app.save_event_log(
        event_type="admin_event_action",
        data={
            "action": "archive_event",
            "event_id": event_id,
            "event_name": event.get("name"),
        },
        user_id=user_id,
        username=username,
    )
    return True


async def _handle_edit_field_name(
    chat_id: int,
    state: FSMContext,
    app: App,
    event: dict,
    event_id: str,
    user_id: int,
    username: str,
) -> None:
    resp = await ask_user_raw(
        chat_id,
        f"Текущее название: {event.get('name')}\nВведите новое:",
        state=state,
        timeout=None,
    )
    if resp and resp.text:
        old_name = event.get("name")
        await app.update_event(event_id, {"name": resp.text.strip()})
        await send_safe(chat_id, "✅ Название обновлено.")
        await app.save_event_log(
            event_type="admin_event_action",
            data={
                "action": "edit_event",
                "event_id": event_id,
                "field": "name",
                "old": old_name,
                "new": resp.text.strip(),
            },
            user_id=user_id,
            username=username,
        )


async def _handle_edit_field_date(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    resp = await ask_user_raw(
        chat_id,
        f"Текущая дата: {event.get('date_display')}\nВведите новую дату (ДД.ММ.ГГГГ):",
        state=state,
        timeout=None,
    )
    if resp and resp.text:
        try:
            new_date = datetime.strptime(resp.text.strip(), "%d.%m.%Y")
            old_date = event.get("date")
            if old_date:
                new_date = new_date.replace(hour=old_date.hour, minute=old_date.minute)
            await app.update_event(
                event_id,
                {
                    "date": new_date,
                    "date_display": _make_date_display(new_date),
                },
            )
            await send_safe(chat_id, "✅ Дата обновлена.")
        except ValueError:
            await send_safe(
                chat_id,
                "❌ Неверный формат. Используйте ДД.ММ.ГГГГ",
            )


async def _handle_edit_field_time(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    resp = await ask_user_raw(
        chat_id,
        f"Текущее время: {event.get('time_display')}\nВведите новое:",
        state=state,
        timeout=None,
    )
    if resp and resp.text:
        await app.update_event(event_id, {"time_display": resp.text.strip()})
        await send_safe(chat_id, "✅ Время обновлено.")


async def _handle_edit_field_venue(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    resp = await ask_user_raw(
        chat_id,
        f"Текущее место: {event.get('venue') or 'Не указано'}\nВведите новое:",
        state=state,
        timeout=None,
    )
    if resp and resp.text:
        await app.update_event(event_id, {"venue": resp.text.strip()})
        await send_safe(chat_id, "✅ Место обновлено.")


async def _handle_edit_field_address(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    resp = await ask_user_raw(
        chat_id,
        f"Текущий адрес: {event.get('address') or 'Не указано'}\nВведите новый:",
        state=state,
        timeout=None,
    )
    if resp and resp.text:
        await app.update_event(event_id, {"address": resp.text.strip()})
        await send_safe(chat_id, "✅ Адрес обновлен.")


async def _handle_edit_pricing_formula(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    current_base = event.get("price_formula_base", 0)
    current_rate = event.get("price_formula_rate", 0)
    current_ref = event.get("price_formula_reference_year", 2026)
    current_step = event.get("price_formula_step", 1)

    pricing_action = await ask_user_choice(
        chat_id,
        f"Текущие настройки формулы:\n"
        f"• База: {current_base}₽\n"
        f"• Надбавка: {current_rate}₽\n"
        f"• Год отсчёта: {current_ref}\n"
        f"• Шаг: {current_step}\n\n"
        f"Что изменить?",
        choices={
            "base": "Базовая стоимость",
            "rate": "Надбавка",
            "step": "Шаг группировки",
            "back": "Назад",
        },
        state=state,
        timeout=None,
    )

    if pricing_action == "base":
        resp = await ask_user_raw(
            chat_id,
            f"Текущая база: {current_base}₽\nВведите новую:",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_base = int(resp.text.strip())
                await app.update_event(event_id, {"price_formula_base": new_base})
                await send_safe(chat_id, f"✅ База: {new_base}₽.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")
    elif pricing_action == "rate":
        resp = await ask_user_raw(
            chat_id,
            f"Текущая надбавка: {current_rate}₽\nВведите новую:",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_rate = int(resp.text.strip())
                await app.update_event(event_id, {"price_formula_rate": new_rate})
                await send_safe(chat_id, f"✅ Надбавка: {new_rate}₽.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")
    elif pricing_action == "step":
        resp = await ask_user_raw(
            chat_id,
            f"Текущий шаг: {current_step}\nВведите новый (1 = каждый год, 3 = по 3 года):",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_step = max(1, int(resp.text.strip()))
                await app.update_event(event_id, {"price_formula_step": new_step})
                await send_safe(chat_id, f"✅ Шаг: {new_step}.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")


async def _handle_edit_field_pricing(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    pricing_type = event.get("pricing_type", "free")
    if pricing_type == PricingType.FORMULA:
        await _handle_edit_pricing_formula(chat_id, state, app, event, event_id)
    else:
        await send_safe(
            chat_id,
            "Редактирование оплаты доступно только для формульного типа.",
        )


async def _handle_edit_early_bird(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    current_discount = event.get("early_bird_discount", 0)
    current_deadline = event.get("early_bird_deadline")
    deadline_str = (
        current_deadline.strftime("%d.%m.%Y") if current_deadline else "не установлен"
    )

    eb_action = await ask_user_choice(
        chat_id,
        f"Текущие настройки ранней регистрации:\n"
        f"• Скидка: {current_discount}₽\n"
        f"• Дедлайн: {deadline_str}\n\n"
        f"Что изменить?",
        choices={
            "discount": "Изменить скидку",
            "deadline": "Изменить дедлайн",
            "back": "Назад",
        },
        state=state,
        timeout=None,
    )

    if eb_action == "discount":
        resp = await ask_user_raw(
            chat_id,
            f"Текущая скидка: {current_discount}₽\nВведите новую (0 = без скидки):",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_discount = max(0, int(resp.text.strip()))
                await app.update_event(event_id, {"early_bird_discount": new_discount})
                await send_safe(chat_id, f"✅ Скидка: {new_discount}₽.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")
    elif eb_action == "deadline":
        resp = await ask_user_raw(
            chat_id,
            f"Текущий дедлайн: {deadline_str}\nВведите новый (ДД.ММ.ГГГГ):",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_deadline = datetime.strptime(resp.text.strip(), "%d.%m.%Y")
                await app.update_event(event_id, {"early_bird_deadline": new_deadline})
                await send_safe(chat_id, "✅ Дедлайн обновлён.")
            except ValueError:
                await send_safe(chat_id, "❌ Неверный формат. Используйте ДД.ММ.ГГГГ")


async def _handle_edit_guests(
    chat_id: int, state: FSMContext, app: App, event: dict, event_id: str
) -> None:
    current_enabled = event.get("guests_enabled", False)
    current_max = event.get("max_guests_per_person", 3)
    current_min = event.get("guest_price_minimum", 0)

    guest_action = await ask_user_choice(
        chat_id,
        f"Текущие настройки гостей:\n"
        f"• Разрешены: {'Да' if current_enabled else 'Нет'}\n"
        f"• Макс. гостей: {current_max}\n"
        f"• Мин. цена: {current_min}₽\n\n"
        f"Что изменить?",
        choices={
            "toggle": f"{'Выключить' if current_enabled else 'Включить'} гостей",
            "max": "Изменить макс. количество",
            "min_price": "Изменить мин. цену",
            "back": "Назад",
        },
        state=state,
        timeout=None,
    )

    if guest_action == "toggle":
        new_enabled = not current_enabled
        await app.update_event(event_id, {"guests_enabled": new_enabled})
        await send_safe(
            chat_id,
            f"✅ Гости {'включены' if new_enabled else 'выключены'}.",
        )
    elif guest_action == "max":
        resp = await ask_user_raw(
            chat_id,
            f"Текущий максимум: {current_max}\nВведите новый:",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_max = max(1, int(resp.text.strip()))
                await app.update_event(event_id, {"max_guests_per_person": new_max})
                await send_safe(chat_id, f"✅ Максимум гостей: {new_max}.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")
    elif guest_action == "min_price":
        resp = await ask_user_raw(
            chat_id,
            f"Текущая мин. цена: {current_min}₽\nВведите новую (0 = как у регистранта):",
            state=state,
            timeout=None,
        )
        if resp and resp.text:
            try:
                new_min = max(0, int(resp.text.strip()))
                await app.update_event(event_id, {"guest_price_minimum": new_min})
                await send_safe(chat_id, f"✅ Мин. цена гостя: {new_min}₽.")
            except ValueError:
                await send_safe(chat_id, "❌ Введите число.")


async def _handle_edit_event(
    chat_id: int,
    state: FSMContext,
    app: App,
    event: dict,
    event_id: str,
    user_id: int,
    username: str,
) -> None:
    field = await ask_user_choice(
        chat_id,
        "Что изменить?",
        choices={
            "name": "Название",
            "date": "Дата",
            "time": "Время",
            "venue": "Место",
            "address": "Адрес",
            "pricing": "Настройки оплаты",
            "early_bird": "Ранняя регистрация",
            "guests": "Настройки гостей",
            "back": "Назад",
        },
        state=state,
        timeout=None,
    )

    if field == "back":
        return
    elif field == "name":
        await _handle_edit_field_name(
            chat_id, state, app, event, event_id, user_id, username
        )
    elif field == "date":
        await _handle_edit_field_date(chat_id, state, app, event, event_id)
    elif field == "time":
        await _handle_edit_field_time(chat_id, state, app, event, event_id)
    elif field == "venue":
        await _handle_edit_field_venue(chat_id, state, app, event, event_id)
    elif field == "address":
        await _handle_edit_field_address(chat_id, state, app, event, event_id)
    elif field == "pricing":
        await _handle_edit_field_pricing(chat_id, state, app, event, event_id)
    elif field == "early_bird":
        await _handle_edit_early_bird(chat_id, state, app, event, event_id)
    elif field == "guests":
        await _handle_edit_guests(chat_id, state, app, event, event_id)


# ---------------------------------------------------------------------------
# /manage_events
# ---------------------------------------------------------------------------


@commands_menu.add_command(
    "manage_events", "Управление встречами", visibility=Visibility.ADMIN_ONLY
)
@events_router.message(Command("manage_events"), AdminFilter())
async def manage_events_handler(message: Message, state: FSMContext, app: App):
    """Event management dashboard (admin only)."""
    if not message.from_user:
        return

    while True:
        all_events = await app.get_all_events()

        active_events = [
            e
            for e in all_events
            if e.get("status") in ("upcoming", "registration_closed")
        ]
        archived_events = [
            e for e in all_events if e.get("status") in ("archived", "passed")
        ]

        choices = {}
        if active_events:
            for ev in active_events:
                eid = str(ev["_id"])
                reg_count = await app.get_registration_count_for_event(eid)
                enabled_mark = "✅" if ev.get("enabled") else "⏸️"
                choices[eid] = (
                    f"{enabled_mark} {ev.get('city', '?')} "
                    f"({ev.get('date_display', '?')}) - {reg_count} рег."
                )

        choices["show_archive"] = f"📦 Архив ({len(archived_events)} встреч)"
        choices["done"] = "Готово"

        selection = await ask_user_choice(
            message.chat.id,
            "📋 Управление встречами:",
            choices=choices,
            state=state,
            timeout=None,
        )

        if selection == "done":
            await send_safe(message.chat.id, "Готово.")
            return

        if selection == "show_archive":
            if not archived_events:
                await send_safe(message.chat.id, "Архив пуст.")
                continue

            archive_choices = {}
            for ev in archived_events[:20]:
                eid = str(ev["_id"])
                from src.router import get_event_date_display

                archive_choices[eid] = (
                    f"{ev.get('city', '?')} ({get_event_date_display(ev)})"
                )
            archive_choices["back"] = "Назад"

            arch_selection = await ask_user_choice(
                message.chat.id,
                "📦 Архив встреч (выберите для разархивации):",
                choices=archive_choices,
                state=state,
                timeout=None,
            )
            if arch_selection != "back" and arch_selection:
                await app.update_event(
                    arch_selection,
                    {"status": EventStatus.PASSED, "enabled": False},
                )
                arch_event = await app.get_event_by_id(arch_selection)
                city = arch_event.get("city", "?") if arch_event else "?"
                await send_safe(
                    message.chat.id,
                    f"Встреча {city} разархивирована (статус: прошла).",
                )
            continue

        if not selection:
            continue
        event = await app.get_event_by_id(selection)
        if not event:
            await send_safe(message.chat.id, "❌ Встреча не найдена.")
            continue

        reg_count = await app.get_registration_count_for_event(selection)
        summary = _format_event_summary(event, reg_count)

        action = await ask_user_choice(
            message.chat.id,
            f"{summary}\n\nЧто сделать?",
            choices={
                "edit": "Редактировать",
                "toggle": "Вкл/Выкл регистрацию",
                "archive": "Архивировать",
                "back": "Назад",
            },
            state=state,
            timeout=None,
        )

        if action == "back":
            continue

        if action == "toggle":
            await _handle_toggle_event(
                message.chat.id,
                state,
                app,
                event,
                selection,
                message.from_user.id,
                message.from_user.username,
            )
            continue

        if action == "archive":
            await _handle_archive_event(
                message.chat.id,
                state,
                app,
                event,
                selection,
                reg_count,
                message.from_user.id,
                message.from_user.username,
            )
            continue

        if action == "edit":
            await _handle_edit_event(
                message.chat.id,
                state,
                app,
                event,
                selection,
                message.from_user.id,
                message.from_user.username,
            )
