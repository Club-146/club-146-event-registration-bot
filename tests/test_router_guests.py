import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.types import User


@pytest.fixture
def mock_message():
    message = AsyncMock()
    message.from_user = MagicMock(spec=User)
    message.from_user.id = 12345
    message.from_user.username = "test_user"
    message.chat = MagicMock()
    message.chat.id = 12345
    return message


@pytest.fixture
def mock_state():
    return AsyncMock(spec=FSMContext)


@pytest.fixture
def mock_app():
    app = MagicMock()
    app.save_registration_guests = AsyncMock()
    app.save_event_log = AsyncMock()
    app.get_user_active_registrations = AsyncMock(return_value=[])
    app.get_event_for_registration = AsyncMock(return_value=None)
    app.calculate_event_payment = MagicMock(return_value=(3000, 0, 3000, 3000))
    app.calculate_guest_price = MagicMock(return_value=(3000, 3000))
    app.parse_graduation_year_and_class_letter = MagicMock(
        return_value=(2010, "А", None)
    )
    return app


@pytest.fixture
def base_reg():
    return {
        "target_city": "Москва",
        "event_id": "aabbccddeeff00112233aabb",
        "graduation_year": 2010,
        "class_letter": "А",
        "graduate_type": "GRADUATE",
        "full_name": "Тест Тестов",
        "guests": [],
    }


@pytest.fixture
def base_event():
    return {
        "max_guests_per_person": 3,
        "guests_enabled": True,
        "city": "Москва",
        "date_display": "21 Марта, Сб",
        "pricing_type": "formula",
        "price_formula_base": 2000,
        "price_formula_rate": 100,
        "price_formula_reference_year": 2026,
        "price_formula_step": 1,
        "early_bird_discount": 0,
    }


# --- Test 1: _edit_guests adds new guests correctly ---


@pytest.mark.asyncio
async def test_edit_guests_add_new(
    mock_message, mock_state, mock_app, base_reg, base_event
):
    """_edit_guests adds new guests when user selects count and provides names + year."""
    mock_name_resp = MagicMock()
    mock_name_resp.text = "Иван Иванов"
    mock_year_resp = MagicMock()
    mock_year_resp.text = "2010А"

    with (
        patch("src.router.ask_user_choice", new_callable=AsyncMock, return_value="2"),
        patch("src.router.send_safe", new_callable=AsyncMock),
        patch(
            "src.user_interactions.ask_user_raw",
            new_callable=AsyncMock,
            side_effect=[
                mock_name_resp,
                mock_year_resp,
                mock_name_resp,
                mock_year_resp,
            ],
        ),
    ):
        from src.router import _edit_guests

        await _edit_guests(mock_message, mock_state, base_reg, base_event, mock_app)

    mock_app.save_registration_guests.assert_awaited_once()
    saved_guests = mock_app.save_registration_guests.call_args[0][2]
    assert len(saved_guests) == 2
    assert all(g["name"] == "Иван Иванов" for g in saved_guests)
    assert all(g["price"] == 3000 for g in saved_guests)
    assert all(g["graduation_year"] == 2010 for g in saved_guests)
    assert all(g["class_letter"] == "А" for g in saved_guests)

    mock_app.save_event_log.assert_awaited_once()
    log_data = mock_app.save_event_log.call_args[0][1]
    assert log_data["action"] == "update_guests"
    assert log_data["guest_count"] == 2


# --- Test 2: _edit_guests removes all guests ---


@pytest.mark.asyncio
async def test_edit_guests_remove_all(
    mock_message, mock_state, mock_app, base_reg, base_event
):
    """_edit_guests removes all guests when user selects 0."""
    base_reg["guests"] = [{"name": "Гость", "price": 3000}]

    with (
        patch("src.router.ask_user_choice", new_callable=AsyncMock, return_value="0"),
        patch("src.router.send_safe", new_callable=AsyncMock) as mock_send,
    ):
        from src.router import _edit_guests

        await _edit_guests(mock_message, mock_state, base_reg, base_event, mock_app)

    mock_app.save_registration_guests.assert_awaited_once_with(
        12345, "aabbccddeeff00112233aabb", []
    )
    mock_send.assert_awaited_once()
    assert "убраны" in mock_send.call_args[0][1].lower()

    log_data = mock_app.save_event_log.call_args[0][1]
    assert log_data["action"] == "remove_all_guests"


# --- Test 3: _edit_guests updates existing guests ---


@pytest.mark.asyncio
async def test_edit_guests_update_existing(
    mock_message, mock_state, mock_app, base_reg, base_event
):
    """_edit_guests shows hints for existing names and saves updated list."""
    base_reg["guests"] = [
        {
            "name": "Старый Гость",
            "price": 2000,
            "graduation_year": 2005,
            "class_letter": "Б",
        }
    ]

    new_name_resp = MagicMock()
    new_name_resp.text = "Новый Гость"
    new_year_resp = MagicMock()
    new_year_resp.text = "2012В"

    ask_raw_calls = []
    responses = iter([new_name_resp, new_year_resp])

    async def _capture_ask_raw(chat_id, text, state, timeout=None):
        ask_raw_calls.append(text)
        return next(responses)

    mock_app.parse_graduation_year_and_class_letter.return_value = (2012, "В", None)

    with (
        patch("src.router.ask_user_choice", new_callable=AsyncMock, return_value="1"),
        patch("src.router.send_safe", new_callable=AsyncMock),
        patch("src.user_interactions.ask_user_raw", side_effect=_capture_ask_raw),
    ):
        from src.router import _edit_guests

        await _edit_guests(mock_message, mock_state, base_reg, base_event, mock_app)

    # Verify hint with old name was shown
    assert any("Старый Гость" in call for call in ask_raw_calls)

    saved_guests = mock_app.save_registration_guests.call_args[0][2]
    assert len(saved_guests) == 1
    assert saved_guests[0]["name"] == "Новый Гость"
    assert saved_guests[0]["graduation_year"] == 2012


# --- Test 4: _edit_guests calculates guest prices with early bird discount ---


@pytest.mark.asyncio
async def test_edit_guests_early_bird_price(
    mock_message, mock_state, mock_app, base_reg, base_event
):
    """Guest price reflects early bird discount via calculate_guest_price(year)."""
    base_event["early_bird_discount"] = 500
    base_event["early_bird_deadline"] = datetime.now() + timedelta(days=7)

    mock_app.calculate_guest_price.return_value = (3000, 2500)
    mock_app.parse_graduation_year_and_class_letter.return_value = (2012, "А", None)

    name_resp = MagicMock()
    name_resp.text = "Гость Один"
    year_resp = MagicMock()
    year_resp.text = "2012А"

    with (
        patch("src.router.ask_user_choice", new_callable=AsyncMock, return_value="1"),
        patch("src.router.send_safe", new_callable=AsyncMock) as mock_send,
        patch(
            "src.user_interactions.ask_user_raw",
            new_callable=AsyncMock,
            side_effect=[name_resp, year_resp],
        ),
    ):
        from src.router import _edit_guests

        await _edit_guests(mock_message, mock_state, base_reg, base_event, mock_app)

    mock_app.calculate_guest_price.assert_called_once_with(base_event, 2012, "GRADUATE")

    saved_guests = mock_app.save_registration_guests.call_args[0][2]
    assert saved_guests[0]["price"] == 3000
    assert saved_guests[0]["price_discounted"] == 2500
    assert saved_guests[0]["graduation_year"] == 2012

    # Verify summary mentions both prices and year
    summary_text = mock_send.call_args_list[-1][0][1]
    assert "3000₽" in summary_text
    assert "2500₽" in summary_text
    assert "2012А" in summary_text


# --- Test 5: manage_registrations displays guest info ---


@pytest.mark.asyncio
async def test_manage_registrations_shows_guests(mock_message, mock_state, mock_app):
    """manage_registrations shows guest names for a selected city registration."""
    reg = {
        "target_city": "Москва",
        "event_id": "aabbccddeeff00112233aabb",
        "full_name": "Тест Тестов",
        "graduation_year": 2010,
        "class_letter": "А",
        "guests": [
            {"name": "Гость Один", "price": 3000},
            {"name": "Гость Два", "price": 3000},
        ],
    }
    event = {
        "city": "Москва",
        "date_display": "21 Марта, Сб",
        "guests_enabled": True,
        "status": "upcoming",
    }

    mock_app.get_event_for_registration = AsyncMock(return_value=event)

    ask_choice_calls = []
    call_count = 0

    async def _fake_ask_choice(chat_id, text, choices, state, timeout=None):
        nonlocal call_count
        ask_choice_calls.append(text)
        call_count += 1
        if call_count == 1:
            return "aabbccddeeff00112233aabb"  # select event by event_id
        return "back"  # then go back

    with (
        patch("src.router.ask_user_choice", side_effect=_fake_ask_choice),
        patch("src.router.send_safe", new_callable=AsyncMock),
        patch("src.router.handle_registered_user", new_callable=AsyncMock),
    ):
        from src.router import manage_registrations

        await manage_registrations(mock_message, mock_state, [reg], mock_app)

    # Second ask_user_choice call shows city detail — should contain guest names
    city_detail_text = ask_choice_calls[1]
    assert "Гость Один" in city_detail_text
    assert "Гость Два" in city_detail_text
    assert "Гости (2)" in city_detail_text
