"""Meetup payment timeline: early-bird, optional late-food ask, named badge.

Deadlines are at 06:00 on the calendar day (buffer: “before six in the morning”).
Timezone-naive datetimes are treated as local wall time of the stored event date.

Defaults (relative to event date) — Maria/Petr Jul 2026:
- early bird + badge print share one cutoff: D-3 (or event.early_bird_deadline)
- optional “bring food when late” messaging (event.ask_bring_food), default food D-4
- one auto-reminder the calendar day *before* that shared cutoff

Auto-reminders (kinds keep legacy keys d4/d2 for registration flags):
- d4: day before early-bird (preferred) or food cutoff — combined copy when same day
- d2: day before badge cutoff; skipped if same send-day as d4 (d4 wins)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Optional


FOOD_DAYS_BEFORE = 4
# Shared default: early bird and badge on the same morning (no deadline sprawl).
EARLY_BIRD_DAYS_BEFORE = 3
BADGE_DAYS_BEFORE = 3
# User-facing reminders fire one calendar day before the relevant cutoff.
BADGE_REMINDER_DAYS_BEFORE_DEADLINE = 1
EARLY_BIRD_REMINDER_DAYS_BEFORE_DEADLINE = 1
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


def ask_bring_food_enabled(event: dict) -> bool:
    """Whether late-registry “bring food” copy is on for this event.

    Missing key defaults to True so older events keep previous behaviour.
    """
    return bool(event.get("ask_bring_food", True))


def food_deadline(event: dict) -> Optional[datetime]:
    """Food-planning cutoff. None when bring-food messaging is disabled."""
    if not ask_bring_food_enabled(event):
        return None
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


@dataclass(frozen=True)
class EarlyBirdInfo:
    discount: int
    deadline: date
    deadline_at: datetime
    deadline_display: str  # full «dd.mm.YYYY в HH:MM»
    deadline_short: str  # dd.mm


def early_bird_deadline_at(event: dict) -> Optional[datetime]:
    """Early-bird cutoff at 06:00.

    Prefer stored ``early_bird_deadline`` (date or datetime). If missing, use
    the same default as the badge (D-3) so we don't multiply cutoffs.
    Only when ``early_bird_discount > 0``.
    """
    if int(event.get("early_bird_discount") or 0) <= 0:
        return None
    stored = _as_date(event.get("early_bird_deadline"))
    if stored is not None:
        return datetime.combine(stored, time(hour=DEADLINE_HOUR, minute=0, second=0))
    # Align with badge by default (EARLY_BIRD_DAYS_BEFORE == BADGE_DAYS_BEFORE).
    return deadline_at(event, EARLY_BIRD_DAYS_BEFORE)


def is_early_bird_active(event: dict, now: Optional[datetime] = None) -> bool:
    """True while now is strictly before the early-bird cutoff."""
    now = now or datetime.now()
    dl = early_bird_deadline_at(event)
    return bool(dl and now < dl)


def early_bird_info(event: dict) -> Optional[EarlyBirdInfo]:
    """Return early-bird discount + cutoff if discount is configured."""
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
    """Compatibility alias — returns early_bird_info when configured.

    ``window_days`` kept for call-site compatibility; unused.
    """
    _ = window_days
    return early_bird_info(event)


@dataclass(frozen=True)
class TimelineCopy:
    food_deadline: Optional[datetime]
    badge_deadline: Optional[datetime]
    early_bird_deadline: Optional[datetime]
    food_deadline_display: str
    badge_deadline_display: str
    early_bird_deadline_display: str
    after_food_deadline: bool
    after_badge_deadline: bool
    after_early_bird_deadline: bool
    ask_bring_food: bool


def timeline_for(event: dict, now: Optional[datetime] = None) -> TimelineCopy:
    now = now or datetime.now()
    food = food_deadline(event)
    badge = badge_deadline(event)
    eb = early_bird_deadline_at(event)
    return TimelineCopy(
        food_deadline=food,
        badge_deadline=badge,
        early_bird_deadline=eb,
        food_deadline_display=format_deadline_ru(food),
        badge_deadline_display=format_deadline_ru(badge),
        early_bird_deadline_display=format_deadline_ru(eb),
        after_food_deadline=bool(food and now >= food),
        after_badge_deadline=bool(badge and now >= badge),
        after_early_bird_deadline=bool(eb and now >= eb),
        ask_bring_food=ask_bring_food_enabled(event),
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
    lines: list[str] = [
        "Хорошо! Вы можете оплатить позже — команда /pay "
        "(там же ссылка на сайт и реквизиты).\n",
        "⏱ Сроки (ориентир — 06:00):",
    ]

    if eb and not t.after_early_bird_deadline:
        lines.append(
            f"• до <b>{eb.deadline_display}</b> — скидка за раннюю регистрацию "
            f"(−{eb.discount}₽);"
        )
    elif eb and t.after_early_bird_deadline:
        lines.append(
            f"• ранняя скидка (−{eb.discount}₽) уже закончилась "
            f"({eb.deadline_display});"
        )

    if t.ask_bring_food and t.food_deadline is not None:
        if eb and t.food_deadline == eb.deadline_at and not t.after_food_deadline:
            # Same morning as early bird — don't repeat the date line twice;
            # only add food-specific after-note below.
            pass
        else:
            lines.append(
                f"• до <b>{t.food_deadline_display}</b> — успеваете в общий заказ еды;"
            )
        lines.append(
            "• после этой даты — пожалуйста, <b>принесите немного еды с собой</b>: "
            "мы заказываем заранее, и при большом числе поздних оплат на месте "
            "может не хватить / придётся докупать."
        )
        # If food and early bird share the same cutoff and early bird line
        # already used that date, clarify food on the early-bird line was enough;
        # the bring-food bullet still stands.

    if t.badge_deadline is not None:
        lines.append(
            f"• до <b>{t.badge_deadline_display}</b> — успеваем подготовить "
            f"<b>именной бейдж</b>;"
        )
        lines.append("• позже — бейдж уже не печатаем (вас всё равно ждут).")

    lines.append(
        "\nПосле оплаты пришлите скриншот в этот чат (или нажмите «Оплатил» в /pay)."
    )
    return "\n".join(lines)


def _d4_anchor_deadline(event: dict) -> Optional[datetime]:
    """Cutoff that drives the d4 reminder (early bird preferred, else food)."""
    eb = early_bird_deadline_at(event)
    if eb is not None:
        return eb
    return food_deadline(event)


def _deadline_for_kind(event: dict, kind: str) -> Optional[datetime]:
    if kind == "d4":
        return _d4_anchor_deadline(event)
    if kind == "d2":
        return badge_deadline(event)
    raise ValueError(f"unknown reminder kind: {kind}")


def reminder_send_day(event: dict, kind: str) -> Optional[date]:
    """Calendar day when the user-facing auto-reminder is sent."""
    dl = _deadline_for_kind(event, kind)
    if dl is None:
        return None
    if kind == "d4":
        return dl.date() - timedelta(days=EARLY_BIRD_REMINDER_DAYS_BEFORE_DEADLINE)
    if kind == "d2":
        return dl.date() - timedelta(days=BADGE_REMINDER_DAYS_BEFORE_DEADLINE)
    raise ValueError(f"unknown reminder kind: {kind}")


def reminder_kind_for_event(
    event: dict, now: Optional[datetime] = None
) -> Optional[str]:
    """Return ``d4`` or ``d2`` if *now* falls on that reminder calendar day.

    Prefer d4 if both land on the same day (shared early-bird+badge cutoff) so
    unpaid users get one combined auto-reminder, not two.
    """
    now = now or datetime.now()
    today = now.date()
    d4_day = reminder_send_day(event, "d4")
    d2_day = reminder_send_day(event, "d2")
    if d4_day and today == d4_day:
        return "d4"
    if d2_day and today == d2_day:
        return "d2"
    return None


def admin_preview_kinds_for_event(
    event: dict, now: Optional[datetime] = None
) -> list[str]:
    """Kinds whose user-reminder day is **tomorrow** (admin preview day).

    Same-day rule as ``reminder_kind_for_event``: if both d4 and d2 would
    fire tomorrow, only preview d4 (combined copy). Two admin previews for
    one user send is noise and implies two messages when there is one.
    """
    now = now or datetime.now()
    tomorrow = now.date() + timedelta(days=ADMIN_PREVIEW_DAYS_BEFORE_REMINDER)
    d4_day = reminder_send_day(event, "d4")
    d2_day = reminder_send_day(event, "d2")
    kinds: list[str] = []
    if d4_day and tomorrow == d4_day:
        kinds.append("d4")
        # d4 already covers the shared early-bird+badge morning.
        return kinds
    if d2_day and tomorrow == d2_day:
        kinds.append("d2")
    return kinds


def reminder_message(kind: str, event: dict, city: str) -> str:
    """Auto-reminder copy. First line is a dense TL;DR for Telegram chat preview."""
    t = timeline_for(event)
    badge_short = format_date_short_ru(t.badge_deadline)
    eb = early_bird_info(event)

    if kind == "d4":
        # Early-bird / food planning reminder (day before cutoff).
        if eb is not None:
            tldr_bits = [
                f"⏱ {city}",
                f"ранняя −{eb.discount}₽ до {eb.deadline_short}",
            ]
            if t.ask_bring_food and t.food_deadline is not None:
                food_short = format_date_short_ru(t.food_deadline)
                tldr_bits.append(f"еда до {food_short}")
            tldr_bits.append("/pay")
            tldr = " · ".join(tldr_bits)
            body = (
                f"{tldr}\n\n"
                "Если ещё не оплатили взнос — сейчас удобный момент: /pay.\n"
                f"До <b>{eb.deadline_display}</b> — ранняя скидка "
                f"<b>−{eb.discount}₽</b>"
            )
            if t.ask_bring_food and t.food_deadline is not None:
                body += (
                    f"; до <b>{t.food_deadline_display}</b> — общий заказ еды"
                    " (после — при поздней оплате лучше принести что-то "
                    "к столу с собой)"
                )
            body += ".\n"
            if t.badge_deadline is not None:
                body += (
                    f"Именной бейдж — если оплатите до "
                    f"<b>{t.badge_deadline_display}</b>."
                )
            body += f"\n\n{CANCEL_REGISTRATION_FOOTER}"
            return body

        if t.ask_bring_food and t.food_deadline is not None:
            food_short = format_date_short_ru(t.food_deadline)
            tldr = f"⏱ {city} · еда до {food_short} · /pay"
            body = (
                f"{tldr}\n\n"
                "Если ещё не оплатили взнос — сейчас удобный момент: /pay.\n"
                f"После <b>{t.food_deadline_display}</b> еду планируем с запасом; "
                "при поздней оплате лучше принести что-то к столу с собой.\n"
            )
            if t.badge_deadline is not None:
                body += (
                    f"Именной бейдж — если оплатите до "
                    f"<b>{t.badge_deadline_display}</b>."
                )
            body += f"\n\n{CANCEL_REGISTRATION_FOOTER}"
            return body

        # No early bird and no food — nothing useful for d4.
        tldr = f"⏱ {city} · /pay"
        return (
            f"{tldr}\n\n"
            "Если ещё не оплатили взнос — сейчас удобный момент: /pay.\n\n"
            f"{CANCEL_REGISTRATION_FOOTER}"
        )

    if kind == "d2":
        tldr = f"⏱ {city} · 3 дня · бейдж до {badge_short} · /pay"
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
        return "ранняя скидка / еда (за день до cutoff)"
    if kind == "d2":
        return "бейдж (за день до cutoff; D-3)"
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
