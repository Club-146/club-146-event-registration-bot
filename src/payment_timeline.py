"""Meetup payment timeline: food planning (D-4) and named badge (D-2).

Deadlines are at 06:00 on the calendar day N days before the event date
(buffer: “before six in the morning”). Timezone-naive datetimes are treated
as local wall time of the stored event date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional


FOOD_DAYS_BEFORE = 4
BADGE_DAYS_BEFORE = 2
DEADLINE_HOUR = 6  # 06:00


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def event_date(event: dict) -> Optional[date]:
    return _as_date(event.get("date"))


def deadline_at(event: dict, days_before: int, hour: int = DEADLINE_HOUR) -> Optional[datetime]:
    """Instant after which the “late” rule applies (06:00 on that morning)."""
    d = event_date(event)
    if d is None:
        return None
    day = d - timedelta(days=days_before)
    return datetime.combine(day, time(hour=hour, minute=0, second=0))


def food_deadline(event: dict) -> Optional[datetime]:
    return deadline_at(event, FOOD_DAYS_BEFORE)


def badge_deadline(event: dict) -> Optional[datetime]:
    return deadline_at(event, BADGE_DAYS_BEFORE)


def format_deadline_ru(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y в %H:%M")


@dataclass(frozen=True)
class TimelineCopy:
    food_deadline: Optional[datetime]
    badge_deadline: Optional[datetime]
    food_deadline_display: str
    badge_deadline_display: str
    after_food_deadline: bool
    after_badge_deadline: bool


def timeline_for(event: dict, now: Optional[datetime] = None) -> TimelineCopy:
    now = now or datetime.now()
    food = food_deadline(event)
    badge = badge_deadline(event)
    return TimelineCopy(
        food_deadline=food,
        badge_deadline=badge,
        food_deadline_display=format_deadline_ru(food),
        badge_deadline_display=format_deadline_ru(badge),
        after_food_deadline=bool(food and now >= food),
        after_badge_deadline=bool(badge and now >= badge),
    )


def pay_later_message(event: dict, now: Optional[datetime] = None) -> str:
    """User-facing text after «Оплачу позже»."""
    t = timeline_for(event, now)
    return (
        "Хорошо! Вы можете оплатить позже — команда /pay "
        "(там же ссылка на сайт и реквизиты).\n\n"
        "⏱ Сроки (ориентир — 06:00):\n"
        f"• до <b>{t.food_deadline_display}</b> — успеваете в общий заказ еды;\n"
        f"• после этой даты — пожалуйста, <b>принесите немного еды с собой</b>: "
        "мы заказываем заранее, и при большом числе поздних оплат на месте "
        "может не хватить / придётся докупать.\n"
        f"• до <b>{t.badge_deadline_display}</b> — успеваем подготовить "
        "<b>именной бейдж</b>;\n"
        "• позже — бейдж уже не печатаем (вас всё равно ждут).\n\n"
        "После оплаты пришлите скриншот в этот чат (или нажмите «Оплатил» в /pay)."
    )


def reminder_kind_for_event(
    event: dict, now: Optional[datetime] = None
) -> Optional[str]:
    """Return ``d4`` or ``d2`` if *now* falls on that reminder calendar day.

    Reminder day = calendar day of the corresponding 06:00 deadline
    (D-4 food planning day, D-2 badge day).
    """
    now = now or datetime.now()
    food = food_deadline(event)
    badge = badge_deadline(event)
    today = now.date()
    if food and today == food.date():
        return "d4"
    if badge and today == badge.date():
        return "d2"
    return None


def reminder_message(kind: str, event: dict, city: str) -> str:
    t = timeline_for(event)
    if kind == "d4":
        return (
            f"Напоминание о встрече в {city}: до мероприятия 4 дня.\n\n"
            f"Если ещё не оплатили взнос — сейчас удобное время (/pay).\n"
            f"После <b>{t.food_deadline_display}</b> еду планируем с запасом; "
            "при поздней оплате лучше принести что-то к столу с собой.\n"
            f"Именной бейдж — если оплатите до <b>{t.badge_deadline_display}</b>."
        )
    if kind == "d2":
        return (
            f"Напоминание о встрече в {city}: осталось 2 дня.\n\n"
            f"<b>Последний срок для именного бейджа</b> — "
            f"<b>{t.badge_deadline_display}</b>.\n"
            "Оплатить: /pay. После оплаты пришлите скриншот в чат."
        )
    raise ValueError(f"unknown reminder kind: {kind}")


VOLUNTEER_OPTIONS_TEXT = (
    "Если хотите помочь вместо взноса или в дополнение — напишите "
    "организатору <b>@mariikors</b>. Мария решает индивидуально "
    "(скидка / бесплатный вход / волонтёрство).\n\n"
    "Примеры задач:\n"
    "• проверка бейджей на входе\n"
    "• помощь с готовкой / уборкой / орг. делами\n"
    "• фото, видео, stories\n"
    "• активности, музыка, программа"
)


def too_expensive_cancel_message() -> str:
    return (
        "Понимаем. Регистрацию отменили.\n\n"
        "Если передумаете — /start (данные подставим, если найдём прошлую анкету).\n\n"
        f"{VOLUNTEER_OPTIONS_TEXT}"
    )
