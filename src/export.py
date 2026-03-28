import base64
import json
import os
from typing import Dict, Optional

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

from src.app import App, GRADUATE_TYPE_MAP, PAYMENT_STATUS_MAP
from botspot import get_database

# Define the scopes for Google Sheets API
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Common headers for registered user exports
REGISTERED_HEADERS = [
    "ФИО",
    "Год выпуска",
    "Класс",
    "Название встречи",
    "Дата встречи",
    "Город",
    "Статус участника",
    "Telegram Username",
    "Статус оплаты",
    "Сумма оплаты (факт)",
    "Мин. сумма со скидкой",
    "Регулярная сумма",
    "Формула",
    "Дата оплаты",
    "Кол-во гостей",
    "Имена гостей",
]

DELETED_HEADERS = [
    "ФИО",
    "Год выпуска",
    "Класс",
    "Название встречи",
    "Дата встречи",
    "Город",
    "Статус участника",
    "Telegram Username",
    "Статус оплаты",
    "Сумма оплаты (факт)",
    "Дата удаления",
    "Причина удаления",
]

FEEDBACK_HEADERS = [
    "Имя",
    "Username",
    "ID пользователя",
    "Название встречи",
    "Дата встречи",
    "Был на встрече",
    "Город",
    "Рекомендация (1-5)",
    "Площадка (1-5)",
    "Еда (1-5)",
    "Развлечения (1-5)",
    "Будет помогать",
    "Комментарии",
    "Предпочитаемый формат обратной связи",
    "Дата отзыва",
]


def _build_events_map(events: list) -> Dict[str, Dict]:
    """Build {event_id_str: event_doc} lookup from a list of event documents."""
    return {str(e["_id"]): e for e in events}


def _event_name(event: Optional[Dict]) -> str:
    return event.get("name", "") if event else ""


def _event_date(event: Optional[Dict]) -> str:
    if not event:
        return ""
    return event.get("date_display", "") or str(event.get("date", ""))


def _build_registered_row(user: Dict, event: Optional[Dict]) -> list:
    """Build a row for registered user export."""
    raw_status = user.get("payment_status", None)
    payment_status = PAYMENT_STATUS_MAP.get(raw_status, PAYMENT_STATUS_MAP[None])
    graduate_type = user.get("graduate_type", "GRADUATE")
    graduate_type_display = GRADUATE_TYPE_MAP.get(graduate_type, "Выпускник")
    guests = user.get("guests", [])
    guest_count = user.get("guest_count", len(guests))
    guest_names = ", ".join(g.get("name", "") for g in guests) if guests else ""

    return [
        user["full_name"],
        user["graduation_year"],
        user["class_letter"],
        _event_name(event),
        _event_date(event),
        user["target_city"],
        graduate_type_display,
        user.get("username", ""),
        payment_status,
        user.get("payment_amount", 0),
        user.get("discounted_payment_amount", 0),
        user.get("regular_payment_amount", 0),
        user.get("formula_payment_amount", 0),
        user.get("payment_timestamp", ""),
        guest_count,
        guest_names,
    ]


def _build_deleted_row(user: Dict, event: Optional[Dict]) -> list:
    """Build a row for deleted user export."""
    raw_status = user.get("payment_status", None)
    payment_status = PAYMENT_STATUS_MAP.get(raw_status, PAYMENT_STATUS_MAP[None])
    graduate_type = user.get("graduate_type", "GRADUATE")
    graduate_type_display = GRADUATE_TYPE_MAP.get(graduate_type, "Выпускник")

    return [
        user["full_name"],
        user["graduation_year"],
        user["class_letter"],
        _event_name(event),
        _event_date(event),
        user["target_city"],
        graduate_type_display,
        user.get("username", ""),
        payment_status,
        user.get("payment_amount", 0),
        user.get("deletion_timestamp", ""),
        user.get("deletion_reason", ""),
    ]


def _build_feedback_row(item: Dict, event: Optional[Dict]) -> list:
    """Build a row for feedback export."""
    attended = "Да" if item.get("attended") else "Нет"

    help_interest = item.get("help_interest", "")
    if help_interest == "yes":
        help_interest = "Да"
    elif help_interest == "no":
        help_interest = "Нет"
    elif help_interest == "maybe":
        help_interest = "Возможно"

    feedback_format = item.get("feedback_format_preference", "")
    if feedback_format == "bot":
        feedback_format = "Через бота"
    elif feedback_format == "google_forms":
        feedback_format = "Гугл формы"

    return [
        item.get("full_name", ""),
        item.get("username", ""),
        item.get("user_id", ""),
        _event_name(event),
        _event_date(event),
        attended,
        item.get("city", ""),
        item.get("recommendation_level", ""),
        item.get("venue_rating", ""),
        item.get("food_rating", ""),
        item.get("entertainment_rating", ""),
        help_interest,
        item.get("comments", ""),
        feedback_format,
        item.get("timestamp", ""),
    ]


class SheetExporter:
    def __init__(self, spreadsheet_id: str, app: App):
        self.spreadsheet_id = spreadsheet_id
        self.app = app

    def _get_client(self):
        """Create and return an authorized Google Sheets client using credentials from env var"""
        # First try to get base64 encoded credentials
        creds_base64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
        if creds_base64:
            # Decode base64 string to JSON string
            try:
                creds_json = base64.b64decode(creds_base64).decode("utf-8")
                creds_info = json.loads(creds_json)
                logger.info("Using base64 encoded credentials")
            except Exception as e:
                logger.error(f"Error decoding base64 credentials: {e}")
                raise ValueError("Invalid GOOGLE_CREDENTIALS_BASE64 format")
        else:
            # Fall back to regular JSON string if base64 not available
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                # Check if credentials file exists
                creds_file = os.getenv(
                    "GOOGLE_CREDENTIALS_FILE", "google-service-user-credentials.json"
                )
                if os.path.exists(creds_file):
                    logger.info(f"Using credentials file: {creds_file}")
                    credentials = Credentials.from_service_account_file(
                        creds_file, scopes=SCOPES
                    )
                    return gspread.authorize(credentials)
                else:
                    raise ValueError(
                        "No Google credentials found. Set GOOGLE_CREDENTIALS_BASE64, GOOGLE_CREDENTIALS_JSON, or provide a credentials file."
                    )

            try:
                creds_info = json.loads(creds_json)
                logger.info("Using JSON string credentials")
            except json.JSONDecodeError:
                logger.error("Invalid JSON in GOOGLE_CREDENTIALS_JSON")
                raise ValueError("Invalid GOOGLE_CREDENTIALS_JSON format")

        # Create credentials object from the dictionary
        credentials = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return gspread.authorize(credentials)

    async def _prefetch_events_map(self) -> Dict[str, Dict]:
        """Prefetch all events into a lookup dict."""
        all_events = await self.app.get_all_events()
        return _build_events_map(all_events)

    async def export_registered_users(
        self, silent=False, event_id: Optional[str] = None
    ):
        """Export registered users to Google Sheets."""
        query = {"event_id": event_id} if event_id else {}
        cursor = self.app.collection.find(query)
        users = await cursor.to_list(length=None)

        if not users:
            logger.info("Нет пользователей для экспорта")
            if not silent:
                return "Нет пользователей для экспорта"
            return None

        events_map = await self._prefetch_events_map()

        # Connect to Google Sheets
        client = self._get_client()
        spreadsheet = client.open_by_key(self.spreadsheet_id)
        worksheet_titles = [ws.title for ws in spreadsheet.worksheets()]

        # Main sheet
        if "Все встречи" not in worksheet_titles:
            spreadsheet.add_worksheet(title="Все встречи", rows=1000, cols=20)
        main_sheet = spreadsheet.worksheet("Все встречи")
        main_sheet.clear()

        # Dynamic event-specific sheets (replace hardcoded cities)
        event_sheets = {}
        for eid, ev in events_map.items():
            tab_name = ev.get("name", ev.get("city", eid))[
                :100
            ]  # Sheets tab name limit
            if tab_name not in worksheet_titles:
                spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=20)
                worksheet_titles.append(tab_name)
            event_sheets[eid] = spreadsheet.worksheet(tab_name)
            event_sheets[eid].clear()

        # Graduate type sheets
        type_sheets = {}
        for graduate_type in ["Выпускники", "Учителя", "Друзья", "Организаторы"]:
            if graduate_type not in worksheet_titles:
                spreadsheet.add_worksheet(title=graduate_type, rows=1000, cols=20)
            type_sheets[graduate_type] = spreadsheet.worksheet(graduate_type)
            type_sheets[graduate_type].clear()

        # Update all sheets with headers
        main_sheet.update([REGISTERED_HEADERS])
        for sheet in event_sheets.values():
            sheet.update([REGISTERED_HEADERS])
        for sheet in type_sheets.values():
            sheet.update([REGISTERED_HEADERS])

        # Build rows
        main_rows = []
        event_rows = {eid: [] for eid in event_sheets}
        type_rows = {gt: [] for gt in type_sheets}

        for user in users:
            event = events_map.get(user.get("event_id", ""))
            row = _build_registered_row(user, event)
            main_rows.append(row)

            # Route to event sheet
            uid_event = user.get("event_id", "")
            if uid_event in event_rows:
                event_rows[uid_event].append(row)

            # Route to type sheet
            graduate_type = user.get("graduate_type", "GRADUATE")
            graduate_type_display = GRADUATE_TYPE_MAP.get(graduate_type, "Выпускник")
            if graduate_type_display == "Выпускник":
                type_rows["Выпускники"].append(row)
            elif graduate_type_display == "Учитель":
                type_rows["Учителя"].append(row)
            elif graduate_type_display == "Друг":
                type_rows["Друзья"].append(row)
            elif graduate_type_display == "Организатор":
                type_rows["Организаторы"].append(row)

        # Write data
        if main_rows:
            main_sheet.update(main_rows, "A2")
        for eid, rows in event_rows.items():
            if rows:
                event_sheets[eid].update(rows, "A2")
        for gt, rows in type_rows.items():
            if rows:
                type_sheets[gt].update(rows, "A2")

        message = (
            f"Успешно экспортировано {len(main_rows)} пользователей в Google Таблицы\n"
        )
        message += "Доступно по ссылке: " + main_sheet.url
        logger.success(message)

        if not silent:
            return message
        return None

    async def export_to_csv(self, event_id: Optional[str] = None):
        """Export registered users to a CSV file."""
        try:
            query = {"event_id": event_id} if event_id else {}
            cursor = self.app.collection.find(query)
            users = await cursor.to_list(length=None)

            if not users:
                logger.info("Нет пользователей для экспорта")
                return None, "Нет пользователей для экспорта"

            events_map = await self._prefetch_events_map()

            import csv
            from io import StringIO

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(REGISTERED_HEADERS)

            for user in users:
                event = events_map.get(user.get("event_id", ""))
                writer.writerow(_build_registered_row(user, event))

            csv_content = output.getvalue()
            output.close()

            logger.success(f"Успешно экспортировано {len(users)} пользователей в CSV")
            return (
                csv_content,
                f"Успешно экспортировано {len(users)} пользователей в CSV",
            )

        except Exception as e:
            logger.error(f"Ошибка при экспорте данных в CSV: {e}")
            return None, f"Ошибка при экспорте данных в CSV: {e}"

    async def export_deleted_users_to_csv(self, event_id: Optional[str] = None):
        """Export deleted users to a CSV file."""
        query = {"event_id": event_id} if event_id else {}
        cursor = self.app.deleted_users.find(query)
        users = await cursor.to_list(length=None)

        if not users:
            logger.info("Нет удаленных пользователей для экспорта")
            return None, "Нет удаленных пользователей для экспорта"

        events_map = await self._prefetch_events_map()

        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(DELETED_HEADERS)

        for user in users:
            event = events_map.get(user.get("event_id", ""))
            writer.writerow(_build_deleted_row(user, event))

        csv_content = output.getvalue()
        output.close()

        logger.success(
            f"Успешно экспортировано {len(users)} удаленных пользователей в CSV"
        )
        return (
            csv_content,
            f"Успешно экспортировано {len(users)} удаленных пользователей в CSV",
        )

    async def export_feedback_to_sheets(
        self, silent=False, event_id: Optional[str] = None
    ):
        """Export feedback to a dedicated sheet in the Google Spreadsheet."""
        if not hasattr(self.app, "_feedback_collection"):
            self.app._feedback_collection = get_database().get_collection("feedback")

        query = {"event_id": event_id} if event_id else {}
        cursor = self.app._feedback_collection.find(query)
        feedback_items = await cursor.to_list(length=None)

        if not feedback_items:
            logger.info("Нет отзывов для экспорта")
            if not silent:
                return "Нет отзывов для экспорта"
            return None

        events_map = await self._prefetch_events_map()

        client = self._get_client()
        spreadsheet = client.open_by_key(self.spreadsheet_id)
        worksheet_titles = [ws.title for ws in spreadsheet.worksheets()]

        if "Отзывы" not in worksheet_titles:
            spreadsheet.add_worksheet(title="Отзывы", rows=1000, cols=20)
        feedback_sheet = spreadsheet.worksheet("Отзывы")
        feedback_sheet.clear()

        feedback_sheet.update([FEEDBACK_HEADERS])

        feedback_rows = []
        for item in feedback_items:
            event = events_map.get(item.get("event_id", ""))
            feedback_rows.append(_build_feedback_row(item, event))

        if feedback_rows:
            feedback_sheet.update(feedback_rows, "A2")

        message = (
            f"Успешно экспортировано {len(feedback_rows)} отзывов в Google Таблицы\n"
        )
        message += "Доступно по ссылке: " + feedback_sheet.url
        logger.success(message)

        if not silent:
            return message
        return None

    async def export_feedback_to_csv(self, event_id: Optional[str] = None):
        """Export feedback to a CSV file."""
        try:
            if not hasattr(self.app, "_feedback_collection"):
                self.app._feedback_collection = get_database().get_collection(
                    "feedback"
                )

            query = {"event_id": event_id} if event_id else {}
            cursor = self.app._feedback_collection.find(query)
            feedback_items = await cursor.to_list(length=None)

            if not feedback_items:
                logger.info("Нет отзывов для экспорта")
                return None, "Нет отзывов для экспорта"

            events_map = await self._prefetch_events_map()

            import csv
            from io import StringIO

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(FEEDBACK_HEADERS)

            for item in feedback_items:
                event = events_map.get(item.get("event_id", ""))
                writer.writerow(_build_feedback_row(item, event))

            csv_content = output.getvalue()
            output.close()

            logger.success(
                f"Успешно экспортировано {len(feedback_items)} отзывов в CSV"
            )
            return (
                csv_content,
                f"Успешно экспортировано {len(feedback_items)} отзывов в CSV",
            )

        except Exception as e:
            logger.error(f"Ошибка при экспорте отзывов в CSV: {e}")
            return None, f"Ошибка при экспорте отзывов в CSV: {e}"
