"""Unit tests for payment timeline (D-4 food / D-2 badge, 06:00)."""
from datetime import date, datetime

from src.payment_timeline import (
    BADGE_DAYS_BEFORE,
    FOOD_DAYS_BEFORE,
    badge_deadline,
    food_deadline,
    pay_later_message,
    reminder_kind_for_event,
    reminder_message,
    timeline_for,
    too_expensive_cancel_message,
)


def _event(day: date | datetime) -> dict:
    return {"date": day, "city": "Пермь"}


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


def test_reminder_and_too_expensive_copy():
    event = _event(date(2026, 8, 1))
    d4 = reminder_message("d4", event, "Пермь")
    d2 = reminder_message("d2", event, "Пермь")
    assert "4 дня" in d4 or "4 дн" in d4
    assert "бейдж" in d2.lower()
    cancel = too_expensive_cancel_message()
    assert "@mariikors" in cancel
    assert "волонт" in cancel.lower() or "Волонт" in cancel
