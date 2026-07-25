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
        ("/start email__event_1_aug_26_invite_1", "email__event_1_aug_26_invite_1"),
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
        ("email__event_1_aug_26_invite_1", "email__event_1_aug_26_invite_1"),
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


@pytest.mark.parametrize(
    ("raw", "utm_source", "utm_campaign", "utm_content"),
    [
        ("email__event_1_aug_26_invite_1", "email", "event_1_aug_26_invite_1", None),
        ("group_chat", "group_chat", None, None),
        ("email__invite__v2", "email", "invite", "v2"),
        ("tg__partner_ivan__story_a", "tg", "partner_ivan", "story_a"),
    ],
)
def test_parse_start_attribution(raw, utm_source, utm_campaign, utm_content):
    attrs = App.parse_start_attribution(raw)
    assert attrs is not None
    assert attrs["raw"] == raw
    assert attrs["utm_source"] == utm_source
    assert attrs["utm_campaign"] == utm_campaign
    assert attrs["utm_content"] == utm_content


@pytest.mark.asyncio
async def test_record_start_source_first_and_last():
    app = App.__new__(App)
    sources = MagicMock()
    sources.find_one = AsyncMock(return_value=None)
    sources.insert_one = AsyncMock()
    sources.update_one = AsyncMock()
    app._user_sources = sources
    app.save_event_log = AsyncMock()

    first = await App.record_start_source(
        app, 42, "email__event_1_aug_26_invite_1", username="maria"
    )
    assert first == "email__event_1_aug_26_invite_1"
    sources.insert_one.assert_awaited_once()
    inserted = sources.insert_one.await_args.args[0]
    assert inserted["first_source"] == "email__event_1_aug_26_invite_1"
    assert inserted["first_utm_source"] == "email"
    assert inserted["first_utm_campaign"] == "event_1_aug_26_invite_1"
    assert inserted["last_utm_source"] == "email"
    assert inserted["last_utm_campaign"] == "event_1_aug_26_invite_1"
    assert inserted["click_count"] == 1
    assert inserted["user_id"] == 42

    sources.find_one = AsyncMock(
        return_value={
            "user_id": 42,
            "first_source": "email__event_1_aug_26_invite_1",
            "first_utm_source": "email",
            "first_utm_campaign": "event_1_aug_26_invite_1",
            "last_source": "email__event_1_aug_26_invite_1",
        }
    )
    second = await App.record_start_source(app, 42, "group_chat", username="maria")
    assert second == "group_chat"
    sources.update_one.assert_awaited_once()
    update = sources.update_one.await_args.args[1]
    assert update["$set"]["last_source"] == "group_chat"
    assert update["$set"]["last_utm_source"] == "group_chat"
    assert update["$set"]["last_utm_campaign"] is None
    assert "first_source" not in update["$set"]
    assert "first_utm_source" not in update["$set"]
    assert update["$inc"]["click_count"] == 1
    hist = update["$push"]["history"]["$each"][0]
    assert hist["source"] == "group_chat"
    assert hist["utm_source"] == "group_chat"


@pytest.mark.asyncio
async def test_before_tracking_user_keeps_first_on_campaign_click():
    app = App.__new__(App)
    sources = MagicMock()
    sources.find_one = AsyncMock(
        return_value={
            "user_id": 7,
            "first_source": App.BEFORE_TRACKING_SOURCE,
            "last_source": App.BEFORE_TRACKING_SOURCE,
            "history": [],
            "click_count": 0,
        }
    )
    sources.update_one = AsyncMock()
    sources.insert_one = AsyncMock()
    app._user_sources = sources
    app.save_event_log = AsyncMock()

    result = await App.record_start_source(app, 7, "email_campaign", username="old")
    assert result == "email_campaign"
    sources.insert_one.assert_not_called()
    update = sources.update_one.await_args.args[1]
    assert update["$set"]["last_source"] == "email_campaign"
    assert "first_source" not in update["$set"]
    log = app.save_event_log.await_args
    assert log.args[0] == "start_source"
    assert log.args[1]["first_source"] == App.BEFORE_TRACKING_SOURCE
    assert log.args[1]["is_first"] is False


@pytest.mark.asyncio
async def test_direct_start_without_click_does_not_inflate_history():
    app = App.__new__(App)
    sources = MagicMock()
    sources.find_one = AsyncMock(return_value=None)
    sources.insert_one = AsyncMock()
    app._user_sources = sources
    app.save_event_log = AsyncMock()

    await App.record_start_source(
        app, 9, App.DIRECT_SOURCE, username="x", count_as_click=False
    )
    doc = sources.insert_one.await_args.args[0]
    assert doc["first_source"] == "direct"
    assert doc["history"] == []
    assert doc["click_count"] == 0
    app.save_event_log.assert_not_called()


@pytest.mark.asyncio
async def test_get_source_attribution_stats_shape():
    app = App.__new__(App)
    sources = MagicMock()
    sources.count_documents = AsyncMock(return_value=3)

    def aggregate(pipeline):
        cursor = MagicMock()
        pipeline_str = str(pipeline)
        if (
            "utm_source" in pipeline_str
            and "utm_campaign" in pipeline_str
            and "$unwind" in pipeline_str
        ):
            cursor.to_list = AsyncMock(
                return_value=[
                    {
                        "_id": {
                            "utm_source": "email",
                            "utm_campaign": "event_1_aug_26_invite_1",
                        },
                        "clicks": 5,
                    }
                ]
            )
        elif "history" in pipeline_str and "$unwind" in pipeline_str:
            cursor.to_list = AsyncMock(return_value=[{"_id": "email", "clicks": 5}])
        elif "click_count" in pipeline_str:
            cursor.to_list = AsyncMock(return_value=[{"_id": None, "clicks": 5}])
        else:
            cursor.to_list = AsyncMock(
                return_value=[{"_id": "before_tracking", "count": 2}]
            )
        return cursor

    sources.aggregate = MagicMock(side_effect=aggregate)
    app._user_sources = sources

    logs = MagicMock()
    logs.find = MagicMock(
        return_value=MagicMock(
            sort=MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        )
    )
    app._event_logs = logs

    stats = await App.get_source_attribution_stats(app)
    assert stats["total_users"] == 3
    assert stats["total_clicks"] == 5
    assert stats["clicks_by_pair"][0]["_id"]["utm_source"] == "email"
    assert (
        stats["clicks_by_pair"][0]["_id"]["utm_campaign"] == "event_1_aug_26_invite_1"
    )


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
