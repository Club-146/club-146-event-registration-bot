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

# Admin gets a summary 1 calendar day before each user-facing reminder day.
ADMIN_PREVIEW_DAYS_BEFORE_REMINDER = 1


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


def deadline_at(
    event: dict, days_before: int, hour: int = DEADLINE_HOUR
) -> Optional[datetime]:
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


def format_date_short_ru(value: Any) -> str:
    """Compact dd.mm for Telegram preview / TL;DR lines."""
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d.%m")
    if isinstance(value, date):
        return value.strftime("%d.%m")
    d = _as_date(value)
    return d.strftime("%d.%m") if d else "—"


def _as_datetime_date(value: Any) -> Optional[date]:
    return _as_date(value)


@dataclass(frozen=True)
class EarlyBirdInfo:
    discount: int
    deadline: date
    deadline_at: datetime
    deadline_display: str  # full «dd.mm.YYYY в HH:MM» (same as food)
    deadline_short: str  # dd.mm


def early_bird_deadline_at(event: dict) -> Optional[datetime]:
    """Early-bird cutoff = food planning cutoff (D-4 at 06:00).

    Only when ``early_bird_discount > 0``. Stored ``early_bird_deadline`` is not
    used for timing so pay-later / reminders / price math stay aligned.
    """
    if int(event.get("early_bird_discount") or 0) <= 0:
        return None
    return food_deadline(event)


def is_early_bird_active(event: dict, now: Optional[datetime] = None) -> bool:
    """True while now is strictly before the shared food / early-bird cutoff."""
    now = now or datetime.now()
    dl = early_bird_deadline_at(event)
    return bool(dl and now < dl)


def early_bird_info(event: dict) -> Optional[EarlyBirdInfo]:
    """Return early-bird discount + shared cutoff if discount is configured."""
    discount = int(event.get("early_bird_discount") or 0)
    dl = early_bird_deadline_at(event)
    if discount <= 0 or dl is None:
        return None
    return EarlyBirdInfo(
        discount=discount,
        deadline=dl.date(),
        deadline_at=dl,
        deadline_display=format_deadline_ru(dl),
        deadline_short=format_date_short_ru(dl),
    )


def early_bird_near_food_cutoff(
    event: dict, *, window_days: int = 2
) -> Optional[EarlyBirdInfo]:
    """Early bird for D-4 reminders: same cutoff as food (always 'near' when set).

    ``window_days`` kept for call-site compatibility; unused after alignment.
    """
    _ = window_days
    return early_bird_info(event)


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


CANCEL_REGISTRATION_FOOTER = (
    "Если передумали и не придёте — не забудьте отменить регистрацию: "
    "/cancel_registration"
)


def pay_later_message(event: dict, now: Optional[datetime] = None) -> str:
    """User-facing text after «Оплачу позже»."""
    now = now or datetime.now()
    t = timeline_for(event, now)
    eb = early_bird_info(event)
    if eb and not t.after_food_deadline:
        food_line = (
            f"• до <b>{t.food_deadline_display}</b> — успеваете в общий заказ еды "
            f"и скидка за раннюю регистрацию (−{eb.discount}₽);\n"
        )
    else:
        food_line = (
            f"• до <b>{t.food_deadline_display}</b> — успеваете в общий заказ еды;\n"
        )
    return (
        "Хорошо! Вы можете оплатить позже — команда /pay "
        "(там же ссылка на сайт и реквизиты).\n\n"
        "⏱ Сроки (ориентир — 06:00):\n"
        f"{food_line}"
        f"• после этой даты — пожалуйста, <b>принесите немного еды с собой</b>: "
        "мы заказываем заранее, и при большом числе поздних оплат на месте "
        "может не хватить / придётся докупать.\n"
        f"• до <b>{t.badge_deadline_display}</b> — успеваем подготовить "
        "<b>именной бейдж</b>;\n"
        "• позже — бейдж уже не печатаем (вас всё равно ждут).\n\n"
        "После оплаты пришлите скриншот в этот чат (или нажмите «Оплатил» в /pay)."
    )


def _deadline_for_kind(event: dict, kind: str) -> Optional[datetime]:
    if kind == "d4":
        return food_deadline(event)
    if kind == "d2":
        return badge_deadline(event)
    raise ValueError(f"unknown reminder kind: {kind}")


def reminder_kind_for_event(
    event: dict, now: Optional[datetime] = None
) -> Optional[str]:
    """Return ``d4`` or ``d2`` if *now* falls on that reminder calendar day.

    Reminder day = calendar day of the corresponding 06:00 deadline
    (D-4 food planning day, D-2 badge day). Prefer d4 if both somehow collide.
    """
    now = now or datetime.now()
    today = now.date()
    food = food_deadline(event)
    badge = badge_deadline(event)
    if food and today == food.date():
        return "d4"
    if badge and today == badge.date():
        return "d2"
    return None


def admin_preview_kinds_for_event(
    event: dict, now: Optional[datetime] = None
) -> list[str]:
    """Kinds whose user-reminder day is **tomorrow** (admin preview day)."""
    now = now or datetime.now()
    tomorrow = now.date() + timedelta(days=ADMIN_PREVIEW_DAYS_BEFORE_REMINDER)
    kinds: list[str] = []
    food = food_deadline(event)
    badge = badge_deadline(event)
    if food and tomorrow == food.date():
        kinds.append("d4")
    if badge and tomorrow == badge.date():
        kinds.append("d2")
    return kinds


def reminder_message(kind: str, event: dict, city: str) -> str:
    """Auto-reminder copy. First line is a dense TL;DR for Telegram chat preview."""
    t = timeline_for(event)
    food_short = format_date_short_ru(t.food_deadline)
    badge_short = format_date_short_ru(t.badge_deadline)
    eb = early_bird_near_food_cutoff(event)

    if kind == "d4":
        # First line ≈ chat list preview (keep short, high-signal).
        tldr_bits = [f"⏱ {city} · 4 дня", f"еда до {food_short}"]
        if eb:
            tldr_bits.append(f"ранняя −{eb.discount}₽ до {eb.deadline_short}")
        tldr_bits.append("/pay")
        tldr = " · ".join(tldr_bits)

        body = (
            f"{tldr}\n\n"
            "Если ещё не оплатили взнос — сейчас удобный момент: /pay.\n"
        )
        if eb:
            body += (
                f"До <b>{t.food_deadline_display}</b> — общий заказ еды "
                f"и ранняя скидка <b>−{eb.discount}₽</b>; "
                "после — еду планируем с запасом, при поздней оплате лучше "
                "принести что-то к столу с собой.\n"
            )
        else:
            body += (
                f"После <b>{t.food_deadline_display}</b> еду планируем с запасом; "
                "при поздней оплате лучше принести что-то к столу с собой.\n"
            )
        body += (
            f"Именной бейдж — если оплатите до <b>{t.badge_deadline_display}</b>."
            f"\n\n{CANCEL_REGISTRATION_FOOTER}"
        )
        return body

    if kind == "d2":
        tldr = (
            f"⏱ {city} · 2 дня · бейдж до {badge_short} · /pay"
        )
        return (
            f"{tldr}\n\n"
            f"<b>Последний срок для именного бейджа</b> — "
            f"<b>{t.badge_deadline_display}</b>.\n"
            "Оплатить: /pay. После оплаты пришлите скриншот в чат.\n\n"
            f"{CANCEL_REGISTRATION_FOOTER}"
        )
    raise ValueError(f"unknown reminder kind: {kind}")


def kind_label_ru(kind: str) -> str:
    if kind == "d4":
        return "D-4 (еда / 4 дня)"
    if kind == "d2":
        return "D-2 (бейдж / 2 дня)"
    return kind


VOLUNTEER_OPTIONS_TEXT = (
    "Если хотите помочь вместо взноса или в дополнение — напишите "
    "организатору <b>@mariikors</b>. Можно договориться на "
    "скидку / бесплатный вход / волонтёрство.\n\n"
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
