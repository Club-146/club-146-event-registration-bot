"""Telegram-side retention for copied payment-proof media."""

from __future__ import annotations

from aiogram import Bot
from loguru import logger

from src.app import AppSettings


SECONDS_PER_DAY = 86_400


async def configure_payment_proof_retention(
    bot: Bot,
    settings: AppSettings,
) -> bool:
    """Enable chat-wide retention only on an explicitly dedicated proof chat."""
    proof_chat_id = settings.payment_proofs_chat_id
    if proof_chat_id is None:
        logger.warning(
            "Payment-proof retention is not active: PAYMENT_PROOFS_CHAT_ID is unset"
        )
        return False
    if proof_chat_id == settings.events_chat_id:
        logger.error(
            "Payment-proof retention refused: PAYMENT_PROOFS_CHAT_ID must differ "
            "from EVENTS_CHAT_ID because Telegram auto-delete is chat-wide"
        )
        return False

    retention_seconds = settings.payment_proof_retention_days * SECONDS_PER_DAY
    await bot.set_chat_message_auto_delete_time(
        chat_id=proof_chat_id,
        message_auto_delete_time=retention_seconds,
    )
    logger.info(
        "Payment-proof retention active: chat={} days={}",
        proof_chat_id,
        settings.payment_proof_retention_days,
    )
    return True
