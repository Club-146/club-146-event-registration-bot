"""APScheduler hook: daily payment reminder tick (admin preview + auto-send).

Runs on Europe/Moscow wall time so “day before cutoff” matches event deadlines
(also 06:00 local) and admins are not pinged at 03:15 because the container
is UTC.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from loguru import logger

# Meetup ops / deadline copy is Moscow-local (see payment_timeline DEADLINE_HOUR).
REMINDER_TZ = ZoneInfo("Europe/Moscow")

_scheduler = None


def start_reminder_scheduler(
    app,
    bot,
    *,
    hour: int = 9,
    minute: int = 0,
    catchup_start_hour: int = 9,
    catchup_end_hour: int = 18,
) -> None:
    """Start a singleton AsyncIOScheduler (no-op if already running).

    * Daily job at ``hour:minute`` Moscow — primary tick.
    * Hourly catch-up only during ``catchup_start_hour``–``catchup_end_hour``
      Moscow (inclusive start, exclusive end), so a missed 09:00 still retries
      in the daytime without firing at night after deploy/restart.
    """
    global _scheduler
    if _scheduler is not None:
        logger.info("payment reminder scheduler already running")
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("apscheduler not installed; payment reminders will not auto-run")
        return

    from src.payment_reminders import daily_reminder_tick

    scheduler = AsyncIOScheduler(timezone=REMINDER_TZ)

    async def _job():
        try:
            await daily_reminder_tick(app, bot)
        except Exception:
            logger.exception("daily_reminder_tick failed")

    scheduler.add_job(
        _job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=REMINDER_TZ),
        id="payment_reminders_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    # Daytime safety net only (idempotent via control/send flags).
    # hour range is exclusive on end: e.g. 9–18 → 09:15 … 17:15.
    catchup_hours = ",".join(
        str(h) for h in range(catchup_start_hour, catchup_end_hour)
    )
    if catchup_hours:
        scheduler.add_job(
            _job,
            trigger=CronTrigger(hour=catchup_hours, minute=15, timezone=REMINDER_TZ),
            id="payment_reminders_hourly",
            replace_existing=True,
            misfire_grace_time=600,
        )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        f"payment reminder scheduler started "
        f"(daily {hour:02d}:{minute:02d} Europe/Moscow + "
        f"catch-up :15 hours {catchup_start_hour}–{catchup_end_hour - 1} MSK)"
    )


def stop_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
