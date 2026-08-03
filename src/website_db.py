"""Read event data straight from the website's PostgreSQL.

Why this exists
---------------
The site and the bot each kept their own copy of an event. Nothing synced them
and the bot's admin let you edit the place, so on 28.07.2026 the site advertised
ул. Встречная while the bot told registrants ул. Самаркандская. Two stores, two
write paths, no reconciliation — divergence was the default outcome, not an
accident.

The fix is not a sync job. A sync job copies, and anything copied can be stale;
it also needs a conflict rule the moment both sides can write. Instead the bot
*reads through* to the site and never persists what it reads, so there is no
second copy that can drift. Mongo keeps only what the bot genuinely owns:
pricing, registration open/closed, templates, and the registrations themselves.

Access is a dedicated least-privilege role (``club146_bot_ro``) with SELECT on
three tables and nothing else — see 146.school ``scripts/db/``. In particular it
cannot read ``people`` or ``donations``.

Failure policy
--------------
Fails **closed to Mongo**: any error — unreachable database, missing row,
malformed value — leaves the Mongo document untouched and logs. That is the
opposite of ``resolve_event_pricing``, which deliberately has no fallback, and
the difference is intentional: charging a stale *price* is a real harm, whereas
showing a slightly stale *address* while the database is down is strictly better
than showing the user an error. Availability wins here; correctness wins there.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Mapping, Optional

from loguru import logger

from src.date_display import make_date_display, make_time_display

# Columns the bot reads. Explicit rather than SELECT *: the grant is narrow on
# purpose, and naming the columns means a schema change surfaces here as a clear
# error instead of quietly widening what the bot pulls.
_EVENT_QUERY = """
    SELECT uid, title, starts_at, venue, address, url, published
      FROM events
     WHERE uid = $1
       AND published = true
     LIMIT 1
"""

# Events change rarely; bot menus hit these getters on nearly every keystroke.
# Without a cache a single user browsing the menu would open a Postgres round
# trip per tap.
_CACHE_TTL_SECONDS = 60.0


def website_db_requested(settings: Any) -> bool:
    """True only for an explicit literal enable flag."""
    return getattr(settings, "website_db_enabled", False) is True


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def merge_website_event(event: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    """Overlay the website's calendar fields onto a copy of the Mongo event.

    The overlaid keys keep the Mongo document's own names, so every existing
    consumer — the event menu, /status, the CRM mailer, ticket cards — keeps
    reading the same keys off the same dict and cannot end up reading two
    different sources. Authority moves, call sites do not. (Same shape as
    ``merge_remote_pricing`` in ``website_event_bridge``.)

    Returns a **copy**. Nothing here is ever written back to Mongo — that is
    what makes a second source of truth impossible rather than merely
    discouraged.
    """
    resolved = dict(event)

    title = _clean(row.get("title"))
    if title:
        resolved["name"] = title

    # venue is optional on the website by decision; address carries the rest.
    # Assign both together — taking one from the site and leaving the other from
    # Mongo would render a place that exists in neither system.
    venue = _clean(row.get("venue"))
    address = _clean(row.get("address"))
    if venue or address:
        resolved["venue"] = venue or None
        resolved["address"] = address or None

    starts_at = row.get("starts_at")
    if isinstance(starts_at, datetime):
        # Both sides store naive Perm local time (UTC+5, no DST), so this is a
        # straight assignment. date_display is derived rather than copied: it is
        # the string users actually read, and leaving it stale while `date`
        # moved would recreate the very divergence this module removes.
        resolved["date"] = starts_at
        resolved["date_display"] = make_date_display(starts_at)
        if not _clean(event.get("time_display")):
            resolved["time_display"] = make_time_display(starts_at)

    url = _clean(row.get("url"))
    if url:
        resolved["website_url"] = url

    return resolved


class WebsiteEventReader:
    """Lazily-connected, cached, fail-closed reader for the website's events."""

    def __init__(self, settings: Any):
        self.settings = settings
        self._pool = None
        self._cache: dict[str, tuple[float, Optional[dict]]] = {}

    def enabled(self) -> bool:
        return website_db_requested(self.settings) and bool(self._dsn())

    def _dsn(self) -> str:
        raw = getattr(self.settings, "website_database_url", None)
        if raw is None:
            return ""
        # SecretStr or plain str, depending on how it was configured.
        return _clean(
            raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw
        )

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg  # imported lazily so the bot runs without the driver

            timeout = float(getattr(self.settings, "website_db_timeout_seconds", 5.0))
            self._pool = await asyncpg.create_pool(
                self._dsn(),
                min_size=1,
                max_size=int(getattr(self.settings, "website_db_pool_size", 3)),
                command_timeout=timeout,
                timeout=timeout,
            )
        return self._pool

    async def get_event_by_uid(self, uid: str) -> Optional[dict]:
        """Return the website's row for `uid`, or None. Never raises."""
        uid = _clean(uid)
        if not uid or not self.enabled():
            return None

        cached = self._cache.get(uid)
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        try:
            pool = await self._get_pool()
            record = await pool.fetchrow(_EVENT_QUERY, uid)
        except Exception as exc:  # noqa: BLE001 — fail closed, never surface
            # Deliberately not re-raised: the caller falls back to Mongo, and a
            # stale address beats an error message in a registration flow.
            logger.warning(f"website_db: read failed for {uid!r}, using Mongo: {exc}")
            # Keep serving the last good value if we have one, rather than
            # flapping between website and Mongo on an intermittent database.
            return cached[1] if cached is not None else None

        row = dict(record) if record is not None else None
        self._cache[uid] = (time.monotonic(), row)
        return row

    async def resolve_event(self, event: Optional[dict]) -> Optional[dict]:
        """Return `event` with website calendar fields applied, or unchanged."""
        if not event or not self.enabled():
            return event
        uid = _clean(event.get("website_event_uid"))
        if not uid:
            # Not linked to a website event — Mongo is its only possible source
            # and that is not a divergence.
            return event
        row = await self.get_event_by_uid(uid)
        if row is None:
            return event
        return merge_website_event(event, row)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
