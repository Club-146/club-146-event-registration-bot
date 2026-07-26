"""Liveness heartbeat to Healthchecks.

A Telegram bot has no HTTP surface — nothing outside can probe it — so the only
honest way to know it is alive is for the bot to say so from inside its own
event loop.

The tick deliberately does real work before pinging:

  * ``bot.get_me()``   — the bot can still reach Telegram with a valid token.
  * ``ping`` on Mongo  — the database answers.

Pinging on a bare timer would be worse than nothing: an event loop that is up
while Telegram polling is broken would keep the check green forever. If either
call fails we send ``/fail`` instead, so the alert fires immediately rather than
waiting for the grace period to lapse.

Configuration (Coolify env vars — nothing is written to disk):

    HEALTHCHECKS_BASE_URL=https://healthchecks.calmmage.com
    HEALTHCHECKS_PING_KEY=...
    HEARTBEAT_SLUG=club146-bot-prod      # optional, this is the default
    HEARTBEAT_INTERVAL_MINUTES=5         # optional

With no key configured the module stays dormant and logs once — a bot without
monitoring must still boot.
"""

from __future__ import annotations

import os

from loguru import logger

_scheduler = None

DEFAULT_SLUG = "club146-bot-prod"
DEFAULT_INTERVAL_MINUTES = 5


def _config() -> tuple[str, str, str] | None:
    base = (os.environ.get("HEALTHCHECKS_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("HEALTHCHECKS_PING_KEY") or "").strip()
    slug = (os.environ.get("HEARTBEAT_SLUG") or DEFAULT_SLUG).strip()
    if not base or not key:
        return None
    return base, key, slug


async def _send(url: str, timeout: float = 10.0) -> None:
    """Best effort. Monitoring must never take the bot down."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            await client.get(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"heartbeat ping failed: {type(exc).__name__}: {exc}")


async def heartbeat_tick(bot) -> None:
    """One liveness check. Never raises."""
    conf = _config()
    if conf is None:
        return
    base, key, slug = conf
    url = f"{base}/ping/{key}/{slug}"

    try:
        me = await bot.get_me()
        detail = f"telegram ok (@{me.username})"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"heartbeat: telegram unreachable: {type(exc).__name__}")
        await _send(f"{url}/fail")
        return

    try:
        from botspot import get_database

        db = get_database()
        await db.command("ping")
        detail += ", mongo ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"heartbeat: database unreachable: {type(exc).__name__}")
        await _send(f"{url}/fail")
        return

    await _send(url)
    logger.debug(f"heartbeat sent: {detail}")


def start_heartbeat(bot, *, minutes: int | None = None) -> None:
    """Start a singleton heartbeat scheduler (no-op if already running)."""
    global _scheduler
    if _scheduler is not None:
        logger.info("heartbeat scheduler already running")
        return

    if _config() is None:
        logger.info(
            "heartbeat disabled: HEALTHCHECKS_BASE_URL / HEALTHCHECKS_PING_KEY not set"
        )
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.error("apscheduler not installed; heartbeat will not run")
        return

    if minutes is None:
        raw = (os.environ.get("HEARTBEAT_INTERVAL_MINUTES") or "").strip()
        try:
            minutes = int(raw) if raw else DEFAULT_INTERVAL_MINUTES
        except ValueError:
            minutes = DEFAULT_INTERVAL_MINUTES
    minutes = max(1, minutes)

    scheduler = AsyncIOScheduler()

    async def _job() -> None:
        try:
            await heartbeat_tick(bot)
        except Exception:
            logger.exception("heartbeat_tick failed")

    scheduler.add_job(
        _job,
        trigger="interval",
        minutes=minutes,
        id="healthchecks_heartbeat",
        replace_existing=True,
        # Late is better than skipped: a missed tick would look like an outage.
        misfire_grace_time=60,
    )
    scheduler.start()
    _scheduler = scheduler

    # Ping once right after boot instead of waiting out the first interval, so a
    # restart shows up immediately and a crash-loop cannot hide between ticks.
    # (Do NOT pass next_run_time=None to add_job for this — in APScheduler that
    # marks the job paused, which would silently disable the heartbeat.)
    try:
        import asyncio

        asyncio.get_running_loop().create_task(_job())
    except RuntimeError:
        logger.debug("no running loop yet; first heartbeat will come on schedule")

    _, _, slug = _config()  # type: ignore[misc]
    logger.info(f"heartbeat scheduler started (slug={slug}, every {minutes}m)")


def stop_heartbeat() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
