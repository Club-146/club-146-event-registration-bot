"""Tests for App validation methods and pure functions."""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from src.app import (
    App,
    GraduateType,
    EventStatus,
    PricingType,
    GRADUATE_TYPE_MAP,
    GRADUATE_TYPE_MAP_PLURAL,
    PAYMENT_STATUS_MAP,
    CITY_PREPOSITIONAL_MAP,
    FeedbackData,
    AppSettings,
)


@pytest.fixture
def app():
    mock_db = MagicMock()
    mock_db.get_collection.return_value = MagicMock()
    with patch("src.app.get_database", return_value=mock_db):
        return App(
            telegram_bot_token="mock_token",
            spreadsheet_id="mock_sheet",
            payment_phone_number="123",
            payment_name="Test",
        )


class TestValidateFullName:
    def test_valid_name(self, app):
        valid, err = app.validate_full_name("Иванов Иван")
        assert valid is True
        assert err == ""

    def test_single_word(self, app):
        valid, err = app.validate_full_name("Иванов")
        assert valid is False
        assert "имя и фамилию" in err

    def test_none(self, app):
        valid, err = app.validate_full_name(None)
        assert valid is False

    def test_latin_characters(self, app):
        valid, err = app.validate_full_name("Ivan Ivanov")
        assert valid is False
        assert "По-русски" in err

    def test_hyphenated_name(self, app):
        valid, err = app.validate_full_name("Иванова-Петрова Мария")
        assert valid is True

    def test_empty_string(self, app):
        valid, err = app.validate_full_name("   ")
        assert valid is False


class TestValidateGraduationYear:
    def test_valid_year(self, app):
        valid, err = app.validate_graduation_year(2010)
        assert valid is True

    def test_too_early(self, app):
        valid, err = app.validate_graduation_year(1990)
        assert valid is False
        assert "1995" in err

    def test_far_future(self, app):
        valid, err = app.validate_graduation_year(2040)
        assert valid is False

    def test_next_year(self, app):
        valid, err = app.validate_graduation_year(2027)
        assert valid is False
        assert "выпускников" in err

    def test_boundary_1995(self, app):
        valid, err = app.validate_graduation_year(1995)
        assert valid is True


class TestValidateClassLetter:
    def test_valid_letter(self, app):
        valid, err = app.validate_class_letter("А")
        assert valid is True

    def test_empty(self, app):
        valid, err = app.validate_class_letter("")
        assert valid is False

    def test_latin_letter(self, app):
        valid, err = app.validate_class_letter("A")
        assert valid is False
        assert "русском" in err

    def test_multiple_letters(self, app):
        valid, err = app.validate_class_letter("АБ")
        assert valid is False
        assert "одним символом" in err


class TestParseGraduationYearAndClassLetter:
    def test_space_separated(self, app):
        year, letter, err = app.parse_graduation_year_and_class_letter("2010 Б")
        assert year == 2010
        assert letter == "Б"
        assert err is None

    def test_no_space(self, app):
        year, letter, err = app.parse_graduation_year_and_class_letter("2010Б")
        assert year == 2010
        assert letter == "Б"
        assert err is None

    def test_year_only(self, app):
        year, letter, err = app.parse_graduation_year_and_class_letter("2010")
        assert year == 2010
        assert letter == ""
        assert "букву класса" in err

    def test_invalid_input(self, app):
        year, letter, err = app.parse_graduation_year_and_class_letter("abc")
        assert year is None
        assert err is not None

    def test_invalid_year_in_combo(self, app):
        year, letter, err = app.parse_graduation_year_and_class_letter("1990 Б")
        assert year is None
        assert err is not None


class TestCalculateEventPayment:
    def test_free_event(self, app):
        event = {"pricing_type": "free"}
        result = app.calculate_event_payment(event, 2010)
        assert result == (0, 0, 0, 0)

    def test_teacher_free(self, app):
        event = {
            "pricing_type": "formula",
            "free_for_types": ["TEACHER"],
        }
        result = app.calculate_event_payment(event, 2010, "TEACHER")
        assert result == (0, 0, 0, 0)

    def test_organizer_free(self, app):
        event = {
            "pricing_type": "formula",
            "free_for_types": ["ORGANIZER"],
        }
        result = app.calculate_event_payment(event, 2010, "ORGANIZER")
        assert result == (0, 0, 0, 0)

    def test_formula_pricing(self, app):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "price_formula_step": 1,
            "free_for_types": [],
        }
        # 2020 grad: 6 years since, amount = 1000 + 200 * 6 = 2200
        regular, discount, discounted, formula = app.calculate_event_payment(
            event, 2020
        )
        assert formula == 2200
        assert regular == 2200

    def test_formula_with_step(self, app):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1500,
            "price_formula_rate": 500,
            "price_formula_reference_year": 2025,
            "price_formula_step": 3,
            "free_for_types": [],
        }
        # 2019 grad: 6 years, 6//3=2 steps, amount = 1500 + 500*2 = 2500
        regular, discount, discounted, formula = app.calculate_event_payment(
            event, 2019
        )
        assert formula == 2500

    def test_formula_with_early_bird(self, app):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "price_formula_step": 1,
            "free_for_types": [],
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2099, 1, 1),
        }
        regular, discount, discounted, formula = app.calculate_event_payment(
            event, 2025
        )
        assert discount == 500
        assert discounted == regular - 500

    def test_formula_expired_early_bird(self, app):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "price_formula_step": 1,
            "free_for_types": [],
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2020, 1, 1),
        }
        regular, discount, discounted, formula = app.calculate_event_payment(
            event, 2025
        )
        assert discount == 0
        assert discounted == regular

    def test_fixed_by_year(self, app):
        event = {
            "pricing_type": "fixed_by_year",
            "year_price_map": {"2020": 1400, "2019": 1500},
            "free_for_types": [],
        }
        result = app.calculate_event_payment(event, 2020)
        assert result == (1400, 0, 1400, 1400)

    def test_fixed_by_year_missing(self, app):
        event = {
            "pricing_type": "fixed_by_year",
            "year_price_map": {"2020": 1400, "2019": 1500},
            "free_for_types": [],
        }
        # Year not in map -> should use max
        result = app.calculate_event_payment(event, 1995)
        assert result[0] == 1500

    def test_non_graduate_formula(self, app):
        """Non-graduate uses formula with ref_years=12 when no guest_price_minimum."""
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1500,
            "price_formula_rate": 500,
            "price_formula_reference_year": 2025,
            "price_formula_step": 1,
            "free_for_types": [],
        }
        # base + rate * 15 = 1500 + 500*15 = 9000
        result = app.calculate_event_payment(event, 2020, "NON_GRADUATE")
        assert result == (9000, 0, 9000, 9000)

    def test_non_graduate_with_guest_minimum(self, app):
        """Non-graduate uses guest_price_minimum when set."""
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1500,
            "price_formula_rate": 500,
            "price_formula_reference_year": 2025,
            "price_formula_step": 1,
            "free_for_types": [],
            "guest_price_minimum": 3000,
        }
        result = app.calculate_event_payment(event, 2020, "NON_GRADUATE")
        assert result == (3000, 0, 3000, 3000)

    def test_non_graduate_early_bird(self, app):
        """Non-graduate gets early bird discount."""
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1500,
            "price_formula_rate": 500,
            "price_formula_reference_year": 2025,
            "price_formula_step": 3,
            "free_for_types": [],
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2099, 1, 1),
        }
        # base + rate * (15 // 3) = 1500 + 500*5 = 4000, early bird → 3500
        result = app.calculate_event_payment(event, 2020, "NON_GRADUATE")
        assert result == (4000, 500, 3500, 4000)

    def test_non_graduate_low_base(self, app):
        """Non-graduate with low base formula."""
        event = {
            "pricing_type": "formula",
            "price_formula_base": 500,
            "price_formula_rate": 100,
            "price_formula_reference_year": 2025,
            "price_formula_step": 1,
            "free_for_types": [],
        }
        # base + rate * 15 = 500 + 100*15 = 2000
        result = app.calculate_event_payment(event, 2020, "NON_GRADUATE")
        assert result == (2000, 0, 2000, 2000)

    def test_old_graduate_no_cap(self, app):
        """No cap — price grows with years_since graduation."""
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "price_formula_step": 1,
            "free_for_types": [],
        }
        # 1995 grad: 31 years, formula=1000+200*31=7200, no cap
        regular, discount, discounted, formula = app.calculate_event_payment(
            event, 1995
        )
        assert regular == 7200
        assert formula == 7200

    def test_unknown_pricing_type(self, app):
        event = {"pricing_type": "unknown", "free_for_types": []}
        result = app.calculate_event_payment(event, 2020)
        assert result == (0, 0, 0, 0)


class TestCalculateGuestPrice:
    def test_basic(self, app):
        event = {"guest_price_minimum": 1000}
        assert app.calculate_guest_price(event, 2000) == (2000, 2000)

    def test_minimum_applied(self, app):
        event = {"guest_price_minimum": 3000}
        assert app.calculate_guest_price(event, 2000) == (3000, 3000)

    def test_no_minimum(self, app):
        event = {}
        assert app.calculate_guest_price(event, 1500) == (1500, 1500)

    def test_early_bird_applied(self, app):
        event = {
            "guest_price_minimum": 1000,
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2099, 1, 1),
        }
        # max(1000, 2000) = 2000 regular, minus 500 = 1500 discounted
        assert app.calculate_guest_price(event, 2000) == (2000, 1500)

    def test_early_bird_expired(self, app):
        event = {
            "guest_price_minimum": 1000,
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2020, 1, 1),
        }
        assert app.calculate_guest_price(event, 2000) == (2000, 2000)

    def test_early_bird_floor_zero(self, app):
        event = {
            "early_bird_discount": 9999,
            "early_bird_deadline": datetime(2099, 1, 1),
        }
        assert app.calculate_guest_price(event, 500) == (500, 0)


class TestIsEventPassed:
    def test_past_event(self, app):
        event = {"date": datetime(2020, 1, 1)}
        assert app.is_event_passed(event) is True

    def test_future_event(self, app):
        event = {"date": datetime(2099, 1, 1)}
        assert app.is_event_passed(event) is False


class TestEnums:
    def test_event_status_values(self):
        assert EventStatus.UPCOMING == "upcoming"
        assert EventStatus.ARCHIVED == "archived"

    def test_pricing_type_values(self):
        assert PricingType.FREE == "free"
        assert PricingType.FORMULA == "formula"

    def test_graduate_type_map(self):
        assert GRADUATE_TYPE_MAP[GraduateType.GRADUATE.value] == "Выпускник"
        assert GRADUATE_TYPE_MAP[GraduateType.TEACHER.value] == "Учитель"

    def test_graduate_type_map_plural(self):
        assert GRADUATE_TYPE_MAP_PLURAL[GraduateType.GRADUATE.value] == "Выпускники"

    def test_payment_status_map(self):
        assert PAYMENT_STATUS_MAP["confirmed"] == "Оплачено"
        assert PAYMENT_STATUS_MAP["pending"] == "На проверке"
        assert PAYMENT_STATUS_MAP[None] == "Не оплачено"

    def test_city_prepositional_map(self):
        assert CITY_PREPOSITIONAL_MAP["Москва"] == "Москве"
        assert CITY_PREPOSITIONAL_MAP["Пермь"] == "Перми"


class TestFeedbackData:
    def test_create_minimal(self):
        fb = FeedbackData(user_id=123)
        assert fb.user_id == 123
        assert fb.attended is None

    def test_create_full(self):
        fb = FeedbackData(
            user_id=123,
            username="test",
            city="Москва",
            attended=True,
            recommendation_level="10",
        )
        assert fb.city == "Москва"
        assert fb.attended is True

    def test_extra_fields_ignored(self):
        fb = FeedbackData(user_id=123, extra_field="ignored")
        assert fb.user_id == 123


class TestAppSettings:
    def test_create(self):
        settings = AppSettings(
            telegram_bot_token="token",
            events_chat_id=-123,
            payment_phone_number="123",
            payment_name="Test",
        )
        assert settings.telegram_bot_token.get_secret_value() == "token"
        assert settings.events_chat_id == -123
