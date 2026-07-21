"""Unit tests for payment timeline (D-4 food / D-2 badge, 06:00)."""
from datetime import date, datetime

from src.payment_timeline import (
    BADGE_DAYS_BEFORE,
    FOOD_DAYS_BEFORE,
    admin_preview_kinds_for_event,
    badge_deadline,
    early_bird_near_food_cutoff,
    food_deadline,
    pay_later_message,
    reminder_kind_for_event,
    reminder_message,
    timeline_for,
    too_expensive_cancel_message,
)


def _event(day: date | datetime, **extra) -> dict:
    return {"date": day, "city": "Пермь", **extra}


def test_deadlines_are_four_and_two_days_before_at_six_am():
    event = _event(date(2026, 8, 1))
    food = food_deadline(event)
    badge = badge_deadline(event)
    assert food == datetime(2026, 7, 28, 6, 0, 0)
    assert badge == datetime(2026, 7, 30, 6, 0, 0)
    assert FOOD_DAYS_BEFORE == 4
    assert BADGE_DAYS_BEFORE == 2


def test_timeline_flags_after_deadlines():
    event = _event(date(2026, 8, 1))
    before = timeline_for(event, now=datetime(2026, 7, 27, 12, 0))
    assert not before.after_food_deadline
    assert not before.after_badge_deadline
    mid = timeline_for(event, now=datetime(2026, 7, 29, 12, 0))
    assert mid.after_food_deadline
    assert not mid.after_badge_deadline
    late = timeline_for(event, now=datetime(2026, 7, 31, 12, 0))
    assert late.after_food_deadline
    assert late.after_badge_deadline


def test_reminder_kind_on_deadline_calendar_days():
    event = _event(date(2026, 8, 1))
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 28, 10, 0)) == "d4"
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 30, 10, 0)) == "d2"
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 29, 10, 0)) is None


def test_pay_later_message_contains_dates_and_rules():
    event = _event(date(2026, 8, 1))
    text = pay_later_message(event)
    assert "28.07.2026" in text
    assert "30.07.2026" in text
    assert "еды" in text.lower() or "еду" in text.lower()
    assert "бейдж" in text.lower()
    assert "/pay" in text


def test_pay_later_combines_food_and_early_bird():
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    text = pay_later_message(event, now=datetime(2026, 7, 1, 12, 0))
    assert "28.07.2026" in text
    assert "общий заказ еды" in text
    assert "скидка за раннюю регистрацию" in text
    assert "500" in text


def test_reminder_and_too_expensive_copy():
    event = _event(date(2026, 8, 1))
    d4 = reminder_message("d4", event, "Пермь")
    d2 = reminder_message("d2", event, "Пермь")
    assert "4 дня" in d4 or "4 дн" in d4
    assert "бейдж" in d2.lower()
    # TL;DR first line (Telegram preview)
    assert d4.split("\n", 1)[0].startswith("⏱")
    assert d2.split("\n", 1)[0].startswith("⏱")
    assert "еда до 28.07" in d4.split("\n", 1)[0]
    assert "бейдж до 30.07" in d2.split("\n", 1)[0]
    # cancel footer on all auto-reminders
    assert "/cancel_registration" in d4
    assert "/cancel_registration" in d2
    cancel = too_expensive_cancel_message()
    assert "@mariikors" in cancel
    assert "волонт" in cancel.lower() or "Волонт" in cancel


def test_d4_includes_early_bird_aligned_with_food():
    # Early bird cutoff == food D-4 (28.07 06:00)
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    info = early_bird_near_food_cutoff(event)
    assert info is not None
    assert info.discount == 500
    assert info.deadline_short == "28.07"
    d4 = reminder_message("d4", event, "Пермь")
    first = d4.split("\n", 1)[0]
    assert "ранняя" in first.lower() or "−500" in first or "-500" in first
    assert "500" in d4
    assert "28.07" in d4


def test_d4_skips_early_bird_when_no_discount():
    event = _event(date(2026, 8, 1), early_bird_discount=0)
    assert early_bird_near_food_cutoff(event) is None
    d4 = reminder_message("d4", event, "Пермь")
    assert "ранняя" not in d4.lower()

def test_admin_preview_is_day_before_send():
    event = _event(date(2026, 8, 1))
    # send d4 on 28 Jul → preview 27 Jul
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 27, 8)) == ["d4"]
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 28, 8)) == []
