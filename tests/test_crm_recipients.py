"""Focused tests for CRM broadcast recipient selection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app import App
from src.routers.crm import _build_recipient_list, _get_notify_users


def _registration_find(registrations):
    """Return a query-aware mock for the event filters used by _get_users_base."""

    def find(query):
        event_ids = None
        for condition in query.get("$and", []):
            event_filter = condition.get("event_id")
            if isinstance(event_filter, dict) and "$in" in event_filter:
                event_ids = set(event_filter["$in"])
            elif isinstance(event_filter, (str, int)):
                event_ids = {event_filter}

        assert event_ids is not None
        matching = [
            registration
            for registration in registrations
            if registration.get("event_id") in event_ids
        ]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=matching)
        return cursor

    return MagicMock(side_effect=find)


@pytest.mark.asyncio
async def test_current_audience_excludes_old_rows_from_old_and_current_users():
    old_registration = {"_id": "old-1", "user_id": 1, "event_id": "old-event"}
    current_registration = {
        "_id": "current-1",
        "user_id": 1,
        "event_id": "current-event",
    }
    old_only_registration = {
        "_id": "old-2",
        "user_id": 2,
        "event_id": "old-event",
    }
    app = MagicMock()
    app.get_active_events = AsyncMock(return_value=[{"_id": "current-event"}])
    app.collection.find = _registration_find(
        [old_registration, current_registration, old_only_registration]
    )

    recipients = await App._get_users_base(app, active_only=True)

    assert recipients == [current_registration]


@pytest.mark.asyncio
async def test_current_audience_sends_multi_event_user_only_once():
    first_registration = {"_id": "current-1", "user_id": 1, "event_id": "event-a"}
    duplicate_user_registration = {
        "_id": "current-2",
        "user_id": 1,
        "event_id": "event-b",
    }
    other_user_registration = {
        "_id": "current-3",
        "user_id": 2,
        "event_id": "event-b",
    }
    app = MagicMock()
    app.get_active_events = AsyncMock(
        return_value=[{"_id": "event-a"}, {"_id": "event-b"}]
    )
    app.collection.find = _registration_find(
        [first_registration, duplicate_user_registration, other_user_registration]
    )

    recipients = await App._get_users_base(app, active_only=True)

    assert recipients == [first_registration, other_user_registration]


@pytest.mark.asyncio
async def test_current_season_announcement_queries_only_enabled_events():
    app = MagicMock()
    app.collection.distinct = AsyncMock(return_value=[1, 2])
    event_map = {"event-a": {"city": "Москва"}, "event-b": {"city": "Пермь"}}

    recipients = await _build_recipient_list(app, "current", "all", event_map)

    assert recipients == [1, 2]
    app.collection.distinct.assert_awaited_once_with(
        "user_id", {"event_id": {"$in": ["event-a", "event-b"]}}
    )


@pytest.mark.parametrize(
    ("audience", "method_name"),
    [
        ("all", "get_all_users"),
        ("paid", "get_paid_users"),
        ("unpaid", "get_unpaid_users"),
    ],
)
@pytest.mark.asyncio
async def test_notify_all_events_requests_active_recipient_scope(audience, method_name):
    app = MagicMock()
    get_users = AsyncMock(return_value=[{"user_id": 1}])
    setattr(app, method_name, get_users)

    recipients, _ = await _get_notify_users(app, audience, None)

    assert recipients == [{"user_id": 1}]
    get_users.assert_awaited_once_with(event_id=None, active_only=True)


@pytest.mark.asyncio
async def test_early_payment_report_labels_submitted_receipt_under_review():
    from src.routers.crm import notify_early_payment_handler

    message = MagicMock()
    message.chat.id = 123
    state = AsyncMock()
    app = MagicMock()
    app.get_unpaid_users = AsyncMock(
        return_value=[
            {
                "user_id": 456,
                "username": "test_user",
                "full_name": "Тест Тестов",
                "target_city": "Москва",
                "payment_status": "pending",
            }
        ]
    )
    status_message = AsyncMock()

    with (
        patch(
            "src.routers.crm.ask_user_choice",
            new=AsyncMock(return_value="dry_run"),
        ),
        patch(
            "src.routers.crm.send_safe",
            new=AsyncMock(side_effect=[status_message, None]),
        ),
    ):
        await notify_early_payment_handler(message, state, app)

    report = status_message.edit_text.await_args.args[0]
    assert "💰 На проверке" in report
    assert "Оплачу позже" not in report
