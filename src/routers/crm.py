from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger
from typing import Dict, Any, Optional

from src.routers.admin import router
from src.app import App
from botspot import commands_menu
from botspot.components.qol.bot_commands_menu import Visibility
from src.user_interactions import ask_user_choice, ask_user_confirmation, ask_user_raw
from botspot.utils import send_safe
from botspot.utils.admin_filter import AdminFilter


def apply_message_templates(
    template: str, user_data: Dict[str, Any], event: Optional[Dict] = None
) -> str:
    """
    Apply template substitutions to a message based on user data and event.

    Args:
        template: The message template containing placeholders
        user_data: Dictionary with user information
        event: Optional event dict with venue/address/time/date info

    Returns:
        Personalized message with replaced placeholders
    """
    # Extract user data
    user_name = user_data.get("full_name", "")
    user_city_value = user_data.get("target_city", "")
    user_year = user_data.get("graduation_year", "")
    user_class = user_data.get("class_letter", "")

    # Get city-specific details from event
    if event:
        user_city_padezh = event.get("city_prepositional", user_city_value)
        user_address = event.get("address") or "Уточняется"
        user_venue = event.get("venue") or "Уточняется"
        user_time = event.get("time_display") or "Уточняется"
        user_date = event.get("date_display") or "Уточняется"
        if not user_city_value:
            user_city_value = event.get("city", "")
    else:
        user_city_padezh = "городе"
        user_address = "Уточняется"
        user_venue = "Уточняется"
        user_time = "Уточняется"
        user_date = "Уточняется"

    # Apply substitutions
    result = template
    result = result.replace("{name}", user_name)
    result = result.replace("{city}", user_city_value)
    result = result.replace("{city_padezh}", user_city_padezh)
    result = result.replace("{address}", user_address)
    result = result.replace("{venue}", user_venue)
    result = result.replace("{date}", user_date)
    result = result.replace("{time}", user_time)
    result = result.replace("{year}", str(user_year))
    result = result.replace("{class}", str(user_class))

    return result


_TEMPLATE_MARKERS = [
    "{name}",
    "{city}",
    "{city_padezh}",
    "{address}",
    "{venue}",
    "{time}",
    "{year}",
    "{class}",
]


async def _get_notify_users(app: App, audience: str, city_filter) -> tuple:
    """Fetch the target user list and a display name for the audience."""
    if audience == "unpaid":
        users = await app.get_unpaid_users(
            event_id=city_filter, active_only=city_filter is None
        )
        audience_name = "не оплативших пользователей"
    elif audience == "paid":
        users = await app.get_paid_users(
            event_id=city_filter, active_only=city_filter is None
        )
        audience_name = "оплативших пользователей"
    else:
        users = await app.get_all_users(
            event_id=city_filter, active_only=city_filter is None
        )
        audience_name = "всех пользователей"
    return users, audience_name


def _resolve_city_name(city: str, event_map: dict) -> str:
    if city == "all" or not city:
        return "всех городах"
    if city in event_map:
        return event_map[city].get(
            "city_prepositional", event_map[city].get("city", city)
        )
    return city


async def _build_notify_preview(
    app: App, users: list, audience_name: str, city_name: str, notification_text: str
) -> str:
    preview = f"📊 Найдено {len(users)} {audience_name} в {city_name}:\n\n"
    for i, user in enumerate(users[:10], 1):
        uname = user.get("username", "без имени")
        uid = user.get("user_id", "??")
        full_name = user.get("full_name", "Имя не указано")
        user_city = user.get("target_city", "Город не указан")
        preview += f"{i}. {full_name} (@{uname or uid})\n"
        preview += f"   🏙️ {user_city}\n"
    if len(users) > 10:
        preview += f"\n... и еще {len(users) - 10} пользователей"

    preview += "\n\n<b>Предварительный просмотр сообщения:</b>\n\n"
    preview += notification_text

    if users and any(m in notification_text for m in _TEMPLATE_MARKERS):
        example_user = users[0]
        example_event = await app.get_event_for_registration(example_user)
        personalized_example = apply_message_templates(
            notification_text, example_user, example_event
        )
        preview += (
            "\n\n<b>Пример персонализированного сообщения для пользователя:</b>\n"
        )
        preview += f"<i>{example_user.get('full_name', '')}</i>\n\n"
        preview += personalized_example

    return preview


def _build_validation_report(
    users: list, initiator, audience_name: str, city_name: str, notification_text: str
) -> str:
    report = "📢 <b>МАССОВАЯ РАССЫЛКА ЗАПУЩЕНА</b>\n\n"
    report += f"👤 Инициатор: {initiator}\n"
    report += f"🎯 Целевая аудитория: {len(users)} пользователей\n"
    report += f"🏙️ Город: {city_name}\n"
    report += f"💰 Категория: {audience_name}\n\n"
    report += "🗒️ <b>Список получателей:</b>\n"
    for i, user in enumerate(users[:20], 1):
        uname = user.get("username", "без имени")
        uid = user.get("user_id", "??")
        full_name = user.get("full_name", "Имя не указано")
        city = user.get("target_city", "Город не указан")
        report += f"{i}. {full_name} (@{uname or uid}) - {city}\n"
    if len(users) > 20:
        report += f"...и еще {len(users) - 20} пользователей\n"
    report += "\n📋 <b>Шаблон сообщения:</b>\n"
    report += notification_text
    return report


async def _send_notify_messages(app: App, users: list, notification_text: str) -> tuple:
    sent_count = 0
    failed_count = 0
    for user in users:
        user_id = user.get("user_id")
        if not user_id:
            failed_count += 1
            continue
        try:
            user_event = await app.get_event_for_registration(user)
            personalized_text = apply_message_templates(
                notification_text, user, user_event
            )
            await send_safe(user_id, personalized_text)
            sent_count += 1
            validation_message = (
                f"✅ Уведомление отправлено пользователю {user.get('full_name')} "
                f"(@{user.get('username') or user_id})\n🏙️ "
                f"{user.get('target_city', 'Город не указан')}"
            )
            await app.log_to_chat(validation_message, "events")
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
            failed_count += 1
    return sent_count, failed_count


@commands_menu.add_command(
    "notify", "Отправить уведомление пользователям", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("notify"), AdminFilter())
async def notify_users_handler(message: Message, state: FSMContext, app: App):
    """Notify users with a custom message using a step-by-step flow without state management"""
    if not message.from_user:
        await send_safe(message.chat.id, "❌ Ошибка: не удалось определить отправителя")
        return

    # Step 1: audience
    audience = await ask_user_choice(
        message.chat.id,
        "Шаг 1: Кому отправить уведомление?",
        choices={
            "unpaid": "Неоплатившим пользователям",
            "paid": "Оплатившим пользователям",
            "all": "Всем пользователям",
            "cancel": "Отмена",
        },
        state=state,
        timeout=None,
    )
    if audience == "cancel":
        await send_safe(message.chat.id, "Операция отменена")
        return

    # Step 2: city
    city_choices = {"all": "Все города", "cancel": "Отмена"}
    enabled_events = await app.get_enabled_events()
    event_map = {}
    for ev in enabled_events:
        event_id = str(ev["_id"])
        city_choices[event_id] = ev.get("city", "Unknown")
        event_map[event_id] = ev

    city = await ask_user_choice(
        message.chat.id,
        "Шаг 2: Выберите город для рассылки",
        choices=city_choices,
        state=state,
        timeout=None,
    )
    if city == "cancel":
        await send_safe(message.chat.id, "Операция отменена")
        return

    # Step 3: message text
    response = await ask_user_raw(
        message.chat.id,
        "Шаг 3: Введите текст сообщения для отправки\n\n"
        "Доступные шаблоны для подстановки:\n"
        "- {name} - имя пользователя\n"
        "- {city} - название города\n"
        "- {city_padezh} - название города в предложном падеже (в Москве, в Перми)\n"
        "- {address} - адрес встречи\n"
        "- {venue} - место проведения\n"
        "- {time} - время начала\n"
        "- {year} - год выпуска\n"
        "- {class} - буква класса\n\n"
        "Поддерживается HTML форматирование",
        state=state,
        timeout=None,
    )
    if not response or not response.html_text:
        await send_safe(message.chat.id, "Операция отменена")
        return

    notification_text = response.html_text
    if notification_text.lower() == "отмена":
        await send_safe(message.chat.id, "Операция отменена")
        return

    status_msg = await send_safe(
        message.chat.id, "⏳ Получение списка пользователей..."
    )

    city_filter = city if city != "all" else None
    users, audience_name = await _get_notify_users(app, audience, city_filter)

    if not users:
        await status_msg.edit_text(
            "❌ Пользователи, соответствующие критериям, не найдены!"
        )
        return

    city_name = _resolve_city_name(city, event_map)
    preview = await _build_notify_preview(
        app, users, audience_name, city_name, notification_text
    )
    await status_msg.edit_text(preview)

    # Step 4: confirm
    confirm = await ask_user_confirmation(
        message.chat.id,
        f"Шаг 4: ⚠️ Вы собираетесь отправить сообщение {len(users)} пользователям. Продолжить?",
        state=state,
    )
    if not confirm:
        await send_safe(message.chat.id, "Операция отменена")
        return

    initiator = message.from_user.username or message.from_user.id
    validation_report = _build_validation_report(
        users, initiator, audience_name, city_name, notification_text
    )
    await app.log_to_chat(validation_report, "events")

    status_msg = await send_safe(message.chat.id, "⏳ Отправка уведомлений...")
    sent_count, failed_count = await _send_notify_messages(
        app, users, notification_text
    )

    await status_msg.edit_text(
        f"✅ Уведомления отправлены!\n\n"
        f"📊 Статистика:\n"
        f"- Успешно отправлено: {sent_count}\n"
        f"- Ошибок: {failed_count}"
    )


async def _build_recipient_list(
    app, audience: str, city_filter: str, event_map: dict
) -> list:
    """Resolve the list of target user IDs for announce_new_season_handler."""
    if audience == "all_time":
        if city_filter == "all":
            active_ids = set(await app.collection.distinct("user_id"))
            deleted_ids = set(await app.deleted_users.distinct("user_id"))
            return list(active_ids | deleted_ids)
        ev = event_map.get(city_filter, {})
        city = ev.get("city", "")
        active_ids = set(
            await app.collection.distinct("user_id", {"target_city": city})
        )
        deleted_ids = set(
            await app.deleted_users.distinct("user_id", {"target_city": city})
        )
        return list(active_ids | deleted_ids)
    # current
    if city_filter == "all":
        return await app.collection.distinct(
            "user_id", {"event_id": {"$in": list(event_map)}}
        )
    return await app.collection.distinct("user_id", {"event_id": city_filter})


def _build_default_announcement(enabled_events: list, post_link: str) -> str:
    cities = [ev.get("city", "?") for ev in enabled_events]
    cities_str = ", ".join(cities)
    events_list = ""
    for ev in enabled_events:
        venue = ev.get("venue") or "Уточняется"
        address = ev.get("address") or ""
        venue_line = venue + (f", {address}" if address else "")
        from src.router import get_event_date_display

        events_list += (
            f"🏙️ {ev.get('city', '?')} ({get_event_date_display(ev)})\n"
            f"   📍 {venue_line}\n"
        )
    msg = f"Встречи 146 — {cities_str} — приходи!\n\n{events_list}\n"
    if post_link:
        msg += f"Регистрация открыта — детали тут:\n{post_link}\n\n"
    msg += "Чтобы зарегистрироваться, напиши боту /start"
    return msg


async def _get_announcement_text(message, state, default_message: str) -> Optional[str]:
    """Ask user to keep default message or enter a custom one. Returns text or None on cancel."""
    custom_resp = await ask_user_choice(
        message.chat.id,
        f"Шаг 4: Текст сообщения:\n\n{default_message}\n\nИспользовать этот текст или написать свой?",
        choices={
            "use_default": "Использовать этот текст",
            "custom": "Написать свой текст",
            "cancel": "Отмена",
        },
        state=state,
        timeout=None,
    )
    if custom_resp == "cancel":
        await send_safe(message.chat.id, "Операция отменена.")
        return None
    if custom_resp == "custom":
        text_resp = await ask_user_raw(
            message.chat.id,
            "Введите текст сообщения (поддерживается HTML):",
            state=state,
            timeout=None,
        )
        if not text_resp or not text_resp.html_text:
            await send_safe(message.chat.id, "Операция отменена.")
            return None
        return text_resp.html_text
    return default_message


async def _send_announcements(users_ids: list, announcement_text: str) -> tuple:
    sent_count = 0
    failed_count = 0
    for uid in users_ids:
        try:
            await send_safe(uid, announcement_text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send season announcement to user {uid}: {e}")
            failed_count += 1
    return sent_count, failed_count


async def announce_new_season_handler(message: Message, state: FSMContext, app: App):
    """Announce new meetup season to users with audience and city controls."""
    if not message.from_user:
        await send_safe(message.chat.id, "❌ Ошибка: не удалось определить отправителя")
        return

    enabled_events = await app.get_enabled_events()
    if not enabled_events:
        await send_safe(
            message.chat.id,
            "⚠️ Нет активных встреч. Сначала создайте встречи через /create_event",
        )
        return

    events_preview = "📅 Текущие активные встречи:\n"
    for ev in enabled_events:
        from src.router import get_event_date_display

        events_preview += f"  • {ev.get('city', '?')} ({get_event_date_display(ev)})\n"
    await send_safe(message.chat.id, events_preview)

    # Step 1: audience scope
    audience = await ask_user_choice(
        message.chat.id,
        "Шаг 1: Кому отправить анонс?",
        choices={
            "all_time": "Все пользователи за всё время",
            "current": "Зарегистрированные на текущие встречи",
            "cancel": "Отмена",
        },
        state=state,
        timeout=None,
    )
    if audience == "cancel":
        await send_safe(message.chat.id, "Операция отменена.")
        return

    # Step 2: city/event filter
    city_choices: Dict[str, Any] = {"all": "Все города / встречи", "cancel": "Отмена"}
    event_map = {}
    for ev in enabled_events:
        event_id = str(ev["_id"])
        city_choices[event_id] = ev.get("name", ev.get("city", event_id))
        event_map[event_id] = ev

    city_filter = await ask_user_choice(
        message.chat.id,
        "Шаг 2: Выберите город / встречу для рассылки",
        choices=city_choices,
        state=state,
        timeout=None,
    )
    if city_filter == "cancel":
        await send_safe(message.chat.id, "Операция отменена.")
        return

    target_user_ids = await _build_recipient_list(app, audience, city_filter, event_map)
    if not target_user_ids:
        await send_safe(message.chat.id, "❌ Нет пользователей для рассылки.")
        return

    # Step 3: link
    link_resp = await ask_user_raw(
        message.chat.id,
        "Шаг 3: Введите ссылку на пост (или 'нет' чтобы пропустить):",
        state=state,
        timeout=None,
    )
    post_link = ""
    if link_resp and link_resp.text and link_resp.text.strip().lower() != "нет":
        post_link = link_resp.text.strip()

    default_message = _build_default_announcement(enabled_events, post_link)

    # Step 4: text
    announcement_text = await _get_announcement_text(message, state, default_message)
    if announcement_text is None:
        return

    audience_desc = (
        "все пользователи за всё время"
        if audience == "all_time"
        else "текущие зарегистрированные"
    )
    if city_filter != "all" and city_filter in event_map:
        audience_desc += f" ({event_map[city_filter].get('city', city_filter)})"

    confirm = await ask_user_confirmation(
        message.chat.id,
        f"⚠️ Отправить анонс {len(target_user_ids)} пользователям?\n"
        f"Аудитория: {audience_desc}\n\n"
        f"Текст:\n{announcement_text}",
        state=state,
    )
    if not confirm:
        await send_safe(message.chat.id, "Операция отменена.")
        return

    await app.log_to_chat(
        f"📢 <b>АНОНС НОВОГО СЕЗОНА</b>\n\n"
        f"👤 Инициатор: {message.from_user.username or message.from_user.id}\n"
        f"🎯 Получателей: {len(target_user_ids)}\n"
        f"📋 Аудитория: {audience_desc}\n\n"
        f"📝 Текст:\n{announcement_text}",
        "events",
    )

    status_msg = await send_safe(message.chat.id, "⏳ Отправка анонса...")
    sent_count, failed_count = await _send_announcements(
        target_user_ids, announcement_text
    )

    await status_msg.edit_text(
        f"✅ Анонс отправлен!\n\n"
        f"📊 Статистика:\n"
        f"- Успешно: {sent_count}\n"
        f"- Ошибок: {failed_count}"
    )


@commands_menu.add_command(
    "test_user_selection",
    "Тест выборки пользователей",
    visibility=Visibility.ADMIN_ONLY,
)
@router.message(Command("test_user_selection"), AdminFilter())
async def test_user_selection_handler(message: Message, state: FSMContext, app: App):
    """Test the user selection methods by reporting counts for each city and payment status"""

    # Show processing message
    status_msg = await send_safe(
        message.chat.id, "⏳ Тестирование выборки пользователей..."
    )

    # Initialize report
    report = "📊 <b>Результаты тестирования выборки пользователей:</b>\n\n"
    report += "<i>Примечание: бесплатные мероприятия и учителя автоматически помечаются как оплатившие.</i>\n\n"

    # Get counts for all events combined
    all_users = await app.get_all_users(active_only=True)
    all_paid = await app.get_paid_users(active_only=True)
    all_unpaid = await app.get_unpaid_users(active_only=True)

    report += "<b>Все города:</b>\n"
    report += f"- Всего пользователей: {len(all_users)}\n"
    report += f"- Оплатившие: {len(all_paid)}\n"
    report += f"- Неоплатившие: {len(all_unpaid)}\n\n"

    # Get counts per active event
    active_events = await app.get_active_events()
    for ev in active_events:
        event_id = str(ev["_id"])
        city_display = ev.get("name", ev.get("city", "Unknown"))

        ev_all = await app.get_all_users(event_id=event_id)
        ev_paid = await app.get_paid_users(event_id=event_id)
        ev_unpaid = await app.get_unpaid_users(event_id=event_id)

        report += f"<b>{city_display}:</b>\n"
        report += f"- Всего пользователей: {len(ev_all)}\n"
        report += f"- Оплатившие: {len(ev_paid)}\n"
        report += f"- Неоплатившие: {len(ev_unpaid)}\n\n"

    # Update status message with report
    await status_msg.edit_text(report, parse_mode="HTML")


@commands_menu.add_command(
    "notify_early_payment",
    "Уведомить о раннем платеже",
    visibility=Visibility.ADMIN_ONLY,
)
@router.message(Command("notify_early_payment"), AdminFilter())
async def notify_early_payment_handler(message: Message, state: FSMContext, app: App):
    """Notify users who haven't paid yet about the early payment deadline"""

    # Ask user for action choice
    response = await ask_user_choice(
        message.chat.id,
        "Что вы хотите сделать?",
        choices={
            "notify": "Отправить уведомления о раннем платеже",
            "dry_run": "Тестовый режим (показать список, но не отправлять)",
            "cancel": "Отмена",
        },
        state=state,
        timeout=None,
    )

    if response == "cancel":
        await send_safe(message.chat.id, "Операция отменена")
        return

    # Show processing message
    status_msg = await send_safe(
        message.chat.id, "⏳ Получение списка не оплативших..."
    )

    # Get list of users who haven't paid (across all active events)
    unpaid_users = await app.get_unpaid_users(active_only=True)

    # Check if we have unpaid users
    if not unpaid_users:
        await status_msg.edit_text("✅ Все пользователи оплатили!")
        return

    # Generate report for both dry run and actual notification
    report = f"📊 Найдено {len(unpaid_users)} пользователей без оплаты:\n\n"

    for i, user in enumerate(unpaid_users, 1):
        username = user.get("username", "без имени")
        user_id = user.get("user_id", "??")
        full_name = user.get("full_name", "Имя не указано")
        city = user.get("target_city", "Город не указан")
        payment_status = user.get("payment_status", "Не оплачено")

        # Format payment status
        if payment_status == "pending":
            payment_status = "Оплачу позже"
        elif payment_status == "declined":
            payment_status = "Отклонено"
        else:
            payment_status = "Не оплачено"

        report += f"{i}. {full_name} (@{username or user_id})\n"
        report += f"   🏙️ {city}, 💰 {payment_status}\n\n"

    # Update status message with report
    await status_msg.edit_text(report)

    # For dry run, we're done
    if response == "dry_run":
        await send_safe(
            message.chat.id, "🔍 Тестовый режим завершен. Уведомления не отправлялись."
        )
        return

    # For actual notification, ask for confirmation
    confirm = await ask_user_confirmation(
        message.chat.id,
        f"⚠️ Вы собираетесь отправить уведомление {len(unpaid_users)} пользователям о раннем платеже. Продолжить?",
        state=state,
    )

    if not confirm:
        await send_safe(message.chat.id, "Операция отменена")
        return

    # First send a detailed report to the validation chat
    validation_report = "📢 <b>МАССОВАЯ РАССЫЛКА ЗАПУЩЕНА</b>\n\n"
    if message.from_user:
        validation_report += (
            f"👤 Инициатор: {message.from_user.username or message.from_user.id}\n"
        )
    else:
        validation_report += "👤 Инициатор: Неизвестно\n"
    validation_report += (
        f"🎯 Целевая аудитория: {len(unpaid_users)} пользователей без оплаты\n\n"
    )
    validation_report += "🗒️ <b>Список получателей:</b>\n"

    # Add a list of users (limited to avoid oversized message)
    for i, user in enumerate(unpaid_users[:20], 1):
        username = user.get("username", "без имени")
        user_id = user.get("user_id", "??")
        full_name = user.get("full_name", "Имя не указано")
        city = user.get("target_city", "Город не указан")
        validation_report += f"{i}. {full_name} (@{username or user_id}) - {city}\n"

    if len(unpaid_users) > 20:
        validation_report += f"...и еще {len(unpaid_users) - 20} пользователей\n"

    # Add template text to the report
    validation_report += "\n📋 <b>Шаблон сообщения:</b>\n"
    template_text = (
        "🔔 <b>Напоминание о раннем платеже</b>\n\n"
        "Привет, {name}! Напоминаем, что до окончания периода ранней оплаты "
        "осталось совсем немного времени (до 15 марта 2025).\n\n"
        "Оплатив сейчас, ты получаешь скидку для участия в {city_padezh}:\n"
        "- Москва: 1000 руб.\n"
        "- Пермь: 500 руб.\n\n"
        "Место проведения: {venue}\n"
        "Адрес: {address}\n"
        "Время начала: {time}\n\n"
        "Чтобы оплатить, используй команду /pay"
    )
    validation_report += template_text

    # Send report to validation chat before starting the actual notifications
    await app.log_to_chat(validation_report, "events")

    # Then use the same template for the actual notifications
    notification_text = template_text

    sent_count = 0
    failed_count = 0

    status_msg = await send_safe(message.chat.id, "⏳ Отправка уведомлений...")

    for user in unpaid_users:
        user_id = user.get("user_id")
        if not user_id:
            failed_count += 1
            continue

        try:
            # Look up event for this user's registration
            user_event = await app.get_event_for_registration(user)
            # Process templates for this user using our utility function
            personalized_text = apply_message_templates(
                notification_text, user, user_event
            )

            await send_safe(user_id, personalized_text)
            sent_count += 1

            # Notify validation chat about sent message
            validation_message = (
                f"✅ Уведомление отправлено пользователю {user.get('full_name')} "
                f"(@{user.get('username') or user_id})\n🏙️ "
                f"{user.get('target_city', 'Город не указан')}"
            )
            await app.log_to_chat(validation_message, "events")
        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")
            failed_count += 1

    # Update status message with results
    result_text = (
        f"✅ Уведомления отправлены!\n\n"
        f"📊 Статистика:\n"
        f"- Успешно отправлено: {sent_count}\n"
        f"- Ошибок: {failed_count}"
    )

    await status_msg.edit_text(result_text)


# @commands_menu.add_command(
#     "send_feedback_request", "Отправить запрос на обратную связь", visibility=Visibility.ADMIN_ONLY
# )
# @router.message(Command("send_feedback_request"), AdminFilter())
# async def send_feedback_request_handler(message: Message, state: FSMContext):
#     """Send feedback request messages to users"""
#     if not message.from_user:
#         await send_safe(message.chat.id, "❌ Ошибка: не удалось определить отправителя")
#         return

#     # Step 1: Select city
#     city = await ask_user_choice(
#         message.chat.id,
#         "Шаг 1: Выберите город, по которому хотите запросить обратную связь:",
#         choices={
#             "MOSCOW": "Москва",
#             "PERM": "Пермь",
#             "SAINT_PETERSBURG": "Санкт-Петербург",
#             "BELGRADE": "Белград",
#             "all": "Все города",
#             "cancel": "Отмена",
#         },
#         state=state,
#         timeout=None,
#     )

#     if city == "cancel":
#         await send_safe(message.chat.id, "Операция отменена")
#         return

#     # Step 2: Ask if this is a test or production run
#     run_type = await ask_user_choice(
#         message.chat.id,
#         "Шаг 2: Это тестовый запуск или боевой?",
#         choices={
#             "test": "Тестовый (отправить только себе)",
#             "prod": "Боевой (отправить всем пользователям)",
#             "cancel": "Отмена",
#         },
#         state=state,
#         timeout=None,
#     )

#     if run_type == "cancel":
#         await send_safe(message.chat.id, "Операция отменена")
#         return

#     # Show processing message
#     status_msg = await send_safe(message.chat.id, "⏳ Получение списка пользователей...")

#     # Get city-specific details and dates for messages
#     city_display_name = {
#         "MOSCOW": "Москве",
#         "PERM": "Перми",
#         "SAINT_PETERSBURG": "Санкт-Петербурге",
#         "BELGRADE": "Белграде",
#         "all": "разных городах",
#     }.get(city or "", city or "")

#     target_users = []

#     if run_type == "test":
#         # Just the admin for test run
#         admin_data = {
#             "user_id": message.from_user.id,
#             "username": message.from_user.username,
#             "full_name": message.from_user.full_name,
#             "target_city": city if city != "all" else "MOSCOW",  # Default to Moscow for test
#         }
#         target_users = [admin_data]
#     else:
#         # Real users for production run
#         target_users = await src.get_users_without_feedback(city if city != "all" else None)

#     # Check if we have users matching criteria
#     if not target_users:
#         await status_msg.edit_text("❌ Пользователи, соответствующие критериям, не найдены!")
#         return

#     # Generate preview report
#     preview = f"📊 Найдено {len(target_users)} пользователей для отправки запроса обратной связи по {city_display_name}:\n\n"

#     # Show a preview of up to 10 users
#     for i, user in enumerate(target_users[:10], 1):
#         username = user.get("username", "без имени")
#         user_id = user.get("user_id", "??")
#         full_name = user.get("full_name", "Имя не указано")
#         user_city = user.get("target_city", "Город не указан")

#         preview += f"{i}. {full_name} (@{username or user_id})\n"
#         preview += f"   🏙️ {user_city}\n"

#     if len(target_users) > 10:
#         preview += f"\n... и еще {len(target_users) - 10} пользователей"

#     # Update status message with preview
#     await status_msg.edit_text(preview)

#     # Step 3: Ask for confirmation
#     confirm = await ask_user_confirmation(
#         message.chat.id,
#         f"Шаг 3: ⚠️ Вы собираетесь отправить запрос обратной связи {len(target_users)} пользователям. Продолжить?",
#         state=state,
#     )

#     if not confirm:
#         await send_safe(message.chat.id, "Операция отменена")
#         return

#     # First send a detailed report to the validation chat
#     validation_report = f"📢 <b>МАССОВАЯ РАССЫЛКА ЗАПРОСОВ ОБРАТНОЙ СВЯЗИ ЗАПУЩЕНА</b>\n\n"
#     validation_report += f"👤 Инициатор: {message.from_user.username or message.from_user.id}\n"
#     validation_report += f"🎯 Целевая аудитория: {len(target_users)} пользователей в {city_display_name}\n"
#     validation_report += f"🚀 Режим запуска: {'Тестовый' if run_type == 'test' else 'Боевой'}\n\n"
#     validation_report += f"🗒️ <b>Список получателей:</b>\n"

#     # Add a list of users (limited to avoid oversized message)
#     for i, user in enumerate(target_users[:20], 1):
#         username = user.get("username", "без имени")
#         user_id = user.get("user_id", "??")
#         full_name = user.get("full_name", "Имя не указано")
#         user_city = user.get("target_city", "Город не указан")
#         validation_report += f"{i}. {full_name} (@{username or user_id}) - {user_city}\n"

#     if len(target_users) > 20:
#         validation_report += f"...и еще {len(target_users) - 20} пользователей\n"

#     # Send report to validation chat before starting the actual messages
#     await src.log_to_chat(validation_report, "events")

#     # Start sending the messages
#     sent_count = 0
#     failed_count = 0

#     status_msg = await send_safe(message.chat.id, "⏳ Отправка запросов обратной связи...")

#     from botspot.core.dependency_manager import get_dependency_manager
#     deps = get_dependency_manager()
#     bot = deps.bot

#     for user in target_users:
#         user_id = user.get("user_id")
#         if not user_id:
#             failed_count += 1
#             continue

#         try:
#             # Get city-specific information
#             user_city = user.get("target_city")
#             user_city_enum = None
#             for city_enum_value in TargetCity:
#                 if city_enum_value.value == user_city:
#                     user_city_enum = city_enum_value
#                     break

#             city_name = user_city if user_city else "вашем городе"
#             city_date = date_of_event.get(user_city_enum, "недавно") if user_city_enum else "недавно"
#             day_of_week = ""

#             if "Марта" in city_date:
#                 day_of_week = "субботу"
#             elif "Апреля" in city_date:
#                 day_of_week = "субботу"

#             # Personalize the initial message (from Petr Lavrov)
#             initial_message = (
#                 f"Привет! Как тебе встреча выпускников в {city_name}? Было классно что получилось добраться. "
#                 f"У меня к сожалению не получилось приехать, но очень радостно на сердце что такие встречи реальны."
#             )

#             # Send the initial message as if from Petr
#             await send_safe(user_id, initial_message, parse_mode="HTML")

#             # Wait 30 seconds to simulate natural delay
#             await asyncio.sleep(30)

#             # Send photo link message
#             photo_links_message = (
#                 "Вот кстати ссылки на альбомы встреч в каждой локации:\n"
#                 "Пермь: ХХХ\n"
#                 "Москва: ХХХ\n"
#                 "Питер: ХХХ"
#             )

#             await send_safe(user_id, photo_links_message)

#             # Wait 2 minutes (120 seconds)
#             await asyncio.sleep(120)

#             # Send the request for feedback message
#             feedback_request = (
#                 "Как думаешь, удобно ли бы тебе было нам дать обратную связь по тому как прошло, "
#                 "чтобы мы в следующий раз еще лучше сделали? Я тебе сейчас через чат-бот сделаю запрос, если удобно - ответь пожалуйста."
#             )

#             await send_safe(user_id, feedback_request)

#             # Wait 3 minutes (180 seconds) before the bot sends its message
#             await asyncio.sleep(180)

#             # Final feedback bot message with correct city and date
#             feedback_bot_message = (
#                 f"Я чат-бот, собираю обратную связь по встрече в {city_name} в {day_of_week}, {city_date}. "
#                 f"Благодаря в том числе и твоей обратной связи мы продолжаем улучшать наши мероприятия, "
#                 f"помоги нам пожалуйста, потрать 4 минуты.\n\n"
#                 f"Пожалуйста, используй команду /feedback чтобы оставить обратную связь."
#             )

#             await send_safe(user_id, feedback_bot_message)

#             sent_count += 1

#             # Notify validation chat about sent message sequence
#             validation_message = f"✅ Запрос обратной связи отправлен пользователю {user.get('full_name')} (@{user.get('username') or user_id})\n🏙️ {user.get('target_city', 'Город не указан')}"
#             await src.log_to_chat(validation_message, "events")

#         except Exception as e:
#             logger.error(f"Failed to send feedback request to user {user_id}: {e}")
#             failed_count += 1

#             # Log error to validation chat
#             error_message = f"❌ Ошибка отправки запроса обратной связи пользователю {user.get('full_name')} (@{user.get('username') or user_id}): {str(e)}"
#             await src.log_to_chat(error_message, "errors")

#     # Update status message with results
#     result_text = (
#         f"✅ Запросы обратной связи отправлены!\n\n"
#         f"📊 Статистика:\n"
#         f"- Успешно отправлено: {sent_count}\n"
#         f"- Ошибок: {failed_count}"
#     )

#     await status_msg.edit_text(result_text)
