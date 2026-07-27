"""Tests for admin manual registration (no Telegram user required)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import User
from bson import ObjectId

from src.routers.admin import admin_manual_register


@pytest.fixture
def mock_message():
    message = AsyncMock()
    message.from_user = MagicMock(spec=User)
    message.from_user.id = 999
    message.from_user.username = "maria"
    message.chat = MagicMock()
    message.chat.id = 999
    return message


@pytest.fixture
def mock_state():
    return AsyncMock(spec=FSMContext)


@pytest.fixture
def event_doc():
    return {
        "_id": ObjectId("6a599a17a37724d81b7eadc3"),
        "city": "Пермь",
        "date": datetime(2026, 8, 1),
        "guests_enabled": True,
        "max_guests_per_person": 3,
        "pricing_type": "formula",
        "price_formula_base": 1400,
        "price_formula_rate": 200,
        "price_formula_reference_year": 2026,
        "price_formula_step": 3,
        "guest_price_minimum": 1500,
        "free_for_types": ["TEACHER", "ORGANIZER"],
        "early_bird_discount": 0,
        "status": "upcoming",
    }


@pytest.mark.asyncio
async def test_admin_manual_register_without_telegram(
    mock_message, mock_state, event_doc
):
    inserted_id = ObjectId()
    registration = {
        "_id": inserted_id,
        "full_name": "Петр Петров",
        "event_id": str(event_doc["_id"]),
        "start_source": "manual_admin",
        "user_id": None,
        "guests": [],
    }

    app = AsyncMock()
    app.get_all_events = AsyncMock(return_value=[event_doc])
    app.get_event_by_id = AsyncMock(return_value=event_doc)
    app.parse_graduation_year_and_class_letter = MagicMock(
        return_value=(2010, "А", None)
    )
    app.calculate_event_payment = MagicMock(return_value=(2000, 0, 2000, 2000))
    app.save_registered_user = AsyncMock()
    app.save_registration_guests = AsyncMock()
    app.save_event_log = AsyncMock()
    app.export_registered_users_to_google_sheets = AsyncMock()
    app.collection = AsyncMock()
    app.collection.find_one = AsyncMock(return_value=registration)

    name = MagicMock(text="Петр Петров")
    year = MagicMock(text="2010А")
    tg = MagicMock(text="-")

    with (
        patch("src.routers.admin.ask_user_choice", new_callable=AsyncMock) as choice,
        patch(
            "src.routers.admin.ask_user_raw",
            new_callable=AsyncMock,
            side_effect=[name, year, tg],
        ),
        patch("src.routers.admin.send_safe", new_callable=AsyncMock) as send,
        patch(
            "src.routers.admin.freeze_new_registration_snapshot",
            new_callable=AsyncMock,
            return_value={"source_registration_id": str(inserted_id)},
        ) as freeze,
    ):
        # event → type → guests count → confirm
        choice.side_effect = [
            str(event_doc["_id"]),
            "GRADUATE",
            "0",
            "yes",
        ]
        await admin_manual_register(mock_message, mock_state, app)

    app.save_registered_user.assert_awaited_once()
    saved_user = app.save_registered_user.call_args.args[0]
    assert saved_user.full_name == "Петр Петров"
    assert saved_user.start_source == "manual_admin"
    assert saved_user.user_id is None
    assert saved_user.graduation_year == 2010
    freeze.assert_awaited_once()
    send.assert_awaited()
    assert any("Зарегистрирован" in str(c.args[1]) for c in send.await_args_list)


@pytest.mark.asyncio
async def test_admin_manual_register_with_guest_year(
    mock_message, mock_state, event_doc
):
    inserted_id = ObjectId()
    registration = {
        "_id": inserted_id,
        "full_name": "Мария Сидорова",
        "event_id": str(event_doc["_id"]),
        "start_source": "manual_admin",
        "user_id": 42,
        "guests": [],
    }
    app = AsyncMock()
    app.get_all_events = AsyncMock(return_value=[event_doc])
    app.get_event_by_id = AsyncMock(return_value=event_doc)
    app.parse_graduation_year_and_class_letter = MagicMock(
        side_effect=[(2012, "Б", None), (2018, "В", None)]
    )
    app.calculate_event_payment = MagicMock(return_value=(1800, 0, 1800, 1800))
    app.calculate_guest_price = MagicMock(return_value=(1600, 1600))
    app.save_registered_user = AsyncMock()
    app.save_registration_guests = AsyncMock()
    app.save_event_log = AsyncMock()
    app.export_registered_users_to_google_sheets = AsyncMock()
    app.collection = AsyncMock()
    app.collection.find_one = AsyncMock(return_value=registration)

    name = MagicMock(text="Мария Сидорова")
    year = MagicMock(text="2012Б")
    tg = MagicMock(text="42")
    guest_name = MagicMock(text="Гость Гостев")
    guest_year = MagicMock(text="2018В")

    with (
        patch("src.routers.admin.ask_user_choice", new_callable=AsyncMock) as choice,
        patch(
            "src.routers.admin.ask_user_raw",
            new_callable=AsyncMock,
            side_effect=[name, year, tg],
        ),
        patch(
            "src.user_interactions.ask_user_raw",
            new_callable=AsyncMock,
            side_effect=[guest_name, guest_year],
        ),
        patch("src.routers.admin.send_safe", new_callable=AsyncMock),
        patch(
            "src.routers.admin.freeze_new_registration_snapshot",
            new_callable=AsyncMock,
            return_value={},
        ),
    ):
        choice.side_effect = [
            str(event_doc["_id"]),
            "GRADUATE",
            "1",
            "yes",
        ]
        await admin_manual_register(mock_message, mock_state, app)

    app.save_registration_guests.assert_awaited_once()
    guests = app.save_registration_guests.call_args.args[2]
    assert len(guests) == 1
    assert guests[0]["name"] == "Гость Гостев"
    assert guests[0]["graduation_year"] == 2018
    assert guests[0]["price"] == 1600
