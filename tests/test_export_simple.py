import pytest
from unittest.mock import MagicMock, AsyncMock
from bson import ObjectId

from src.export import (
    SheetExporter,
    _build_registered_row,
    _build_deleted_row,
    _build_feedback_row,
    _event_name,
    _event_date,
    _build_events_map,
    REGISTERED_HEADERS,
    DELETED_HEADERS,
    FEEDBACK_HEADERS,
)

# --- Shared test data ---

test_event_id = str(ObjectId())
test_events = [
    {
        "_id": ObjectId(test_event_id),
        "name": "Москва (Весна 2026)",
        "city": "Москва",
        "date_display": "21 Марта, Сб",
    }
]

_BASE_USER = {
    "full_name": "Иванов Иван",
    "graduation_year": 2010,
    "class_letter": "А",
    "target_city": "Москва",
    "graduate_type": "GRADUATE",
    "payment_status": "confirmed",
    "payment_amount": 2000,
    "username": "ivan",
}


# ─── _event_name / _event_date ───────────────────────────────────────────────


class TestEventHelpers:
    def test_event_name_none(self):
        assert _event_name(None) == ""

    def test_event_name_with_data(self):
        assert _event_name(test_events[0]) == "Москва (Весна 2026)"

    def test_event_name_missing_field(self):
        assert _event_name({"city": "Пермь"}) == ""

    def test_event_date_none(self):
        assert _event_date(None) == ""

    def test_event_date_with_date_display(self):
        assert _event_date(test_events[0]) == "21 Марта, Сб"

    def test_event_date_falls_back_to_date_field(self):
        event = {"date": "2026-03-21"}
        assert _event_date(event) == "2026-03-21"

    def test_event_date_empty_date_display_falls_back(self):
        event = {"date_display": "", "date": "2026-03-21"}
        assert _event_date(event) == "2026-03-21"

    def test_event_date_missing_both_fields(self):
        assert _event_date({}) == ""


# ─── _build_events_map ───────────────────────────────────────────────────────


class TestBuildEventsMap:
    def test_empty_list(self):
        assert _build_events_map([]) == {}

    def test_maps_by_string_id(self):
        result = _build_events_map(test_events)
        assert test_event_id in result
        assert result[test_event_id]["name"] == "Москва (Весна 2026)"

    def test_multiple_events(self):
        eid1 = ObjectId()
        eid2 = ObjectId()
        events = [
            {"_id": eid1, "name": "Event A"},
            {"_id": eid2, "name": "Event B"},
        ]
        result = _build_events_map(events)
        assert len(result) == 2
        assert result[str(eid1)]["name"] == "Event A"
        assert result[str(eid2)]["name"] == "Event B"


# ─── _build_registered_row ───────────────────────────────────────────────────


class TestBuildRegisteredRow:
    def test_with_event(self):
        user = {**_BASE_USER, "event_id": test_event_id}
        row = _build_registered_row(user, test_events[0])
        assert row[0] == "Иванов Иван"
        assert row[3] == "Москва (Весна 2026)"
        assert row[4] == "21 Марта, Сб"
        assert row[5] == "Москва"
        assert row[6] == "Выпускник"
        assert row[7] == "ivan"
        assert row[8] == "Оплачено"
        assert row[9] == 2000

    def test_without_event(self):
        row = _build_registered_row(_BASE_USER, None)
        assert row[3] == ""
        assert row[4] == ""

    def test_payment_status_none(self):
        user = {**_BASE_USER, "payment_status": None}
        row = _build_registered_row(user, None)
        assert row[8] == "Не оплачено"

    def test_payment_status_pending(self):
        user = {**_BASE_USER, "payment_status": "pending"}
        row = _build_registered_row(user, None)
        assert row[8] == "Оплачу позже"

    def test_graduate_type_teacher(self):
        user = {**_BASE_USER, "graduate_type": "TEACHER"}
        row = _build_registered_row(user, None)
        assert row[6] == "Учитель"

    def test_graduate_type_non_graduate(self):
        user = {**_BASE_USER, "graduate_type": "NON_GRADUATE"}
        row = _build_registered_row(user, None)
        assert row[6] == "Друг"

    def test_guests_counted_from_list(self):
        user = {
            **_BASE_USER,
            "guests": [{"name": "Гость А"}, {"name": "Гость Б"}],
        }
        row = _build_registered_row(user, None)
        assert row[14] == 2
        assert "Гость А" in row[15]
        assert "Гость Б" in row[15]

    def test_guest_count_override(self):
        user = {**_BASE_USER, "guest_count": 5, "guests": []}
        row = _build_registered_row(user, None)
        assert row[14] == 5

    def test_no_username_defaults_empty(self):
        user = {k: v for k, v in _BASE_USER.items() if k != "username"}
        row = _build_registered_row(user, None)
        assert row[7] == ""

    def test_row_length_matches_headers(self):
        row = _build_registered_row(_BASE_USER, test_events[0])
        assert len(row) == len(REGISTERED_HEADERS)


# ─── _build_deleted_row ──────────────────────────────────────────────────────


class TestBuildDeletedRow:
    _DELETED_USER = {
        **_BASE_USER,
        "deletion_timestamp": "2026-03-01T10:00:00",
        "deletion_reason": "Не смогу прийти",
    }

    def test_with_event(self):
        row = _build_deleted_row(self._DELETED_USER, test_events[0])
        assert row[0] == "Иванов Иван"
        assert row[3] == "Москва (Весна 2026)"
        assert row[4] == "21 Марта, Сб"
        assert row[8] == "Оплачено"
        assert row[10] == "2026-03-01T10:00:00"
        assert row[11] == "Не смогу прийти"

    def test_without_event(self):
        row = _build_deleted_row(self._DELETED_USER, None)
        assert row[3] == ""
        assert row[4] == ""

    def test_payment_status_none(self):
        user = {**self._DELETED_USER, "payment_status": None}
        row = _build_deleted_row(user, None)
        assert row[8] == "Не оплачено"

    def test_missing_deletion_fields_default_empty(self):
        user = {k: v for k, v in _BASE_USER.items()}
        row = _build_deleted_row(user, None)
        assert row[10] == ""
        assert row[11] == ""

    def test_row_length_matches_headers(self):
        row = _build_deleted_row(self._DELETED_USER, test_events[0])
        assert len(row) == len(DELETED_HEADERS)


# ─── _build_feedback_row ─────────────────────────────────────────────────────


class TestBuildFeedbackRow:
    _BASE_FEEDBACK = {
        "full_name": "Петрова Анна",
        "username": "anna",
        "user_id": 999,
        "event_id": test_event_id,
        "attended": True,
        "city": "Москва",
        "recommendation_level": 5,
        "venue_rating": 4,
        "food_rating": 3,
        "entertainment_rating": 4,
        "help_interest": "yes",
        "comments": "Всё отлично!",
        "feedback_format_preference": "bot",
        "timestamp": "2026-03-22",
    }

    def test_attended_true(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, test_events[0])
        assert row[5] == "Да"

    def test_attended_false(self):
        item = {**self._BASE_FEEDBACK, "attended": False}
        row = _build_feedback_row(item, None)
        assert row[5] == "Нет"

    def test_attended_missing_defaults_no(self):
        item = {k: v for k, v in self._BASE_FEEDBACK.items() if k != "attended"}
        row = _build_feedback_row(item, None)
        assert row[5] == "Нет"

    def test_help_interest_yes(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, None)
        assert row[11] == "Да"

    def test_help_interest_no(self):
        item = {**self._BASE_FEEDBACK, "help_interest": "no"}
        row = _build_feedback_row(item, None)
        assert row[11] == "Нет"

    def test_help_interest_maybe(self):
        item = {**self._BASE_FEEDBACK, "help_interest": "maybe"}
        row = _build_feedback_row(item, None)
        assert row[11] == "Возможно"

    def test_help_interest_unknown_passthrough(self):
        item = {**self._BASE_FEEDBACK, "help_interest": "unknown_val"}
        row = _build_feedback_row(item, None)
        assert row[11] == "unknown_val"

    def test_feedback_format_bot(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, None)
        assert row[13] == "Через бота"

    def test_feedback_format_google_forms(self):
        item = {**self._BASE_FEEDBACK, "feedback_format_preference": "google_forms"}
        row = _build_feedback_row(item, None)
        assert row[13] == "Гугл формы"

    def test_feedback_format_unknown_passthrough(self):
        item = {**self._BASE_FEEDBACK, "feedback_format_preference": "other"}
        row = _build_feedback_row(item, None)
        assert row[13] == "other"

    def test_with_event(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, test_events[0])
        assert row[3] == "Москва (Весна 2026)"
        assert row[4] == "21 Марта, Сб"

    def test_without_event(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, None)
        assert row[3] == ""
        assert row[4] == ""

    def test_row_length_matches_headers(self):
        row = _build_feedback_row(self._BASE_FEEDBACK, test_events[0])
        assert len(row) == len(FEEDBACK_HEADERS)


# ─── SheetExporter.export_to_csv ─────────────────────────────────────────────


def _make_mock_app(users=None, deleted_users=None, events=None):
    mock_app = MagicMock()

    if users is None:
        users = []
    if deleted_users is None:
        deleted_users = []
    if events is None:
        events = []

    users_cursor = AsyncMock()
    users_cursor.to_list.return_value = users
    mock_app.collection.find.return_value = users_cursor

    deleted_cursor = AsyncMock()
    deleted_cursor.to_list.return_value = deleted_users
    mock_app.deleted_users.find.return_value = deleted_cursor

    mock_app.get_all_events = AsyncMock(return_value=events)
    return mock_app


class TestExportToCsv:
    @pytest.mark.asyncio
    async def test_no_users_returns_none(self):
        app = _make_mock_app(users=[])
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_to_csv()
        assert csv_content is None
        assert "Нет" in msg

    @pytest.mark.asyncio
    async def test_users_without_event(self):
        users = [{**_BASE_USER}]
        app = _make_mock_app(users=users, events=[])
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_to_csv()
        assert csv_content is not None
        assert "Иванов Иван" in csv_content
        assert "экспортировано" in msg.lower()

    @pytest.mark.asyncio
    async def test_users_with_event(self):
        users = [{**_BASE_USER, "event_id": test_event_id}]
        app = _make_mock_app(users=users, events=test_events)
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_to_csv()
        assert "Москва (Весна 2026)" in csv_content
        assert "21 Марта, Сб" in csv_content

    @pytest.mark.asyncio
    async def test_event_id_filter_passes_query(self):
        users = [{**_BASE_USER, "event_id": test_event_id}]
        app = _make_mock_app(users=users, events=test_events)
        exporter = SheetExporter("sheet_id", app)
        await exporter.export_to_csv(event_id=test_event_id)
        mock_app = app
        mock_app.collection.find.assert_called_once_with({"event_id": test_event_id})

    @pytest.mark.asyncio
    async def test_csv_has_headers(self):
        users = [{**_BASE_USER}]
        app = _make_mock_app(users=users)
        exporter = SheetExporter("sheet_id", app)
        csv_content, _ = await exporter.export_to_csv()
        first_line = csv_content.splitlines()[0]
        assert "ФИО" in first_line

    @pytest.mark.asyncio
    async def test_multiple_users(self):
        users = [
            {**_BASE_USER, "full_name": "Пользователь А"},
            {**_BASE_USER, "full_name": "Пользователь Б", "graduate_type": "TEACHER"},
        ]
        app = _make_mock_app(users=users)
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_to_csv()
        assert "Пользователь А" in csv_content
        assert "Пользователь Б" in csv_content
        assert "2 пользователей" in msg


# ─── SheetExporter.export_deleted_users_to_csv ───────────────────────────────


class TestExportDeletedUsersToCsv:
    _DELETED = {
        **_BASE_USER,
        "deletion_timestamp": "2026-03-01",
        "deletion_reason": "Отмена",
    }

    @pytest.mark.asyncio
    async def test_no_users_returns_none(self):
        app = _make_mock_app(deleted_users=[])
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_deleted_users_to_csv()
        assert csv_content is None
        assert "Нет" in msg

    @pytest.mark.asyncio
    async def test_deleted_users_exported(self):
        app = _make_mock_app(deleted_users=[self._DELETED], events=test_events)
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_deleted_users_to_csv()
        assert csv_content is not None
        assert "Иванов Иван" in csv_content
        assert "экспортировано" in msg.lower()

    @pytest.mark.asyncio
    async def test_deleted_csv_has_headers(self):
        app = _make_mock_app(deleted_users=[self._DELETED])
        exporter = SheetExporter("sheet_id", app)
        csv_content, _ = await exporter.export_deleted_users_to_csv()
        first_line = csv_content.splitlines()[0]
        assert "Дата удаления" in first_line

    @pytest.mark.asyncio
    async def test_event_id_filter_passes_query(self):
        app = _make_mock_app(
            deleted_users=[{**self._DELETED, "event_id": test_event_id}],
            events=test_events,
        )
        exporter = SheetExporter("sheet_id", app)
        await exporter.export_deleted_users_to_csv(event_id=test_event_id)
        app.deleted_users.find.assert_called_once_with({"event_id": test_event_id})


# ─── SheetExporter.export_feedback_to_csv ────────────────────────────────────


class TestExportFeedbackToCsv:
    _FEEDBACK = {
        "full_name": "Петрова Анна",
        "username": "anna",
        "user_id": 999,
        "event_id": test_event_id,
        "attended": True,
        "city": "Москва",
        "recommendation_level": 5,
        "venue_rating": 4,
        "food_rating": 4,
        "entertainment_rating": 5,
        "help_interest": "yes",
        "comments": "Супер!",
        "feedback_format_preference": "bot",
        "timestamp": "2026-03-22",
    }

    def _make_app_with_feedback(self, feedback_items, events=None):
        app = _make_mock_app(events=events or test_events)
        feedback_cursor = AsyncMock()
        feedback_cursor.to_list.return_value = feedback_items
        app._feedback_collection = MagicMock()
        app._feedback_collection.find.return_value = feedback_cursor
        return app

    @pytest.mark.asyncio
    async def test_no_feedback_returns_none(self):
        app = self._make_app_with_feedback([])
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_feedback_to_csv()
        assert csv_content is None
        assert "Нет" in msg

    @pytest.mark.asyncio
    async def test_feedback_exported(self):
        app = self._make_app_with_feedback([self._FEEDBACK])
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_feedback_to_csv()
        assert csv_content is not None
        assert "Петрова Анна" in csv_content
        assert "экспортировано" in msg.lower()

    @pytest.mark.asyncio
    async def test_feedback_csv_has_headers(self):
        app = self._make_app_with_feedback([self._FEEDBACK])
        exporter = SheetExporter("sheet_id", app)
        csv_content, _ = await exporter.export_feedback_to_csv()
        first_line = csv_content.splitlines()[0]
        assert "Был на встрече" in first_line

    @pytest.mark.asyncio
    async def test_feedback_with_event_data(self):
        app = self._make_app_with_feedback([self._FEEDBACK], events=test_events)
        exporter = SheetExporter("sheet_id", app)
        csv_content, _ = await exporter.export_feedback_to_csv()
        assert "Москва (Весна 2026)" in csv_content
        assert "21 Марта, Сб" in csv_content

    @pytest.mark.asyncio
    async def test_feedback_event_id_filter(self):
        app = self._make_app_with_feedback([self._FEEDBACK], events=test_events)
        exporter = SheetExporter("sheet_id", app)
        await exporter.export_feedback_to_csv(event_id=test_event_id)
        app._feedback_collection.find.assert_called_once_with(
            {"event_id": test_event_id}
        )

    @pytest.mark.asyncio
    async def test_multiple_feedback_items(self):
        items = [
            self._FEEDBACK,
            {**self._FEEDBACK, "full_name": "Сидоров Сергей", "attended": False},
        ]
        app = self._make_app_with_feedback(items)
        exporter = SheetExporter("sheet_id", app)
        csv_content, msg = await exporter.export_feedback_to_csv()
        assert "Петрова Анна" in csv_content
        assert "Сидоров Сергей" in csv_content
        assert "2 отзывов" in msg


# ─── Legacy test (kept for back-compat) ──────────────────────────────────────


class TestExportFunctions:
    @pytest.fixture
    def mock_app(self):
        mock_app = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.to_list.return_value = [
            {
                "full_name": "Test User 1",
                "graduation_year": 2010,
                "class_letter": "A",
                "target_city": "Москва",
                "event_id": "evt1",
                "user_id": 12345,
                "username": "user1",
                "graduate_type": "GRADUATE",
                "payment_status": "confirmed",
                "payment_amount": 2000,
            },
            {
                "full_name": "Test User 2",
                "graduation_year": 2005,
                "class_letter": "Б",
                "target_city": "Пермь",
                "event_id": "evt2",
                "user_id": 67890,
                "username": "user2",
                "graduate_type": "TEACHER",
            },
        ]
        mock_app.collection.find.return_value = mock_cursor
        mock_app.get_all_events = AsyncMock(return_value=[])
        return mock_app

    @pytest.mark.asyncio
    async def test_export_to_csv(self, mock_app):
        """Test export_to_csv function"""
        exporter = SheetExporter("test_sheet_id", app=mock_app)
        csv_content, message = await exporter.export_to_csv()

        mock_app.collection.find.assert_called_once_with({})

        assert csv_content is not None
        assert "Test User 1" in csv_content
        assert "Test User 2" in csv_content
        assert "Москва" in csv_content
        assert "Пермь" in csv_content

        assert "экспортировано" in message.lower()
        assert "2 пользователей" in message
