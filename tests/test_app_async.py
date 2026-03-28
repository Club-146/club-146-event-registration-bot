"""Tests for async App methods with mocked database."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.app import App, RegisteredUser, FeedbackData


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
