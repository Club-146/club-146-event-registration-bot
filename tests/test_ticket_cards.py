"""Paid ticket-card gate, rendering, delivery, and `/status` recovery tests."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import BufferedInputFile
from PIL import Image

from src.ticket_cards import (
    CARD_SIZE,
    TicketCardError,
    _font,
    build_ticket_card_data,
    is_ticket_unlocked,
    make_ticket_card,
    send_paid_ticket_card,
)


EVENT_ID = "6a599a17a37724d81b7eadc3"


def _registration(status="confirmed", **updates):
    registration = {
        "_id": "6a6011111111111111111111",
        "user_id": 123456789,
        "event_id": EVENT_ID,
        "full_name": "Лавров Петр",
        "graduate_type": "GRADUATE",
        "graduation_year": 2007,
        "class_letter": "А",
        "payment_status": status,
        "payment_amount": 2500,
        "target_city": "Пермь",
    }
    registration.update(updates)
    return registration


def _event(**updates):
    event = {
        "_id": EVENT_ID,
        "name": "Летняя встреча выпускников 2026",
        "city": "Пермь",
        "date_display": "1 Августа, Сб",
        "time_display": "18:00",
        "venue": 'Беседка "Журавушка"',
        "address": 'г.Пермь, ул. Встречная 28, беседка "Журавушка"',
        "pricing_type": "formula",
        "free_for_types": [],
    }
    event.update(updates)
    return event


def test_confirmed_registration_renders_deterministic_png():
    first = make_ticket_card(_registration(), _event())
    second = make_ticket_card(_registration(), _event())

    assert first == second
    assert first.ticket_code.startswith("146-")
    assert sha256(first.image).hexdigest() == sha256(second.image).hexdigest()
    assert "Лавров" not in first.filename

    image = Image.open(BytesIO(first.image))
    assert image.format == "PNG"
    assert image.size == CARD_SIZE
    assert image.mode == "RGB"


def test_ticket_font_is_portable_and_supports_cyrillic():
    assert _font(24).getname()[0] == "DejaVu Sans"


def test_visual_code_is_bound_to_event_registration_and_telegram_user():
    original = build_ticket_card_data(_registration(), _event()).ticket_code
    other_user = build_ticket_card_data(
        _registration(user_id=987654321), _event()
    ).ticket_code
    other_registration = build_ticket_card_data(
        _registration(_id="6a6022222222222222222222"), _event()
    ).ticket_code

    assert len({original, other_user, other_registration}) == 3


def test_immediate_card_lists_up_to_three_named_guests_as_one_group():
    registration = _registration(
        guests=[{"name": "Анна"}, {"name": "Борис"}, {"name": "Вера"}]
    )
    data = build_ticket_card_data(registration, _event())
    artifact = make_ticket_card(registration, _event())

    assert data.guest_names == ["Анна", "Борис", "Вера"]
    assert artifact.image != make_ticket_card(_registration(), _event()).image


def test_more_than_three_guests_fails_closed_instead_of_omitting_a_person():
    registration = _registration(
        guests=[{"name": f"Гость {index}"} for index in range(4)]
    )
    with pytest.raises(TicketCardError, match="at most three"):
        make_ticket_card(registration, _event())


@pytest.mark.parametrize(
    "status", [None, "not paid", "Не оплачено", "pending", "declined", "paid"]
)
def test_unconfirmed_registration_never_unlocks_or_renders(status):
    registration = _registration(status=status)

    assert is_ticket_unlocked(registration) is False
    with pytest.raises(TicketCardError, match="payment_status == confirmed"):
        make_ticket_card(registration, _event())


def test_event_binding_mismatch_fails_closed():
    with pytest.raises(TicketCardError, match="does not match"):
        make_ticket_card(_registration(), _event(_id="other-event"))


def test_future_website_ticket_code_is_additive_and_preferred():
    artifact = make_ticket_card(
        _registration(ticket_code="CLUB146-PAID-ABC123"), _event()
    )

    assert artifact.ticket_code == "CLUB146-PAID-ABC123"
    assert artifact.filename.endswith("CLUB146-PAID-ABC123.png")


def test_invalid_future_website_ticket_code_fails_closed():
    with pytest.raises(TicketCardError, match="Invalid website-issued"):
        make_ticket_card(_registration(ticket_code="../../not-a-ticket"), _event())


@pytest.mark.asyncio
async def test_send_paid_ticket_card_uses_telegram_photo():
    bot = AsyncMock()
    dependencies = MagicMock(bot=bot)
    with patch("src.ticket_cards.get_dependency_manager", return_value=dependencies):
        sent = await send_paid_ticket_card(123456789, _registration(), _event())

    assert sent is True
    call = bot.send_photo.await_args
    assert call is not None
    assert call.kwargs["chat_id"] == 123456789
    assert call.kwargs["parse_mode"] is None
    assert isinstance(call.kwargs["photo"], BufferedInputFile)
    assert "Покажите эту карточку на входе" in call.kwargs["caption"]


@pytest.mark.asyncio
async def test_send_paid_ticket_card_does_not_touch_telegram_when_unpaid():
    with patch("src.ticket_cards.get_dependency_manager") as dependencies:
        sent = await send_paid_ticket_card(
            123456789, _registration(status="pending"), _event()
        )

    assert sent is False
    dependencies.assert_not_called()


def _status_message():
    message = AsyncMock()
    message.from_user = MagicMock(id=123456789, username="petr")
    message.chat = MagicMock(id=123456789, type="private")
    message.text = "/status"
    return message


def _status_app(registration):
    app = MagicMock()
    app.save_event_log = AsyncMock()
    app.get_user_active_registrations = AsyncMock(return_value=[registration])
    app.get_event_for_registration = AsyncMock(return_value=_event())
    app.get_enabled_events = AsyncMock(return_value=[_event()])
    app.is_event_passed = MagicMock(return_value=False)
    return app


@pytest.mark.asyncio
async def test_status_resends_confirmed_ticket_card():
    from src.router import status_handler

    registration = _registration()
    app = _status_app(registration)
    with (
        patch("src.router.send_safe", new_callable=AsyncMock) as send_safe,
        patch("src.router.send_paid_ticket_card", new_callable=AsyncMock) as send_card,
    ):
        await status_handler(_status_message(), AsyncMock(), app)

    status_text = send_safe.await_args.args[1]
    assert "Именной билет действителен для входа и доступен ниже" in status_text
    send_card.assert_awaited_once_with(123456789, registration, _event())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "not paid", "pending", "declined"])
async def test_status_never_sends_unconfirmed_ticket_card(status):
    from src.router import status_handler

    registration = _registration(status=status)
    app = _status_app(registration)
    with (
        patch("src.router.send_safe", new_callable=AsyncMock) as send_safe,
        patch("src.router.send_paid_ticket_card", new_callable=AsyncMock) as send_card,
    ):
        await status_handler(_status_message(), AsyncMock(), app)

    status_text = send_safe.await_args.args[1]
    assert "Заявка сохранена; действующего билета пока нет" in status_text
    assert "после подтверждения оплаты" in status_text
    send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_receipt_confirmation_sends_newly_unlocked_ticket():
    from src.routers.payment import confirm_payment_callback

    registration = _registration(
        payment_status="pending",
        discounted_payment_amount=2500,
        regular_payment_amount=3000,
    )
    updated_registration = _registration(
        payment_status="confirmed",
        payment_amount=2500,
        payment_history=[{"amount": 2500, "timestamp": "2026-07-20T12:00:00"}],
        discounted_payment_amount=2500,
        regular_payment_amount=3000,
    )

    callback = AsyncMock()
    callback.data = f"confirm_payment_123456789_{EVENT_ID}_2500"
    callback.message = AsyncMock()
    callback.message.chat = MagicMock(id=-100146)
    callback.message.caption = None
    callback.message.text = "Проверка пожертвования"
    callback.message.edit_reply_markup = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    state = AsyncMock()
    state.get_state.return_value = None

    app = MagicMock()
    app.update_payment_status = AsyncMock()
    app.collection.find_one = AsyncMock(return_value=updated_registration)
    app.get_event_for_registration = AsyncMock(return_value=_event())
    app.export_registered_users_to_google_sheets = AsyncMock()

    with (
        patch("src.routers.payment.app", app),
        patch(
            "src.routers.payment._resolve_registration_from_callback",
            new_callable=AsyncMock,
            return_value=registration,
        ),
        patch(
            "src.routers.payment._resolve_payment_amount",
            new_callable=AsyncMock,
            return_value=2500,
        ),
        patch("src.routers.payment.send_safe", new_callable=AsyncMock),
        patch(
            "src.routers.payment.send_paid_ticket_card", new_callable=AsyncMock
        ) as send_card,
    ):
        await confirm_payment_callback(callback, state)

    app.update_payment_status.assert_awaited_once_with(
        123456789,
        event_id=EVENT_ID,
        status="confirmed",
        payment_amount=2500,
    )
    send_card.assert_awaited_once_with(123456789, updated_registration, _event())
