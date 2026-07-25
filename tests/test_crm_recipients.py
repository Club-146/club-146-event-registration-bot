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


@pytest.mark.asyncio
async def test_all_time_city_audience_reaches_historical_rows():
    """Perm recovery: history rows whose target_city embeds the city name
    ("Пермь (Летняя встреча 2025)") or that only link via an archived Perm
    event must be included, from both active and deleted collections."""
    app = MagicMock()
    app.get_all_events = AsyncMock(
        return_value=[
            {"_id": "perm-2026", "city": "Пермь"},
            {"_id": "perm-summer-2025", "city": "Пермь"},
            {"_id": "moscow-2026", "city": "Москва"},
        ]
    )
    captured = {}

    async def collection_distinct(field, query):
        captured["active"] = query
        return [1, 2]

    async def deleted_distinct(field, query):
        captured["deleted"] = query
        return [2, 3]

    app.collection.distinct = AsyncMock(side_effect=collection_distinct)
    app.deleted_users.distinct = AsyncMock(side_effect=deleted_distinct)
    event_map = {"perm-2026": {"city": "Пермь"}}

    recipients = await _build_recipient_list(app, "all_time", "perm-2026", event_map)

    assert sorted(recipients) == [1, 2, 3]
    for query in (captured["active"], captured["deleted"]):
        event_clause, city_clause = query["$or"]
        assert set(event_clause["event_id"]["$in"]) == {
            "perm-2026",
            "perm-summer-2025",
        }
        assert city_clause["target_city"]["$regex"] == "^Пермь"


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

    recipients, _ = await _get_notify_users(app, audience, None, scope="current")

    assert recipients == [{"user_id": 1}]
    get_users.assert_awaited_once_with(event_id=None, active_only=True)


@pytest.mark.asyncio
async def test_notify_all_time_scope_uses_full_historical_base():
    app = MagicMock()
    app.get_all_events = AsyncMock(
        return_value=[
            {"_id": "perm-2026", "city": "Пермь"},
            {"_id": "perm-old", "city": "Пермь"},
        ]
    )
    app.get_all_time_broadcast_users = AsyncMock(
        return_value=[{"user_id": 1}, {"user_id": 2}, {"user_id": 3}]
    )
    event_map = {"perm-2026": {"city": "Пермь"}}

    recipients, name = await _get_notify_users(
        app, "all", "perm-2026", scope="all_time", event_map=event_map
    )

    assert len(recipients) == 3
    assert "всей базы" in name
    query = app.get_all_time_broadcast_users.await_args.args[0]
    event_clause, city_clause = query["$or"]
    assert set(event_clause["event_id"]["$in"]) == {"perm-2026", "perm-old"}
    assert city_clause["target_city"]["$regex"] == "^Пермь"


@pytest.mark.asyncio
async def test_notify_all_time_all_cities_uses_empty_history_query():
    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[])
    app.get_all_time_broadcast_users = AsyncMock(return_value=[{"user_id": 9}])

    recipients, _ = await _get_notify_users(
        app, "all", None, scope="all_time", event_map={}
    )

    assert recipients == [{"user_id": 9}]
    app.get_all_time_broadcast_users.assert_awaited_once_with({})


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
