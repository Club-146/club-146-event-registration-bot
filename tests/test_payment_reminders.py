"""Tests for payment reminder selection (no real bot sends)."""
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.payment_reminders import (
    daily_reminder_tick,
    format_admin_preview,
    send_admin_previews,
    send_payment_reminders,
)
from src.payment_timeline import admin_preview_kinds_for_event


@pytest.mark.asyncio
async def test_send_payment_reminders_d4_marks_flag():
    event = {
        "_id": "evt1",
        "date": date(2026, 8, 1),
        "city": "Пермь",
        "status": "upcoming",
        "enabled": True,
    }
    reg = {
        "_id": "reg1",
        "user_id": 42,
        "event_id": "evt1",
        "payment_status": "not paid",
        "payment_reminder_d4_sent": False,
    }

    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[event])
    app.collection.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[reg]))
    )
    app.collection.update_one = AsyncMock()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    with (
        patch(
            "src.payment_reminders.get_control",
            new=AsyncMock(return_value={"paused": False}),
        ),
        patch(
            "src.payment_reminders.mark_auto_send_completed",
            new=AsyncMock(),
        ),
    ):
        stats = await send_payment_reminders(
            app, bot, now=datetime(2026, 7, 28, 9, 0), dry_run=False
        )
    assert stats["d4"] == 1
    assert stats["d2"] == 0
    bot.send_message.assert_awaited_once()
    app.collection.update_one.assert_awaited()
    assert "payment_reminder_d4_sent" in str(app.collection.update_one.await_args)


@pytest.mark.asyncio
async def test_send_payment_reminders_skips_already_sent():
    event = {
        "_id": "evt1",
        "date": date(2026, 8, 1),
        "city": "Пермь",
        "status": "upcoming",
        "enabled": True,
    }
    reg = {
        "_id": "reg1",
        "user_id": 42,
        "event_id": "evt1",
        "payment_status": None,
        "payment_reminder_d4_sent": True,
    }
    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[event])
    app.collection.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[reg]))
    )
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch(
        "src.payment_reminders.get_control",
        new=AsyncMock(return_value={"paused": False}),
    ):
        stats = await send_payment_reminders(
            app, bot, now=datetime(2026, 7, 28, 9, 0), dry_run=False
        )
    assert stats["skipped"] == 1
    assert stats["d4"] == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_payment_reminders_respects_pause():
    event = {
        "_id": "evt1",
        "date": date(2026, 8, 1),
        "city": "Пермь",
        "status": "upcoming",
        "enabled": True,
    }
    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[event])
    bot = MagicMock()
    bot.send_message = AsyncMock()

    with patch(
        "src.payment_reminders.get_control",
        new=AsyncMock(return_value={"paused": True}),
    ):
        stats = await send_payment_reminders(
            app, bot, now=datetime(2026, 7, 28, 9, 0)
        )
    assert stats["paused"] == 1
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_send_now_ignores_calendar_and_pause():
    event = {
        "_id": "evt1",
        "date": date(2026, 8, 1),
        "city": "Пермь",
        "status": "upcoming",
        "enabled": True,
    }
    reg = {
        "_id": "reg1",
        "user_id": 7,
        "event_id": "evt1",
        "payment_status": "not paid",
    }
    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[event])
    app.collection.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[reg]))
    )
    app.collection.update_one = AsyncMock()
    bot = MagicMock()
    bot.send_message = AsyncMock()

    # Not the reminder day (July 20), but force d4
    stats = await send_payment_reminders(
        app,
        bot,
        now=datetime(2026, 7, 20, 12, 0),
        force_event_id="evt1",
        force_kind="d4",
        respect_pause=False,
        only_due_today=False,
    )
    assert stats["d4"] == 1
    bot.send_message.assert_awaited_once()


def test_admin_preview_kinds_day_before_reminder():
    event = {"date": date(2026, 8, 1), "city": "Пермь"}
    # D-4 send day = Jul 28 → preview Jul 27
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 27, 10)) == [
        "d4"
    ]
    # D-2 send day = Jul 30 → preview Jul 29
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 29, 10)) == [
        "d2"
    ]
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 28, 10)) == []


def test_format_admin_preview_includes_text_and_recipients():
    event = {"_id": "aabbccddeeff001122334455", "city": "Пермь", "date": date(2026, 8, 1)}
    targets = [{"full_name": "Иван", "username": "ivan"}]
    text = "Hello body"
    msg = format_admin_preview(
        event, "d4", targets, text, paused=True, send_date_display="28.07.2026"
    )
    assert "Завтра" in msg
    assert "ПАУЗА" in msg
    assert "Иван" in msg
    assert "Hello body" in msg


@pytest.mark.asyncio
async def test_send_admin_previews_marks_sent():
    event = {
        "_id": "evt1",
        "date": date(2026, 8, 1),
        "city": "Пермь",
        "status": "upcoming",
        "enabled": True,
    }
    reg = {"_id": "r1", "user_id": 1, "full_name": "A", "username": "a"}
    app = MagicMock()
    app.get_all_events = AsyncMock(return_value=[event])
    app.collection.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[reg]))
    )
    app.log_to_chat = AsyncMock(return_value=MagicMock())

    with (
        patch(
            "src.payment_reminders.get_control",
            new=AsyncMock(return_value={"paused": False, "admin_preview_sent": False}),
        ),
        patch(
            "src.payment_reminders.mark_admin_preview_sent",
            new=AsyncMock(),
        ) as mark,
    ):
        stats = await send_admin_previews(
            app, now=datetime(2026, 7, 27, 9, 0), dry_run=False
        )
    assert stats["previews"] == 1
    app.log_to_chat.assert_awaited()
    mark.assert_awaited()


@pytest.mark.asyncio
async def test_daily_tick_calls_both():
    app = MagicMock()
    bot = MagicMock()
    with (
        patch(
            "src.payment_reminders.send_admin_previews",
            new=AsyncMock(return_value={"previews": 1}),
        ) as p,
        patch(
            "src.payment_reminders.send_payment_reminders",
            new=AsyncMock(return_value={"d4": 2}),
        ) as s,
    ):
        out = await daily_reminder_tick(app, bot, now=datetime(2026, 7, 27, 9))
    assert out["preview"]["previews"] == 1
    assert out["send"]["d4"] == 2
    p.assert_awaited_once()
    s.assert_awaited_once()
