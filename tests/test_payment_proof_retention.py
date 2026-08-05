from unittest.mock import AsyncMock

import pytest

from src.app import AppSettings
from src.payment_proof_retention import configure_payment_proof_retention


def settings(**overrides) -> AppSettings:
    values = {
        "telegram_bot_token": "token",
        "events_chat_id": -1001,
        "payment_phone_number": "phone",
        "payment_name": "Maria",
    }
    values.update(overrides)
    return AppSettings(**values)


@pytest.mark.asyncio
async def test_retention_requires_dedicated_chat():
    bot = AsyncMock()

    assert await configure_payment_proof_retention(bot, settings()) is False
    assert (
        await configure_payment_proof_retention(
            bot, settings(payment_proofs_chat_id=-1001)
        )
        is False
    )
    bot.set_chat_message_auto_delete_time.assert_not_awaited()


@pytest.mark.asyncio
async def test_retention_sets_one_year_timer_on_dedicated_chat():
    bot = AsyncMock()

    assert (
        await configure_payment_proof_retention(
            bot, settings(payment_proofs_chat_id=-2002)
        )
        is True
    )
    bot.set_chat_message_auto_delete_time.assert_awaited_once_with(
        chat_id=-2002,
        message_auto_delete_time=31_536_000,
    )
