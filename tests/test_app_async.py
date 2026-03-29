"""Tests for async App methods with mocked database."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.app import App, RegisteredUser, FeedbackData, GraduateType


@pytest.fixture
def app():
    mock_collection = AsyncMock()
    mock_event_logs = AsyncMock()
    mock_deleted_users = AsyncMock()
    mock_events_col = AsyncMock()

    mock_db = MagicMock()

    def get_collection(name):
        if name == "registered_users":
            return mock_collection
        elif name == "event_logs":
            return mock_event_logs
        elif name == "deleted_users":
            return mock_deleted_users
        elif name == "events":
            return mock_events_col
        elif name == "feedback":
            return AsyncMock()
        return AsyncMock()

    mock_db.get_collection = get_collection

    with patch("src.app.get_database", return_value=mock_db):
        a = App(
            telegram_bot_token="mock_token",
            spreadsheet_id="mock_sheet",
            payment_phone_number="123",
            payment_name="Test",
        )
        # Force initialize collections
        _ = a.collection
        _ = a.event_logs
        _ = a.deleted_users
        _ = a.events_col
        return a


class TestSaveRegisteredUser:
    @pytest.mark.asyncio
    async def test_new_registration(self, app):
        app.collection.find_one = AsyncMock(return_value=None)
        app.collection.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="abc123")
        )
        app.event_logs.insert_one = AsyncMock()

        user = RegisteredUser(
            full_name="Иванов Иван",
            graduation_year=2010,
            class_letter="А",
            target_city="Москва",
            event_id="aabbccddeeff00112233aabb",
        )
        await app.save_registered_user(user, user_id=12345, username="ivan")
        app.collection.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_existing(self, app):
        app.collection.find_one = AsyncMock(
            return_value={"_id": "existing_id", "user_id": 12345}
        )
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        user = RegisteredUser(
            full_name="Иванов Иван",
            graduation_year=2010,
            class_letter="А",
            target_city="Москва",
            event_id="aabbccddeeff00112233aabb",
        )
        await app.save_registered_user(user, user_id=12345, username="ivan")
        app.collection.update_one.assert_called_once()


class TestSaveRegistrationGuests:
    @pytest.mark.asyncio
    async def test_save_guests(self, app):
        app.collection.update_one = AsyncMock()
        guests = [{"name": "Гость 1", "price": 2000}]
        await app.save_registration_guests(12345, "Москва", guests)
        app.collection.update_one.assert_called_once()
        call_args = app.collection.update_one.call_args
        assert call_args[0][1]["$set"]["guest_count"] == 1


class TestGetUserRegistrations:
    @pytest.mark.asyncio
    async def test_get_registrations(self, app):
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[{"user_id": 123, "target_city": "Москва"}]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_user_registrations(123)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_registration_single(self, app):
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[{"user_id": 123, "target_city": "Москва"}]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_user_registration(123)
        assert result is not None
        assert result["user_id"] == 123

    @pytest.mark.asyncio
    async def test_get_registration_none(self, app):
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_user_registration(123)
        assert result is None


class TestDeleteUserRegistration:
    @pytest.mark.asyncio
    async def test_delete_with_event_id(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {
                    "user_id": 123,
                    "target_city": "Москва",
                    "event_id": "aabbccddeeff00112233aabb",
                }
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)
        app.collection.find_one = AsyncMock(
            return_value={
                "user_id": 123,
                "target_city": "Москва",
                "event_id": "aabbccddeeff00112233aabb",
            }
        )
        app.collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        app.deleted_users.insert_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.delete_user_registration(
            123, event_id="aabbccddeeff00112233aabb", username="test", full_name="Test"
        )
        app.event_logs.insert_one.assert_called()


class TestEventMethods:
    @pytest.mark.asyncio
    async def test_get_active_events(self, app):
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[{"city": "Москва"}])
        app.events_col.find = MagicMock(return_value=mock_cursor)

        result = await app.get_active_events()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_enabled_events(self, app):
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.events_col.find = MagicMock(return_value=mock_cursor)

        result = await app.get_enabled_events()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_all_events(self, app):
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[{"city": "Москва"}])
        app.events_col.find = MagicMock(return_value=mock_cursor)

        result = await app.get_all_events()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_event_by_id(self, app):
        app.events_col.find_one = AsyncMock(
            return_value={"city": "Москва", "_id": "abc"}
        )
        result = await app.get_event_by_id("507f1f77bcf86cd799439011")
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_event_by_id_invalid(self, app):
        result = await app.get_event_by_id("invalid")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_event(self, app):
        app.events_col.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id="new_id")
        )
        result = await app.create_event({"city": "Москва"})
        assert result == "new_id"

    @pytest.mark.asyncio
    async def test_update_event(self, app):
        app.events_col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        result = await app.update_event("507f1f77bcf86cd799439011", {"venue": "New"})
        assert result is True

    @pytest.mark.asyncio
    async def test_get_registration_count(self, app):
        app.collection.count_documents = AsyncMock(return_value=5)
        result = await app.get_registration_count_for_event("abc")
        assert result == 5

    @pytest.mark.asyncio
    async def test_get_event_for_registration_with_event_id(self, app):
        app.events_col.find_one = AsyncMock(return_value={"city": "Москва"})
        reg = {"event_id": "507f1f77bcf86cd799439011"}
        result = await app.get_event_for_registration(reg)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_event_for_registration_legacy(self, app):
        app.events_col.find_one = AsyncMock(return_value={"city": "Москва"})
        reg = {"target_city": "Москва"}
        result = await app.get_event_for_registration(reg)
        assert result is not None


class TestSavePaymentInfo:
    @pytest.mark.asyncio
    async def test_save_with_screenshot(self, app):
        app.collection.find_one = AsyncMock(
            return_value={"full_name": "Test", "user_id": 123}
        )
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.save_payment_info(
            user_id=123,
            event_id="aabbccddeeff00112233aabb",
            discounted_amount=1800,
            regular_amount=2000,
            screenshot_message_id=999,
            formula_amount=3000,
            username="test",
            payment_status="pending",
        )
        app.collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_without_formula(self, app):
        app.collection.find_one = AsyncMock(return_value=None)
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.save_payment_info(user_id=123, event_id="aabbccddeeff00112233aabb")
        app.collection.update_one.assert_called_once()


class TestUpdatePaymentStatus:
    @pytest.mark.asyncio
    async def test_confirm_first_payment(self, app):
        app.collection.find_one = AsyncMock(
            return_value={
                "full_name": "Test",
                "payment_status": "pending",
            }
        )
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.update_payment_status(
            user_id=123,
            event_id="aabbccddeeff00112233aabb",
            status="confirmed",
            payment_amount=2000,
            admin_id=999,
            admin_username="admin",
        )
        app.collection.update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_additional_payment(self, app):
        app.collection.find_one = AsyncMock(
            return_value={
                "full_name": "Test",
                "payment_status": "pending",
                "payment_amount": 1000,
                "payment_history": [{"amount": 1000}],
            }
        )
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.update_payment_status(
            user_id=123,
            event_id="aabbccddeeff00112233aabb",
            status="confirmed",
            payment_amount=500,
        )
        update_call = app.collection.update_one.call_args[0][1]["$set"]
        assert update_call["payment_amount"] == 1500

    @pytest.mark.asyncio
    async def test_with_admin_comment(self, app):
        app.collection.find_one = AsyncMock(return_value=None)
        app.collection.update_one = AsyncMock()
        app.event_logs.insert_one = AsyncMock()

        await app.update_payment_status(
            user_id=123,
            event_id="aabbccddeeff00112233aabb",
            status="declined",
            admin_comment="Скриншот нечитаемый",
        )
        update_call = app.collection.update_one.call_args[0][1]["$set"]
        assert update_call["admin_comment"] == "Скриншот нечитаемый"


class TestSaveEventLog:
    @pytest.mark.asyncio
    async def test_basic_log(self, app):
        app.event_logs.insert_one = AsyncMock()
        await app.save_event_log("test_event", {"key": "value"})
        app.event_logs.insert_one.assert_called_once()
        log_entry = app.event_logs.insert_one.call_args[0][0]
        assert log_entry["event_type"] == "test_event"

    @pytest.mark.asyncio
    async def test_log_with_user(self, app):
        app.event_logs.insert_one = AsyncMock()
        await app.save_event_log("test", {"data": 1}, user_id=123, username="u")
        log_entry = app.event_logs.insert_one.call_args[0][0]
        assert log_entry["user_id"] == 123
        assert log_entry["username"] == "u"


class TestSaveFeedback:
    @pytest.mark.asyncio
    async def test_save_dict(self, app):
        app.collection.find_one = AsyncMock(return_value={"full_name": "Иванов Иван"})
        app.event_logs.insert_one = AsyncMock()

        with patch("src.app.get_database") as mock_db:
            mock_feedback_col = AsyncMock()
            mock_feedback_col.insert_one = AsyncMock(
                return_value=MagicMock(inserted_id="fb123")
            )
            mock_db.return_value.get_collection.return_value = mock_feedback_col
            result = await app.save_feedback({"user_id": 123, "attended": True})
            assert result == "fb123"

    @pytest.mark.asyncio
    async def test_save_model(self, app):
        app.collection.find_one = AsyncMock(return_value=None)
        app.event_logs.insert_one = AsyncMock()

        with patch("src.app.get_database") as mock_db:
            mock_feedback_col = AsyncMock()
            mock_feedback_col.insert_one = AsyncMock(
                return_value=MagicMock(inserted_id="fb456")
            )
            mock_db.return_value.get_collection.return_value = mock_feedback_col
            fb = FeedbackData(user_id=123, full_name="Test", attended=False)
            result = await app.save_feedback(fb)
            assert result == "fb456"


class TestNormalizeGraduateTypes:
    @pytest.mark.asyncio
    async def test_normalize(self, app):
        app.collection.update_many = AsyncMock(return_value=MagicMock(modified_count=3))
        app.event_logs.insert_one = AsyncMock()
        result = await app.normalize_graduate_types(
            admin_id=999, admin_username="admin"
        )
        assert result == 3


class TestGetUsersBase:
    @pytest.mark.asyncio
    async def test_all_users(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[{"user_id": 1}])
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_all_users()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_paid_users(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_paid_users(event_id="abc")
        assert result == []

    @pytest.mark.asyncio
    async def test_unpaid_users(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.get_unpaid_users(event_id="aabbccddeeff00112233aabb")
        assert result == []

    @pytest.mark.asyncio
    async def test_filter_by_event_id(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.collection.find = MagicMock(return_value=mock_cursor)

        await app.get_all_users(event_id="aabbccddeeff00112233aabb")
        query = app.collection.find.call_args[0][0]
        assert "$and" in query


class TestFixDatabase:
    @pytest.mark.asyncio
    async def test_fix_with_changes(self, app):
        from bson import ObjectId

        # Mock events: one free event, one paid event with free_for_types
        mock_events = [
            {"_id": ObjectId(), "pricing_type": "free", "free_for_types": []},
            {
                "_id": ObjectId(),
                "pricing_type": "formula",
                "free_for_types": ["TEACHER", "ORGANIZER"],
            },
        ]
        mock_events_cursor = MagicMock()
        mock_events_cursor.sort = MagicMock(return_value=mock_events_cursor)
        mock_events_cursor.to_list = AsyncMock(return_value=mock_events)
        app.events_col.find = MagicMock(return_value=mock_events_cursor)

        app.collection.update_many = AsyncMock(return_value=MagicMock(modified_count=2))
        app.event_logs.insert_one = AsyncMock()

        result = await app._fix_database()
        # 2 from free event + 2 from free_for_types on paid event
        assert result["total_fixed"] == 4
        app.event_logs.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_fix_no_changes(self, app):
        # Mock events: no free events, no free_for_types
        mock_events_cursor = MagicMock()
        mock_events_cursor.sort = MagicMock(return_value=mock_events_cursor)
        mock_events_cursor.to_list = AsyncMock(return_value=[])
        app.events_col.find = MagicMock(return_value=mock_events_cursor)

        app.collection.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        app.event_logs.insert_one = AsyncMock()

        result = await app._fix_database()
        assert result["total_fixed"] == 0
        app.event_logs.insert_one.assert_not_called()


class TestMoveUserToDeleted:
    @pytest.mark.asyncio
    async def test_move_with_event_id(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {
                    "user_id": 123,
                    "target_city": "Москва",
                    "event_id": "aabbccddeeff00112233aabb",
                }
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)
        app.deleted_users.insert_one = AsyncMock()
        app.collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

        result = await app.move_user_to_deleted(
            123, event_id="aabbccddeeff00112233aabb"
        )
        assert result is True
        app.deleted_users.insert_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_move_multiple(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"user_id": 123, "target_city": "Москва"},
                {"user_id": 123, "target_city": "Пермь"},
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)
        app.deleted_users.insert_many = AsyncMock()
        app.collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))

        result = await app.move_user_to_deleted(123)
        assert result is True
        app.deleted_users.insert_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_move_not_found(self, app):
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.collection.find = MagicMock(return_value=mock_cursor)

        result = await app.move_user_to_deleted(123)
        assert result is False


class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_no_fixes(self, app):
        """Test startup when _fix_database returns total_fixed == 0."""
        # _update_event_statuses
        app.events_col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        # _fix_database internals
        mock_events_cursor = MagicMock()
        mock_events_cursor.sort = MagicMock(return_value=mock_events_cursor)
        mock_events_cursor.to_list = AsyncMock(return_value=[])
        app.events_col.find = MagicMock(return_value=mock_events_cursor)
        app.collection.update_many = AsyncMock(return_value=MagicMock(modified_count=0))

        with patch("src.migrations.run_migrations", AsyncMock(return_value=None)):
            await app.startup()

    @pytest.mark.asyncio
    async def test_startup_with_fixes(self, app):
        """Test startup when _fix_database returns total_fixed > 0 (exercises lines 166-172)."""
        from bson import ObjectId

        mock_events = [
            {"_id": ObjectId(), "pricing_type": "free", "free_for_types": []},
        ]
        mock_events_cursor = MagicMock()
        mock_events_cursor.sort = MagicMock(return_value=mock_events_cursor)
        mock_events_cursor.to_list = AsyncMock(return_value=mock_events)
        app.events_col.find = MagicMock(return_value=mock_events_cursor)
        app.events_col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        app.collection.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
        app.event_logs.insert_one = AsyncMock()

        with patch("src.migrations.run_migrations", AsyncMock(return_value=None)):
            await app.startup()


class TestUpdateEventStatuses:
    @pytest.mark.asyncio
    async def test_no_modified(self, app):
        app.events_col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        await app._update_event_statuses()
        # Called twice: auto-archive (>3 months) + mark passed
        assert app.events_col.update_many.call_count == 2

    @pytest.mark.asyncio
    async def test_some_modified(self, app):
        """Exercises modified_count > 0 -> logger.info."""
        app.events_col.update_many = AsyncMock(return_value=MagicMock(modified_count=3))
        await app._update_event_statuses()
        assert app.events_col.update_many.call_count == 2


class TestGetUserActiveRegistrations:
    @pytest.mark.asyncio
    async def test_filters_archived(self, app):
        """Exercises lines 394-400: archived events are excluded."""
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"user_id": 1, "event_id": "507f1f77bcf86cd799439011"},
                {"user_id": 1, "event_id": "507f1f77bcf86cd799439012"},
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)

        call_count = 0

        async def mock_find_one(query):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"city": "Москва", "status": "upcoming"}
            return {"city": "Пермь", "status": "archived"}

        app.events_col.find_one = mock_find_one

        result = await app.get_user_active_registrations(1)
        assert len(result) == 1


class TestGetAllEventsEmpty:
    @pytest.mark.asyncio
    async def test_empty_list(self, app):
        """Line 240: get_all_events with empty result."""
        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        app.events_col.find = MagicMock(return_value=mock_cursor)

        result = await app.get_all_events()
        assert result == []


class TestGetEventByCityAndDate:
    @pytest.mark.asyncio
    async def test_found(self, app):
        """Line 240: get_event_by_city_and_date."""
        from datetime import datetime

        app.events_col.find_one = AsyncMock(return_value={"city": "Москва"})
        dt = datetime(2025, 6, 15)
        result = await app.get_event_by_city_and_date("Москва", dt)
        assert result is not None
        app.events_col.find_one.assert_called_once_with({"city": "Москва", "date": dt})

    @pytest.mark.asyncio
    async def test_not_found(self, app):
        from datetime import datetime

        app.events_col.find_one = AsyncMock(return_value=None)
        result = await app.get_event_by_city_and_date("Тбилиси", datetime(2025, 1, 1))
        assert result is None


class TestLogToChatException:
    @pytest.mark.asyncio
    @patch("src.app.send_safe", side_effect=Exception("network error"))
    async def test_exception_returns_none(self, mock_send, app):
        """Lines 696-698: exception in send_safe returns None."""
        app.settings.logs_chat_id = 99999
        result = await app.log_to_chat("test message", "logs")
        assert result is None


class TestLogRegistrationCompletedBranches:
    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_teacher_status(self, mock_send, app):
        """Lines 752, 764: teacher branch in log_registration_completed."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 111
        await app.log_registration_completed(
            user_id=1,
            username="u",
            full_name="Иванов Иван",
            graduation_year=2000,
            class_letter="А",
            city="Москва",
            graduate_type=GraduateType.TEACHER.value,
        )
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][1]
        assert "Учитель" in msg
        assert "Бесплатно (учитель)" in msg

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_non_graduate_status(self, mock_send, app):
        """Line 754: non-graduate branch."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 111
        await app.log_registration_completed(
            user_id=1,
            username="u",
            full_name="Смит Джон",
            graduation_year=2000,
            class_letter="А",
            city="Москва",
            graduate_type=GraduateType.NON_GRADUATE.value,
        )
        msg = mock_send.call_args[0][1]
        assert "Не выпускник" in msg

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_organizer_status(self, mock_send, app):
        """Lines 756, 766: organizer branch."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 111
        await app.log_registration_completed(
            user_id=1,
            username="u",
            full_name="Организатор Один",
            graduation_year=2000,
            class_letter="А",
            city="Москва",
            graduate_type=GraduateType.ORGANIZER.value,
        )
        msg = mock_send.call_args[0][1]
        assert "Организатор" in msg
        assert "Бесплатно (организатор)" in msg

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_belgrade_payment(self, mock_send, app):
        """Line 768: Белград payment branch."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 111
        await app.log_registration_completed(
            user_id=1,
            username="u",
            full_name="Иванов Иван",
            graduation_year=2000,
            class_letter="А",
            city="Белград",
            graduate_type=GraduateType.GRADUATE.value,
        )
        msg = mock_send.call_args[0][1]
        assert "Белград" in msg

    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_with_guests(self, mock_send, app):
        """Lines 771-773: guests block."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 111
        guests = [{"name": "Гость А", "price": 2000}]
        await app.log_registration_completed(
            user_id=1,
            username="u",
            full_name="Иванов Иван",
            graduation_year=2000,
            class_letter="А",
            city="Москва",
            graduate_type=GraduateType.GRADUATE.value,
            guests=guests,
        )
        msg = mock_send.call_args[0][1]
        assert "Гости" in msg
        assert "Гость А" in msg


class TestLogRegistrationCanceledNoCity:
    @pytest.mark.asyncio
    @patch("src.app.send_safe")
    async def test_no_city(self, mock_send, app):
        """Line 799: no city -> 'Все города'."""
        mock_send.return_value = MagicMock()
        app.settings.events_chat_id = 222
        await app.log_registration_canceled(
            user_id=1, username="u", full_name="Иванов Иван", city=None
        )
        msg = mock_send.call_args[0][1]
        assert "Все города" in msg


class TestGetUsersWithAndWithoutFeedback:
    @pytest.mark.asyncio
    async def test_get_users_without_feedback(self, app):
        """Lines 1039-1046: get_users_without_feedback."""
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"user_id": 1, "target_city": "Москва"},
                {"user_id": 2, "target_city": "Пермь"},
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)

        # user 1 has feedback, user 2 does not
        mock_fb_col = AsyncMock()

        async def find_one_fb(query):
            if query["user_id"] == 1:
                return {"user_id": 1}
            return None

        mock_fb_col.find_one = find_one_fb
        app._feedback_collection = mock_fb_col

        result = await app.get_users_without_feedback()
        assert len(result) == 1
        assert result[0]["user_id"] == 2

    @pytest.mark.asyncio
    async def test_get_users_with_feedback(self, app):
        """Lines 1052-1059: get_users_with_feedback."""
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(
            return_value=[
                {"user_id": 1, "target_city": "Москва"},
                {"user_id": 2, "target_city": "Пермь"},
            ]
        )
        app.collection.find = MagicMock(return_value=mock_cursor)

        mock_fb_col = AsyncMock()

        async def find_one_fb(query):
            if query["user_id"] == 1:
                return {"user_id": 1}
            return None

        mock_fb_col.find_one = find_one_fb
        app._feedback_collection = mock_fb_col

        result = await app.get_users_with_feedback()
        assert len(result) == 1
        assert result[0]["user_id"] == 1


class TestHasProvidedFeedbackWithEventId:
    @pytest.mark.asyncio
    async def test_with_event_id_found(self, app):
        """Lines 1283-1290: has_provided_feedback with event_id."""
        mock_fb_col = AsyncMock()
        mock_fb_col.find_one = AsyncMock(return_value={"user_id": 1, "event_id": "e1"})
        app._feedback_collection = mock_fb_col

        result = await app.has_provided_feedback(1, event_id="e1")
        assert result is True
        mock_fb_col.find_one.assert_called_once_with({"user_id": 1, "event_id": "e1"})

    @pytest.mark.asyncio
    async def test_with_event_id_not_found(self, app):
        mock_fb_col = AsyncMock()
        mock_fb_col.find_one = AsyncMock(return_value=None)
        app._feedback_collection = mock_fb_col

        result = await app.has_provided_feedback(1, event_id="e2")
        assert result is False

    @pytest.mark.asyncio
    async def test_without_event_id_initializes_collection(self, app):
        """Tests that _feedback_collection is initialized when not set."""
        if hasattr(app, "_feedback_collection"):
            del app._feedback_collection

        mock_fb_col = AsyncMock()
        mock_fb_col.find_one = AsyncMock(return_value=None)

        with patch("src.app.get_database") as mock_db:
            mock_db.return_value.get_collection.return_value = mock_fb_col
            result = await app.has_provided_feedback(99)
        assert result is False
