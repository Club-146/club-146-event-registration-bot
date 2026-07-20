"""Send D-4 / D-2 payment reminders to unpaid registrants."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from loguru import logger

from src.payment_timeline import reminder_kind_for_event, reminder_message


async def iter_unpaid_for_reminders(app, event: dict) -> list[dict]:
    event_id = str(event["_id"])
    cursor = app.collection.find(
        {
            "event_id": event_id,
            "payment_status": {"$nin": ["confirmed", "pending"]},
        }
    )
    return await cursor.to_list(length=None)


async def send_payment_reminders(
    app,
    bot,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan upcoming events; send at most one D-4 and one D-2 per registration.

    Marks ``payment_reminder_d4_sent`` / ``payment_reminder_d2_sent`` on the reg.
    """
    now = now or datetime.now()
    stats = {"events": 0, "d4": 0, "d2": 0, "errors": 0, "skipped": 0}

    events = await app.get_all_events()
    for event in events:
        status = event.get("status")
        if status in ("archived", "passed"):
            continue
        if not event.get("enabled", True) and status != "upcoming":
            # still allow registration_closed if people need to pay
            if status not in ("upcoming", "registration_closed"):
                continue

        kind = reminder_kind_for_event(event, now)
        if not kind:
            continue

        stats["events"] += 1
        city = event.get("city", "городе")
        text = reminder_message(kind, event, city)
        flag = (
            "payment_reminder_d4_sent" if kind == "d4" else "payment_reminder_d2_sent"
        )

        unpaid = await iter_unpaid_for_reminders(app, event)
        for reg in unpaid:
            if reg.get(flag):
                stats["skipped"] += 1
                continue
            user_id = reg.get("user_id")
            if not user_id:
                stats["skipped"] += 1
                continue
            if dry_run:
                stats[kind] += 1
                continue
            try:
                await bot.send_message(int(user_id), text)
                await app.collection.update_one(
                    {"_id": reg["_id"]},
                    {"$set": {flag: True, f"{flag}_at": now.isoformat()}},
                )
                stats[kind] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.warning(
                    f"payment reminder {kind} failed for user {user_id}: {e}"
                )

    return stats
