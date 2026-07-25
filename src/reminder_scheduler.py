"""APScheduler hook: daily payment reminder tick (admin preview + auto-send)."""

from __future__ import annotations


from loguru import logger

_scheduler = None


def start_reminder_scheduler(app, bot, *, hour: int = 9, minute: int = 0) -> None:
    """Start a singleton AsyncIOScheduler (no-op if already running)."""
    global _scheduler
    if _scheduler is not None:
        logger.info("payment reminder scheduler already running")
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.error("apscheduler not installed; payment reminders will not auto-run")
        return

    from src.payment_reminders import daily_reminder_tick

    scheduler = AsyncIOScheduler()

    async def _job():
        try:
            await daily_reminder_tick(app, bot)
        except Exception:
            logger.exception("daily_reminder_tick failed")

    scheduler.add_job(
        _job,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="payment_reminders_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # Hourly safety net: same flags make it idempotent.
    scheduler.add_job(
        _job,
        trigger="cron",
        minute=15,
        id="payment_reminders_hourly",
        replace_existing=True,
        misfire_grace_time=600,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        f"payment reminder scheduler started (daily {hour:02d}:{minute:02d} + hourly :15)"
    )


def stop_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
