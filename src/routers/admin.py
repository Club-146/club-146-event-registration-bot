import base64
import json
from typing import Mapping, Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
)
from litellm import acompletion
from loguru import logger
from pydantic import BaseModel
from src.app import App
from src.ticket_cards import send_paid_ticket_card
from botspot import commands_menu
from botspot.components.qol.bot_commands_menu import Visibility
from src.user_interactions import ask_user_choice, ask_user_raw
from botspot.utils import send_safe
from botspot.utils.admin_filter import AdminFilter


# Define Pydantic model for payment information
class PaymentInfo(BaseModel):
    amount: Optional[int]
    is_valid: bool  # Whether there's a clear payment amount in the document


router = Router()


# Helper function for calculating median


async def admin_handler(message: Message, state: FSMContext, app: App):
    from src.routers.stats import (
        show_stats,
        show_simple_stats,
        show_year_stats,
        show_five_year_stats,
        show_payment_stats,
    )

    response = await ask_user_choice(
        message.chat.id,
        "Вы администратор бота. Что вы хотите сделать?",
        choices={
            "register": "Протестировать бота (обычный сценарий)",
            "management": "Управление",
            "communication": "Коммуникации",
            "stats": "Статистика и аналитика",
        },
        state=state,
        timeout=None,
    )

    # -- Management submenu --
    if response == "management":
        response = await ask_user_choice(
            message.chat.id,
            "Управление:",
            choices={
                "manage_events": "Управление встречами",
                "register_payment": "Зарегистрировать оплату (за другого участника)",
                "export": "Экспортировать данные",
            },
            state=state,
            timeout=None,
        )

    # -- Communication submenu --
    if response == "communication":
        response = await ask_user_choice(
            message.chat.id,
            "Коммуникации:",
            choices={
                "notify_users": "Рассылка пользователям",
                "announce_season": "Анонс нового сезона встреч",
            },
            state=state,
            timeout=None,
        )

    # -- Stats submenu --
    if response == "stats":
        response = await ask_user_choice(
            message.chat.id,
            "Статистика и аналитика:",
            choices={
                "view_stats": "Статистика (подробно)",
                "view_simple_stats": "Статистика (кратко)",
                "view_year_stats": "По годам выпуска",
                "five_year_stats": "По пятилеткам выпуска",
                "payment_stats": "Диаграмма оплат",
            },
            state=state,
            timeout=None,
        )

    # -- Dispatch --
    if response == "manage_events":
        from src.routers.events import manage_events_handler

        await manage_events_handler(message, state, app=app)
    elif response == "register_payment":
        await admin_register_payment(message, state, app)
    elif response == "export":
        await export_handler(message, state, app=app)
    elif response == "notify_users":
        from src.routers.crm import notify_users_handler

        await notify_users_handler(message, state, app=app)
    elif response == "announce_season":
        from src.routers.crm import announce_new_season_handler

        await announce_new_season_handler(message, state, app=app)
    elif response == "view_stats":
        await show_stats(message, app=app)
    elif response == "view_simple_stats":
        await show_simple_stats(message, app=app)
    elif response == "view_year_stats":
        await show_year_stats(message, app=app)
    elif response == "five_year_stats":
        await show_five_year_stats(message, app=app)
    elif response == "payment_stats":
        await show_payment_stats(message, app=app)
    # For "register", continue with normal flow
    return response


async def _select_event_for_payment(
    chat_id: int, state: FSMContext, app: App
) -> Optional[str]:
    """Select a non-archived event. Returns event_id or None if cancelled/empty."""
    from src.router import get_event_date_display

    all_events = await app.get_all_events()
    non_archived = [e for e in all_events if e.get("status") != "archived"]

    if not non_archived:
        await send_safe(chat_id, "Нет доступных встреч.")
        return None

    event_choices = {}
    for ev in non_archived:
        eid = str(ev["_id"])
        event_choices[eid] = f"{ev.get('city', '?')} ({get_event_date_display(ev)})"
    event_choices["cancel"] = "Отмена"

    selected = await ask_user_choice(
        chat_id,
        "Выберите встречу:",
        choices=event_choices,
        state=state,
        timeout=None,
    )
    if selected == "cancel":
        await send_safe(chat_id, "Отменено.")
        return None
    return selected


async def _select_unpaid_user(
    chat_id: int, state: FSMContext, app: App, event_id: str
) -> Optional[tuple]:
    """Pick an unpaid user from list or manual input.

    Returns (reg_doc, user_id, full_name) or None if cancelled/not found.
    """
    unpaid_users = await app.get_unpaid_users(event_id=event_id)

    user_choices = {}
    for u in unpaid_users:
        uid = str(u["user_id"]) if u.get("user_id") else f"reg_{u['_id']}"
        uname = f"@{u['username']}" if u.get("username") else "без username"
        status = u.get("payment_status", "не оплачено")
        user_choices[uid] = f"{uname} — {u.get('full_name', '?')} ({status})"
    user_choices["manual"] = "Ввести username вручную"
    user_choices["cancel"] = "Отмена"

    header = (
        f"Неоплаченные участники ({len(unpaid_users)}):"
        if unpaid_users
        else "Все оплатили! Но можно добавить вручную:"
    )
    chosen_user = await ask_user_choice(
        chat_id, header, choices=user_choices, state=state, timeout=None
    )
    if chosen_user == "cancel":
        await send_safe(chat_id, "Отменено.")
        return None

    if chosen_user == "manual":
        return await _resolve_manual_user(chat_id, state, app, event_id)

    # Find from unpaid list
    reg = next((u for u in unpaid_users if str(u.get("user_id")) == chosen_user), None)
    if not reg:
        await send_safe(chat_id, "Ошибка: пользователь не найден.")
        return None
    return reg, reg.get("user_id"), reg.get("full_name", "?")


async def _resolve_manual_user(
    chat_id: int, state: FSMContext, app: App, event_id: str
) -> Optional[tuple]:
    """Resolve a manually entered username to a registration."""
    username_input = await ask_user_raw(
        chat_id,
        "Введите Telegram username (с @ или без):",
        state=state,
        timeout=300,
    )
    if not username_input:
        await send_safe(chat_id, "Время ожидания истекло.")
        return None
    username_clean = str(username_input).lstrip("@").strip()
    reg = await app.collection.find_one(
        {"username": username_clean, "event_id": event_id}
    )
    if not reg:
        await send_safe(
            chat_id,
            f"Пользователь @{username_clean} не найден среди зарегистрированных на эту встречу.",
        )
        return None
    return reg, reg.get("user_id"), reg.get("full_name", "?")


async def _confirm_payment_amount(
    chat_id: int, state: FSMContext, target_name: str
) -> Optional[int]:
    """Ask admin for payment amount. Returns int amount or None on failure."""
    amount_input = await ask_user_raw(
        chat_id,
        f"Подтверждаем оплату для {target_name}.\nВведите сумму в рублях:",
        state=state,
        timeout=300,
    )
    if not amount_input:
        await send_safe(chat_id, "Время ожидания истекло.")
        return None

    try:
        return int(str(amount_input).strip())
    except ValueError:
        await send_safe(chat_id, "Неверный формат суммы.")
        return None


async def _send_admin_confirmed_ticket(
    app: App, target_user_id: int, event_id: str
) -> None:
    """Re-read authoritative state and attempt ticket delivery without blocking payment."""

    try:
        registration = await app.collection.find_one(
            {"user_id": target_user_id, "event_id": event_id}
        )
        if not isinstance(registration, Mapping):
            logger.warning(
                f"Could not re-read registration for ticket delivery: "
                f"user={target_user_id}, event={event_id}"
            )
            return
        event = await app.get_event_for_registration(registration)
        await send_paid_ticket_card(target_user_id, registration, event)
    except Exception as e:
        logger.warning(
            f"Could not deliver confirmed ticket for user {target_user_id}: {e}"
        )


async def admin_register_payment(message: Message, state: FSMContext, app: App):
    """Admin flow: select event → pick unpaid user → confirm payment."""
    chat_id = message.chat.id

    selected = await _select_event_for_payment(chat_id, state, app)
    if not selected:
        return

    user_result = await _select_unpaid_user(chat_id, state, app, selected)
    if not user_result:
        return
    reg, target_user_id, target_name = user_result

    amount = await _confirm_payment_amount(chat_id, state, target_name)
    if amount is None:
        return

    if target_user_id:
        await app.update_payment_status(
            user_id=int(target_user_id),
            event_id=selected,
            status="confirmed",
            payment_amount=amount,
            admin_id=message.from_user.id if message.from_user else None,
            admin_username=message.from_user.username if message.from_user else None,
        )

        # Notify the user
        try:
            from botspot.core.dependency_manager import get_dependency_manager

            bot = get_dependency_manager().bot
            await bot.send_message(
                int(target_user_id),
                f"Ваша оплата {amount}₽ подтверждена администратором. Спасибо!\n"
                "Именной билет отправляем следующим сообщением. "
                "Если он не появится, откройте /status.",
            )
        except Exception as e:
            logger.warning(f"Could not notify user {target_user_id}: {e}")

        await _send_admin_confirmed_ticket(app, int(target_user_id), selected)
    else:
        # No user_id — update by registration _id
        await app.collection.update_one(
            {"_id": reg["_id"]},
            {"$set": {"payment_status": "confirmed", "payment_amount": amount}},
        )

    await send_safe(
        chat_id,
        f"Оплата {amount}₽ подтверждена для {target_name}.",
    )
    await app.export_registered_users_to_google_sheets()


@commands_menu.add_command(
    "export",
    "Экспорт списка участников (активных и удаленных)",
    visibility=Visibility.ADMIN_ONLY,
)
@router.message(Command("export"), AdminFilter())
async def export_handler(message: Message, state: FSMContext, app: App):
    """Экспорт списка зарегистрированных или удаленных участников в Google Sheets или CSV"""
    notif = await send_safe(message.chat.id, "Подготовка экспорта...")

    # Ask user for export type
    export_type_response = await ask_user_choice(
        message.chat.id,
        "Что вы хотите экспортировать?",
        choices={
            "registered": "Зарегистрированные участники",
            "deleted": "Удаленные участники",
            "feedback": "Отзывы пользователей",
        },
        state=state,
        timeout=None,
    )

    # Ask which event to export
    event_choices = {"all": "Все встречи"}
    all_events = await app.get_all_events()
    for ev in all_events:
        eid = str(ev["_id"])
        event_choices[eid] = ev.get("name", ev.get("city", eid))
    event_response = await ask_user_choice(
        message.chat.id,
        "За какую встречу экспортировать?",
        choices=event_choices,
        state=state,
        timeout=None,
    )
    selected_event_id = event_response if event_response != "all" else None

    # Ask user for export format
    export_format_response = await ask_user_choice(
        message.chat.id,
        "Выберите формат экспорта:",
        choices={"sheets": "Google Таблицы", "csv": "CSV Файл"},
        state=state,
        timeout=None,
    )

    # Handle registered users export
    if export_type_response == "registered":
        if export_format_response == "sheets":
            await notif.edit_text("Экспорт данных в Google Таблицы...")
            result = await app.export_registered_users_to_google_sheets(
                event_id=selected_event_id, force=True
            )
            await send_safe(message.chat.id, result or "")
        else:
            await notif.edit_text("Экспорт данных в CSV файл...")
            csv_content, result_message = await app.export_to_csv(
                event_id=selected_event_id
            )

            if csv_content:
                await send_safe(
                    message.chat.id, csv_content, filename="участники_встречи.csv"
                )
            else:
                await send_safe(message.chat.id, result_message)

    # Handle deleted users export
    elif export_type_response == "deleted":
        if export_format_response == "sheets":
            await notif.edit_text("Экспорт удаленных участников в Google Таблицы...")
            await send_safe(
                message.chat.id,
                "Экспорт удаленных участников в Google Таблицы пока не поддерживается",
            )
        else:
            await notif.edit_text("Экспорт удаленных участников в CSV файл...")
            csv_content, result_message = await app.export_deleted_users_to_csv(
                event_id=selected_event_id
            )

            if csv_content:
                await send_safe(
                    message.chat.id, csv_content, filename="удаленные_участники.csv"
                )
            else:
                await send_safe(message.chat.id, result_message)

    # Handle feedback export
    elif export_type_response == "feedback":
        if export_format_response == "sheets":
            await notif.edit_text("Экспорт отзывов в Google Таблицы...")
            result = await app.export_feedback_to_sheets(event_id=selected_event_id)
            await send_safe(message.chat.id, result or "")
        else:
            await notif.edit_text("Экспорт отзывов в CSV файл...")
            csv_content, result_message = await app.export_feedback_to_csv(
                event_id=selected_event_id
            )

            if csv_content:
                await send_safe(
                    message.chat.id, csv_content, filename="отзывы_пользователей.csv"
                )
            else:
                await send_safe(message.chat.id, result_message)

    await notif.delete()


def _format_graduate_type(grad_type: str, plural=False):
    from src.app import GRADUATE_TYPE_MAP, GRADUATE_TYPE_MAP_PLURAL

    if plural:
        return GRADUATE_TYPE_MAP_PLURAL[grad_type.upper()]
    return GRADUATE_TYPE_MAP[grad_type.upper()]


@commands_menu.add_command(
    "normalize_db",
    "Нормализовать типы выпускников в БД",
    visibility=Visibility.ADMIN_ONLY,
)
@router.message(Command("normalize_db"), AdminFilter())
async def normalize_db(message: Message, app: App):
    """Normalize graduate types in the database"""

    # Send initial message
    status_msg = await send_safe(
        message.chat.id, "Нормализация типов выпускников в базе данных..."
    )

    # Run normalization
    modified = await app.normalize_graduate_types()

    # Update message with results
    await status_msg.edit_text(
        f"✅ Нормализация завершена. Обновлено записей: {modified}"
    )


# todo: auto-determine file type from name.
# async def extract_payment_from_image(
#         file_bytes: bytes
# file_name: str
# ) -> PaymentInfo:
# if file_name.endswith(".pdf"):
#     file_type = "application/pdf"
## elif file_name.endswith(".jpg") or file_name.endswith(".jpeg") or file_name.endswith(".png"):
# else:
#     file_type = "image/{file_name.split('.')[-1]}"
async def extract_payment_from_image(
    file_bytes: bytes, file_type: str = "image/jpeg"
) -> PaymentInfo:
    """Extract payment amount from an image or PDF using Claude Vision via litellm"""
    try:
        # Define the system prompt for payment extraction
        system_prompt = """You are a payment receipt analyzer.
        Your task is to extract ONLY the payment amount in rubles from the receipt image or PDF.

        If you cannot determine the amount or if it's ambiguous, set amount to null and is_valid to false."""

        # For images, encode to base64
        encoded_file = base64.b64encode(file_bytes).decode("utf-8")
        if file_type not in ["image/jpeg", "image/png", "application/pdf"]:
            raise ValueError(f"Unsupported file type: {file_type}")

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please extract the payment amount from this receipt:",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{file_type};base64,{encoded_file}"},
                    },
                ],
            },
        ]

        # Make the API call with the Pydantic model
        response = await acompletion(
            model="anthropic/claude-sonnet-4-6",
            messages=messages,
            max_tokens=100,
            response_format=PaymentInfo,
        )

        return PaymentInfo(**json.loads(response.choices[0].message.content))  # type: ignore[union-attr]
    except Exception as e:
        logger.error(f"Error extracting payment amount: {e}")
        return PaymentInfo(amount=None, is_valid=False)


def _get_file_info(response) -> Optional[tuple]:
    """Extract file_id and file_type from a message with photo or PDF.

    Returns (file_id, file_type) or None if no supported attachment found.
    """
    has_photo = response.photo is not None and len(response.photo) > 0
    has_pdf = (
        response.document is not None
        and response.document.mime_type == "application/pdf"
    )
    if has_photo and response.photo:
        return response.photo[-1].file_id, "image/jpeg"
    if has_pdf and response.document:
        return response.document.file_id, "application/pdf"
    return None


async def _download_file(file_id: str) -> Optional[bytes]:
    """Download a Telegram file by file_id. Returns file bytes or None."""
    from botspot.core.dependency_manager import get_dependency_manager

    bot = get_dependency_manager().bot

    file = await bot.get_file(file_id)
    if not file or not file.file_path:
        return None

    file_bytes = await bot.download_file(file.file_path)
    if not file_bytes:
        return None

    return file_bytes.read()


@commands_menu.add_command(
    "parse_payment", "Анализ платежа с помощью Claude", visibility=Visibility.ADMIN_ONLY
)
@router.message(Command("parse_payment"), AdminFilter())
async def parse_payment_handler(message: Message, state: FSMContext):
    """Hidden admin command to test payment parsing from images/PDFs"""
    # Ask user to send a payment proof
    response = await ask_user_raw(
        message.chat.id,
        "Отправьте скриншот или PDF с подтверждением платежа для анализа суммы платежа",
        state,
        timeout=300,  # 5 minutes timeout
    )

    if not response:
        await send_safe(message.chat.id, "Время ожидания истекло.")
        return

    file_info = _get_file_info(response)
    if not file_info:
        await send_safe(
            message.chat.id, "Пожалуйста, отправьте изображение или PDF-файл"
        )
        return

    file_id, file_type = file_info

    # Send status message
    status_msg = await send_safe(message.chat.id, "⏳ Анализирую платеж...")

    try:
        file_data = await _download_file(file_id)
        if not file_data:
            await status_msg.edit_text("❌ Не удалось скачать файл")
            return

        # Extract payment information directly from the file
        result = await extract_payment_from_image(file_data, file_type)

        # Format the response
        if result.is_valid:
            response_text = f"✅ Обнаружен платеж на сумму: <b>{result.amount}</b> руб."
        else:
            response_text = "❌ Не удалось извлечь сумму платежа"

        # Update the status message with the results
        await status_msg.edit_text(response_text, parse_mode="HTML")

    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")
