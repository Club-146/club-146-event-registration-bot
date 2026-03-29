"""Tests for pure helper functions in routers."""

from datetime import datetime

from src.routers._events_helpers import (
    _suggest_event_name,
    _format_pricing,
    _format_event_summary,
    _make_date_display,
    SEASON_NAMES,
    MONTH_NAMES_RU,
    DAY_OF_WEEK_RU,
)
from src.routers.admin import _format_graduate_type
from src.routers.crm import apply_message_templates
from src.routers.stats import get_median


class TestSuggestEventName:
    def test_spring(self):
        result = _suggest_event_name("Москва", datetime(2026, 3, 21))
        assert result == "Москва (Весенняя встреча 2026)"

    def test_summer(self):
        result = _suggest_event_name("Пермь", datetime(2025, 7, 15))
        assert result == "Пермь (Летняя встреча 2025)"

    def test_autumn(self):
        result = _suggest_event_name("Москва", datetime(2026, 10, 1))
        assert result == "Москва (Осенняя встреча 2026)"

    def test_winter_december(self):
        result = _suggest_event_name("Пермь", datetime(2026, 12, 20))
        assert result == "Пермь (Зимняя встреча 2026)"

    def test_winter_january(self):
        result = _suggest_event_name("Москва", datetime(2027, 1, 15))
        assert result == "Москва (Зимняя встреча 2027)"


class TestFormatPricing:
    def test_free(self):
        result = _format_pricing({"pricing_type": "free"})
        assert result == "Бесплатно"

    def test_formula(self):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1000,
            "price_formula_rate": 200,
            "price_formula_reference_year": 2026,
            "price_formula_step": 1,
        }
        result = _format_pricing(event)
        assert "1000" in result
        assert "200" in result

    def test_formula_with_step(self):
        event = {
            "pricing_type": "formula",
            "price_formula_base": 1500,
            "price_formula_rate": 500,
            "price_formula_reference_year": 2025,
            "price_formula_step": 3,
        }
        result = _format_pricing(event)
        assert "÷ 3" in result

    def test_fixed_by_year(self):
        result = _format_pricing({"pricing_type": "fixed_by_year"})
        assert "Фиксированная" in result

    def test_unknown(self):
        result = _format_pricing({"pricing_type": "whatever"})
        assert "Неизвестно" in result


class TestFormatEventSummary:
    def test_basic_event(self):
        event = {
            "name": "Москва (Весенняя встреча 2026)",
            "city": "Москва",
            "date_display": "21 Марта, Сб",
            "time_display": "18:00",
            "venue": "Лофт",
            "address": "ул. Тест",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
        }
        result = _format_event_summary(event)
        assert "Москва" in result
        assert "21 Марта" in result
        assert "Лофт" in result
        assert "Бесплатно" in result

    def test_with_registrations(self):
        event = {
            "name": "Test",
            "city": "Пермь",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
        }
        result = _format_event_summary(event, reg_count=15)
        assert "15" in result

    def test_free_for_types(self):
        event = {
            "name": "Test",
            "city": "Пермь",
            "pricing_type": "formula",
            "price_formula_base": 500,
            "price_formula_rate": 100,
            "price_formula_reference_year": 2026,
            "status": "upcoming",
            "enabled": True,
            "free_for_types": ["TEACHER", "ORGANIZER"],
        }
        result = _format_event_summary(event)
        assert "Учителя" in result
        assert "Организаторы" in result

    def test_early_bird(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
            "early_bird_discount": 500,
            "early_bird_deadline": datetime(2026, 3, 18),
        }
        result = _format_event_summary(event)
        assert "500" in result
        assert "18.03.2026" in result

    def test_guests_enabled(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
            "guests_enabled": True,
            "max_guests_per_person": 2,
            "guest_price_minimum": 1000,
        }
        result = _format_event_summary(event)
        assert "до 2 чел" in result
        assert "мин. 1000" in result

    def test_guests_no_minimum(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
            "guests_enabled": True,
            "max_guests_per_person": 3,
            "guest_price_minimum": 0,
        }
        result = _format_event_summary(event)
        assert "как у регистранта" in result

    def test_paused_status(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": False,
        }
        result = _format_event_summary(event)
        assert "приостановлена" in result

    def test_archived_status(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "archived",
        }
        result = _format_event_summary(event)
        assert "архиве" in result

    def test_no_venue(self):
        event = {
            "name": "Test",
            "city": "Москва",
            "pricing_type": "free",
            "status": "upcoming",
            "enabled": True,
            "venue": None,
            "address": None,
        }
        result = _format_event_summary(event)
        assert "Не указано" in result


class TestMakeDateDisplay:
    def test_saturday(self):
        result = _make_date_display(datetime(2026, 3, 21))
        assert result == "21 Марта, Сб"

    def test_another_day(self):
        result = _make_date_display(datetime(2026, 1, 5))
        assert "Января" in result


class TestFormatGraduateType:
    def test_graduate(self):
        assert _format_graduate_type("GRADUATE") == "Выпускник"

    def test_teacher(self):
        assert _format_graduate_type("TEACHER") == "Учитель"

    def test_plural(self):
        assert _format_graduate_type("GRADUATE", plural=True) == "Выпускники"

    def test_lowercase(self):
        assert _format_graduate_type("graduate") == "Выпускник"


class TestApplyMessageTemplates:
    def test_basic_substitution(self):
        template = "Привет, {name}! Встреча в {city}."
        user_data = {"full_name": "Иван", "target_city": "Москва"}
        result = apply_message_templates(template, user_data)
        assert result == "Привет, Иван! Встреча в Москва."

    def test_with_event(self):
        template = "Место: {venue}, {address}. Время: {time}. Дата: {date}."
        user_data = {"full_name": "Иван"}
        event = {
            "venue": "Лофт",
            "address": "ул. Тест",
            "time_display": "18:00",
            "date_display": "21 Марта",
        }
        result = apply_message_templates(template, user_data, event)
        assert "Лофт" in result
        assert "ул. Тест" in result
        assert "18:00" in result

    def test_without_event_defaults(self):
        template = "{venue} - {address} - {time} - {date}"
        user_data = {"full_name": "Иван"}
        result = apply_message_templates(template, user_data)
        assert result.count("Уточняется") == 4

    def test_with_event_missing_fields(self):
        template = "{venue} - {address}"
        user_data = {"full_name": "Иван"}
        event = {"venue": None, "address": None}
        result = apply_message_templates(template, user_data, event)
        assert result.count("Уточняется") == 2

    def test_year_and_class(self):
        template = "Выпуск: {year} {class}"
        user_data = {
            "full_name": "Test",
            "graduation_year": 2010,
            "class_letter": "А",
        }
        result = apply_message_templates(template, user_data)
        assert "2010" in result
        assert "А" in result

    def test_city_padezh_from_event(self):
        template = "Встреча в {city_padezh}"
        user_data = {"full_name": "Test"}
        event = {"city_prepositional": "Москве"}
        result = apply_message_templates(template, user_data, event)
        assert "Москве" in result


class TestGetMedian:
    def test_odd_count(self):
        assert get_median([1, 3, 5]) == 3

    def test_even_count(self):
        assert get_median([1, 2, 3, 4]) == 3

    def test_empty(self):
        assert get_median([]) == 0

    def test_single(self):
        assert get_median([42]) == 42

    def test_unsorted(self):
        assert get_median([5, 1, 3]) == 3


class TestSeasonNames:
    def test_all_seasons_present(self):
        assert len(SEASON_NAMES) == 4

    def test_month_names(self):
        assert MONTH_NAMES_RU[1] == "Января"
        assert MONTH_NAMES_RU[12] == "Декабря"

    def test_day_of_week(self):
        assert DAY_OF_WEEK_RU[5] == "Сб"
        assert DAY_OF_WEEK_RU[6] == "Вс"
