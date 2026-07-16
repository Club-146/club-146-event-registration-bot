"""Admin event management router: /create_event and /manage_events commands."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from loguru import logger

from src.app import App, EventStatus, PricingType
from botspot import commands_menu
from botspot.components.qol.bot_commands_menu import Visibility
from src.user_interactions import ask_user_choice, ask_user_confirmation
from botspot.utils import send_safe
from botspot.utils.admin_filter import AdminFilter

from src.routers._events_helpers import (  # noqa: F401 — re-exported for external use
    _build_event_choices,
    _collect_city,
    _collect_date_and_name,
    _collect_early_bird,
    _collect_event_image,
    _collect_free_for_types,
    _collect_guest_settings,
    _collect_pricing_config,
    _collect_venue_info,
    _format_event_summary,
    _handle_archive_selection,
    _handle_event_action,
    _make_date_display,
)

events_router = Router()


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
    event_image = await _collect_event_image(message.chat.id, state)

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
    # Preserve the explicit "Без изображения" choice as None. Omitting the key
    # would activate a bundled default for a matching legacy event.
    event_data["image"] = event_image
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
            if e.get("status") in ("upcoming", "registration_closed", "passed")
        ]
        archived_events = [e for e in all_events if e.get("status") == "archived"]

        choices = await _build_event_choices(app, active_events, archived_events)

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
            await _handle_archive_selection(
                message.chat.id, state, app, archived_events
            )
            continue

        if not selection:
            continue

        await _handle_event_action(
            message.chat.id,
            state,
            app,
            selection,
            message.from_user.id,
            message.from_user.username,
        )
