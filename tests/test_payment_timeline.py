"""Unit tests for payment timeline (shared D-3 early-bird+badge, optional food)."""

from datetime import date, datetime

from src.payment_timeline import (
    BADGE_DAYS_BEFORE,
    EARLY_BIRD_DAYS_BEFORE,
    FOOD_DAYS_BEFORE,
    admin_preview_kinds_for_event,
    ask_bring_food_enabled,
    badge_deadline,
    early_bird_deadline_at,
    early_bird_info,
    early_bird_near_food_cutoff,
    food_deadline,
    pay_later_message,
    reminder_kind_for_event,
    reminder_message,
    reminder_send_day,
    timeline_for,
    too_expensive_cancel_message,
)


def _event(day: date | datetime, **extra) -> dict:
    return {"date": day, "city": "Пермь", **extra}


def test_deadlines_defaults_share_d3():
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    food = food_deadline(event)
    badge = badge_deadline(event)
    eb = early_bird_deadline_at(event)
    assert food == datetime(2026, 7, 28, 6, 0, 0)  # food still D-4 when enabled
    assert badge == datetime(2026, 7, 29, 6, 0, 0)
    assert eb == datetime(2026, 7, 29, 6, 0, 0)  # same morning as badge
    assert FOOD_DAYS_BEFORE == 4
    assert BADGE_DAYS_BEFORE == 3
    assert EARLY_BIRD_DAYS_BEFORE == 3
    assert BADGE_DAYS_BEFORE == EARLY_BIRD_DAYS_BEFORE


def test_ask_bring_food_defaults_true_and_can_disable():
    on = _event(date(2026, 8, 1))
    assert ask_bring_food_enabled(on) is True
    assert food_deadline(on) is not None
    off = _event(date(2026, 8, 1), ask_bring_food=False)
    assert ask_bring_food_enabled(off) is False
    assert food_deadline(off) is None


def test_timeline_flags_after_deadlines():
    event = _event(date(2026, 8, 1))
    before = timeline_for(event, now=datetime(2026, 7, 27, 12, 0))
    assert not before.after_food_deadline
    assert not before.after_badge_deadline
    mid = timeline_for(event, now=datetime(2026, 7, 28, 12, 0))
    assert mid.after_food_deadline
    assert not mid.after_badge_deadline
    late = timeline_for(event, now=datetime(2026, 7, 30, 12, 0))
    assert late.after_food_deadline
    assert late.after_badge_deadline


def test_shared_cutoff_one_auto_reminder_day():
    """Early bird + badge same day → one send day; d4 wins over d2."""
    event = _event(date(2026, 8, 1), early_bird_discount=500, ask_bring_food=False)
    # Shared cutoff Jul 29 → reminder Jul 28
    assert reminder_send_day(event, "d4") == date(2026, 7, 28)
    assert reminder_send_day(event, "d2") == date(2026, 7, 28)
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 28, 10, 0)) == "d4"
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 29, 10, 0)) is None
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 27, 10, 0)) is None
    # Admin preview day is also one kind only
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 27, 3, 15)) == [
        "d4"
    ]


def test_badge_only_reminder_when_no_early_bird_and_no_food():
    event = _event(date(2026, 8, 1), early_bird_discount=0, ask_bring_food=False)
    assert reminder_send_day(event, "d4") is None
    assert reminder_send_day(event, "d2") == date(2026, 7, 28)
    assert reminder_kind_for_event(event, now=datetime(2026, 7, 28, 10, 0)) == "d2"


def test_pay_later_message_contains_dates_and_rules():
    event = _event(date(2026, 8, 1))
    text = pay_later_message(event)
    assert "28.07.2026" in text  # food D-4
    assert "29.07.2026" in text  # badge D-3
    assert "еды" in text.lower() or "еду" in text.lower()
    assert "бейдж" in text.lower()
    assert "/pay" in text


def test_pay_later_hides_food_when_disabled():
    event = _event(date(2026, 8, 1), ask_bring_food=False, early_bird_discount=500)
    text = pay_later_message(event, now=datetime(2026, 7, 1, 12, 0))
    assert "принесите" not in text.lower()
    assert "общий заказ" not in text.lower()
    assert "бейдж" in text.lower()
    assert "ранн" in text.lower()
    assert "29.07.2026" in text  # shared D-3


def test_pay_later_combines_food_and_early_bird():
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    text = pay_later_message(event, now=datetime(2026, 7, 1, 12, 0))
    assert "скидка за раннюю регистрацию" in text
    assert "500" in text
    assert "29.07.2026" in text  # early bird D-3
    assert "принесите" in text.lower() or "еды" in text.lower()


def test_early_bird_uses_stored_deadline():
    event = _event(
        date(2026, 8, 1),
        early_bird_discount=500,
        early_bird_deadline=datetime(2026, 7, 25),
    )
    dl = early_bird_deadline_at(event)
    assert dl == datetime(2026, 7, 25, 6, 0, 0)
    info = early_bird_info(event)
    assert info is not None
    assert info.deadline_short == "25.07"
    assert reminder_send_day(event, "d4") == date(2026, 7, 24)


def test_reminder_and_too_expensive_copy():
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    d4 = reminder_message("d4", event, "Пермь")
    d2 = reminder_message("d2", event, "Пермь")
    assert "бейдж" in d2.lower()
    assert d4.split("\n", 1)[0].startswith("⏱")
    assert d2.split("\n", 1)[0].startswith("⏱")
    assert "бейдж до 29.07" in d2.split("\n", 1)[0]
    assert "3 дня" in d2.split("\n", 1)[0]
    assert "/cancel_registration" in d4
    assert "/cancel_registration" in d2
    cancel = too_expensive_cancel_message()
    assert "@mariikors" in cancel
    assert "волонт" in cancel.lower() or "Волонт" in cancel


def test_d4_includes_early_bird():
    event = _event(date(2026, 8, 1), early_bird_discount=500)
    info = early_bird_near_food_cutoff(event)
    assert info is not None
    assert info.discount == 500
    assert info.deadline_short == "29.07"
    d4 = reminder_message("d4", event, "Пермь")
    first = d4.split("\n", 1)[0]
    assert "ранняя" in first.lower() or "−500" in first or "-500" in first
    assert "500" in d4
    assert "29.07" in d4
    # Combined: also mentions badge
    assert "бейдж" in d4.lower()


def test_d4_skips_early_bird_when_no_discount():
    event = _event(date(2026, 8, 1), early_bird_discount=0)
    assert early_bird_near_food_cutoff(event) is None
    d4 = reminder_message("d4", event, "Пермь")
    assert "ранняя" not in d4.lower()
    assert "еда" in d4.lower() or "еду" in d4.lower()


def test_admin_preview_is_day_before_send():
    event = _event(date(2026, 8, 1), early_bird_discount=500, ask_bring_food=False)
    # shared send Jul 28 → preview Jul 27; only d4 (same-day d2 suppressed)
    kinds = admin_preview_kinds_for_event(event, now=datetime(2026, 7, 27, 8))
    assert kinds == ["d4"]
    assert admin_preview_kinds_for_event(event, now=datetime(2026, 7, 28, 8)) == []
