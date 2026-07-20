"""Payment reminders: auto schedule, admin preview (D-1), pause / send now.

Control state lives in Mongo collection ``payment_reminder_controls``
(one doc per event_id + kind). Per-user send flags stay on registrations.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger

from src.payment_timeline import (
    admin_preview_kinds_for_event,
    kind_label_ru,
    reminder_kind_for_event,
    reminder_message,
)

CONTROL_COLLECTION = "payment_reminder_controls"
KIND_FLAG = {
    "d4": "payment_reminder_d4_sent",
    "d2": "payment_reminder_d2_sent",
}


def _event_id(event: dict) -> str:
    return str(event["_id"])


def _control_id(event_id: str, kind: str) -> str:
    return f"{event_id}:{kind}"


def _get_database():
    from botspot import get_database

    return get_database()


async def controls_collection(app):
    if getattr(app, "_reminder_controls", None) is None:
        app._reminder_controls = _get_database().get_collection(CONTROL_COLLECTION)
    return app._reminder_controls


async def get_control(app, event_id: str, kind: str) -> dict:
    col = await controls_collection(app)
    doc = await col.find_one({"_id": _control_id(event_id, kind)})
    if doc:
        return doc
    return {
        "_id": _control_id(event_id, kind),
        "event_id": event_id,
        "kind": kind,
        "paused": False,
        "admin_preview_sent": False,
        "admin_preview_sent_at": None,
        "auto_send_completed": False,
        "auto_send_completed_at": None,
    }


async def set_paused(app, event_id: str, kind: str, paused: bool) -> dict:
    col = await controls_collection(app)
    now = datetime.now().isoformat()
    await col.update_one(
        {"_id": _control_id(event_id, kind)},
        {
            "$set": {
                "event_id": event_id,
                "kind": kind,
                "paused": paused,
                "paused_updated_at": now,
            },
            "$setOnInsert": {"admin_preview_sent": False, "auto_send_completed": False},
        },
        upsert=True,
    )
    return await get_control(app, event_id, kind)


async def mark_admin_preview_sent(app, event_id: str, kind: str) -> None:
    col = await controls_collection(app)
    await col.update_one(
        {"_id": _control_id(event_id, kind)},
        {
            "$set": {
                "event_id": event_id,
                "kind": kind,
                "admin_preview_sent": True,
                "admin_preview_sent_at": datetime.now().isoformat(),
            },
            "$setOnInsert": {"paused": False, "auto_send_completed": False},
        },
        upsert=True,
    )


async def mark_auto_send_completed(app, event_id: str, kind: str) -> None:
    col = await controls_collection(app)
    await col.update_one(
        {"_id": _control_id(event_id, kind)},
        {
            "$set": {
                "event_id": event_id,
                "kind": kind,
                "auto_send_completed": True,
                "auto_send_completed_at": datetime.now().isoformat(),
            },
            "$setOnInsert": {"paused": False, "admin_preview_sent": False},
        },
        upsert=True,
    )


async def iter_unpaid_for_reminders(app, event: dict) -> list[dict]:
    event_id = _event_id(event)
    cursor = app.collection.find(
        {
            "event_id": event_id,
            "payment_status": {"$nin": ["confirmed", "pending"]},
        }
    )
    return await cursor.to_list(length=None)


def _eligible_events(events: list[dict]) -> list[dict]:
    out = []
    for event in events:
        status = event.get("status")
        if status in ("archived", "passed"):
            continue
        if status in ("upcoming", "registration_closed", None) or event.get(
            "enabled", True
        ):
            out.append(event)
    return out


async def build_reminder_batch(app, event: dict, kind: str) -> tuple[list[dict], str]:
    """Return (unpaid regs not yet messaged, message text)."""
    city = event.get("city", "городе")
    text = reminder_message(kind, event, city)
    flag = KIND_FLAG[kind]
    unpaid = await iter_unpaid_for_reminders(app, event)
    targets = []
    for reg in unpaid:
        if reg.get(flag):
            continue
        if not reg.get("user_id"):
            continue
        targets.append(reg)
    return targets, text


def format_admin_preview(
    event: dict,
    kind: str,
    targets: list[dict],
    text: str,
    *,
    paused: bool,
    send_date_display: str,
) -> str:
    city = event.get("city", "?")
    label = kind_label_ru(kind)
    names = []
    for reg in targets[:40]:
        uname = f"@{reg['username']}" if reg.get("username") else "—"
        names.append(f"• {reg.get('full_name', '?')} ({uname})")
    more = ""
    if len(targets) > 40:
        more = f"\n… и ещё {len(targets) - 40}"
    pause_line = (
        "⏸ <b>ПАУЗА</b> — авто-отправка не пойдёт, пока не снимете.\n" if paused else ""
    )
    return (
        f"📋 <b>Завтра авто-напоминание</b> {label}\n"
        f"Встреча: {city} · event {_event_id(event)[:8]}…\n"
        f"Дата отправки пользователям: <b>{send_date_display}</b>\n"
        f"{pause_line}"
        f"Получателей (неоплатившие, ещё не слали): <b>{len(targets)}</b>\n"
        f"{chr(10).join(names)}{more}\n\n"
        f"——— полный текст ——-\n{text}\n\n"
        "Админ → Управление → Напоминания об оплате: "
        "пауза / снять паузу / отправить сейчас."
    )


def _kinds_for_event(
    event: dict,
    *,
    now: datetime,
    force_kind: Optional[str],
    only_due_today: bool,
) -> list[str]:
    if force_kind:
        return [force_kind]
    if only_due_today:
        k = reminder_kind_for_event(event, now)
        return [k] if k else []
    return []


async def _deliver_batch(
    app,
    bot,
    *,
    targets: list[dict],
    text: str,
    kind: str,
    now: datetime,
    dry_run: bool,
    stats: dict[str, Any],
) -> None:
    flag = KIND_FLAG[kind]
    for reg in targets:
        user_id = reg.get("user_id")
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
            logger.warning(f"payment reminder {kind} failed for user {user_id}: {e}")


async def _send_kind_for_event(
    app,
    bot,
    event: dict,
    kind: str,
    *,
    now: datetime,
    dry_run: bool,
    respect_pause: bool,
    force_kind: Optional[str],
    stats: dict[str, Any],
) -> None:
    eid = _event_id(event)
    if respect_pause:
        ctrl = await get_control(app, eid, kind)
        if ctrl.get("paused"):
            stats["paused"] += 1
            logger.info(f"reminder {kind} paused for event {eid}")
            return

    stats["events"] += 1
    targets, text = await build_reminder_batch(app, event, kind)
    if not targets:
        stats["skipped"] += 1
        return

    await _deliver_batch(
        app,
        bot,
        targets=targets,
        text=text,
        kind=kind,
        now=now,
        dry_run=dry_run,
        stats=stats,
    )
    if not dry_run and not force_kind:
        await mark_auto_send_completed(app, eid, kind)


async def send_payment_reminders(
    app,
    bot,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    force_kind: Optional[str] = None,
    force_event_id: Optional[str] = None,
    respect_pause: bool = True,
    only_due_today: bool = True,
) -> dict[str, Any]:
    """Send D-4 / D-2 payment reminders.

    *force_event_id* + *force_kind*: admin «send now» (ignores calendar day).
    *only_due_today*: when True, only kinds due on *now*'s date (unless force).
    """
    now = now or datetime.now()
    stats: dict[str, Any] = {
        "events": 0,
        "d4": 0,
        "d2": 0,
        "errors": 0,
        "skipped": 0,
        "paused": 0,
    }

    for event in _eligible_events(await app.get_all_events()):
        eid = _event_id(event)
        if force_event_id and eid != force_event_id:
            continue
        for kind in _kinds_for_event(
            event, now=now, force_kind=force_kind, only_due_today=only_due_today
        ):
            await _send_kind_for_event(
                app,
                bot,
                event,
                kind,
                now=now,
                dry_run=dry_run,
                respect_pause=respect_pause,
                force_kind=force_kind,
                stats=stats,
            )

    return stats


async def send_admin_previews(
    app,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Day before each reminder: summary + full text to events/logs chat."""
    now = now or datetime.now()
    stats = {"previews": 0, "skipped": 0, "errors": 0}
    events = _eligible_events(await app.get_all_events())

    for event in events:
        eid = _event_id(event)
        for kind in admin_preview_kinds_for_event(event, now):
            ctrl = await get_control(app, eid, kind)
            if ctrl.get("admin_preview_sent"):
                stats["skipped"] += 1
                continue
            targets, text = await build_reminder_batch(app, event, kind)
            send_date = (now.date() + timedelta(days=1)).strftime("%d.%m.%Y")
            body = format_admin_preview(
                event,
                kind,
                targets,
                text,
                paused=bool(ctrl.get("paused")),
                send_date_display=send_date,
            )
            if dry_run:
                stats["previews"] += 1
                continue
            try:
                sent = await app.log_to_chat(body, "events")
                if sent is None:
                    sent = await app.log_to_chat(body, "logs")
                if sent is None:
                    logger.warning(
                        "admin reminder preview: no events/logs chat configured"
                    )
                    stats["errors"] += 1
                    continue
                await mark_admin_preview_sent(app, eid, kind)
                stats["previews"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"admin preview failed event={eid} kind={kind}: {e}")

    return stats


async def list_upcoming_reminder_plan(
    app, *, now: Optional[datetime] = None, days_ahead: int = 14
) -> list[dict]:
    """Plan rows for admin UI: next N days of previews + sends."""
    from src.payment_timeline import badge_deadline, food_deadline

    now = now or datetime.now()
    plan = []
    events = _eligible_events(await app.get_all_events())
    for event in events:
        eid = _event_id(event)
        for kind, dl_fn in (("d4", food_deadline), ("d2", badge_deadline)):
            dl = dl_fn(event)
            if not dl:
                continue
            send_day = dl.date()
            preview_day = send_day - timedelta(days=1)
            if send_day < now.date() or send_day > now.date() + timedelta(
                days=days_ahead
            ):
                continue
            ctrl = await get_control(app, eid, kind)
            targets, text = await build_reminder_batch(app, event, kind)
            plan.append(
                {
                    "event_id": eid,
                    "city": event.get("city", "?"),
                    "kind": kind,
                    "label": kind_label_ru(kind),
                    "preview_day": preview_day.isoformat(),
                    "send_day": send_day.isoformat(),
                    "paused": bool(ctrl.get("paused")),
                    "recipient_count": len(targets),
                    "text": text,
                }
            )
    plan.sort(key=lambda r: (r["send_day"], r["kind"]))
    return plan


async def daily_reminder_tick(app, bot, *, now: Optional[datetime] = None) -> dict:
    """Scheduled job: admin previews for tomorrow + user sends due today."""
    now = now or datetime.now()
    preview_stats = await send_admin_previews(app, now=now, dry_run=False)
    send_stats = await send_payment_reminders(
        app, bot, now=now, dry_run=False, only_due_today=True, respect_pause=True
    )
    logger.info(f"payment reminder tick: previews={preview_stats} sends={send_stats}")
    return {"preview": preview_stats, "send": send_stats}
