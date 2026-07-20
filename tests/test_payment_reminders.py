"""Tests for payment reminder selection (no real bot sends)."""
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.payment_reminders import send_payment_reminders


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

    stats = await send_payment_reminders(
        app, bot, now=datetime(2026, 7, 28, 9, 0), dry_run=False
    )
    assert stats["skipped"] == 1
    assert stats["d4"] == 0
    bot.send_message.assert_not_awaited()
