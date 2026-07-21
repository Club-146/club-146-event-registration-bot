"""Tests for Telegram deep-link start payload attribution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app import App
from src.router import extract_start_payload


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", None),
        ("/start email_campaign", "email_campaign"),
        ("/start@register_146_meetup_2025_bot email_campaign", "email_campaign"),
        ("/start group_chat", "group_chat"),
        ("/start partner-ivan_01", "partner-ivan_01"),
        ("/start bad payload spaces", None),
        ("/start " + ("x" * 65), None),
        ("hello", None),
        ("", None),
    ],
)
def test_extract_start_payload(text, expected):
    message = MagicMock()
    message.text = text
    assert extract_start_payload(message) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("email_campaign", "email_campaign"),
        ("  dm  ", "dm"),
        ("bad space", None),
        ("", None),
        (None, None),
        ("x" * 64, "x" * 64),
        ("x" * 65, None),
        ("has.dot", None),
    ],
)
def test_normalize_start_payload(raw, expected):
    assert App.normalize_start_payload(raw) == expected


@pytest.mark.asyncio
async def test_record_start_source_first_and_last():
    app = App.__new__(App)
    sources = MagicMock()
    sources.find_one = AsyncMock(return_value=None)
    sources.insert_one = AsyncMock()
    sources.update_one = AsyncMock()
    app._user_sources = sources
    app.save_event_log = AsyncMock()

    first = await App.record_start_source(app, 42, "email_campaign", username="maria")
    assert first == "email_campaign"
    sources.insert_one.assert_awaited_once()
    inserted = sources.insert_one.await_args.args[0]
    assert inserted["first_source"] == "email_campaign"
    assert inserted["last_source"] == "email_campaign"
    assert inserted["user_id"] == 42

    sources.find_one = AsyncMock(
        return_value={
            "user_id": 42,
            "first_source": "email_campaign",
            "last_source": "email_campaign",
        }
    )
    second = await App.record_start_source(app, 42, "group_chat", username="maria")
    assert second == "group_chat"
    sources.update_one.assert_awaited_once()
    update = sources.update_one.await_args.args[1]
    assert update["$set"]["last_source"] == "group_chat"
    assert update["$set"]["first_source"] if False else True  # first not in $set
    assert "first_source" not in update["$set"]


@pytest.mark.asyncio
async def test_get_all_time_broadcast_users_prefers_active_over_deleted():
    app = App.__new__(App)
    active = [
        {"user_id": 1, "full_name": "Active One", "event_id": "e1"},
        {"user_id": 2, "full_name": "Active Two", "event_id": "e1"},
    ]
    deleted = [
        {"user_id": 1, "full_name": "Deleted One", "event_id": "old"},
        {"user_id": 3, "full_name": "Deleted Three", "event_id": "old"},
    ]

    active_cursor = MagicMock()
    active_cursor.to_list = AsyncMock(return_value=active)
    deleted_cursor = MagicMock()
    deleted_cursor.to_list = AsyncMock(return_value=deleted)

    app._collection = MagicMock()
    app._collection.find = MagicMock(return_value=active_cursor)
    app._deleted_users = MagicMock()
    app._deleted_users.find = MagicMock(return_value=deleted_cursor)

    users = await App.get_all_time_broadcast_users(app)
    by_id = {u["user_id"]: u["full_name"] for u in users}
    assert by_id[1] == "Active One"
    assert by_id[2] == "Active Two"
    assert by_id[3] == "Deleted Three"
    assert len(users) == 3
