"""Unit tests for personal site pay-link builder."""

from urllib.parse import parse_qs, urlparse

from src.pay_url import build_pay_url, split_full_name
from src.templates import render


class TestSplitFullName:
    def test_russian_fio_surname_first(self):
        # Bot stores "Фамилия Имя" — donate form wants name / surname fields.
        assert split_full_name("Иванов Иван") == ("Иван", "Иванов")

    def test_with_patronymic(self):
        assert split_full_name("Иванов Иван Иванович") == ("Иван Иванович", "Иванов")

    def test_single_token(self):
        assert split_full_name("Иван") == ("Иван", "")

    def test_empty(self):
        assert split_full_name("") == ("", "")
        assert split_full_name("   ") == ("", "")


class TestBuildPayUrl:
    def test_encodes_cyrillic_and_amount(self):
        url = build_pay_url(
            "https://staging.146.school.calmmage.com",
            4500,
            full_name="Тестов Тест",
            graduation_year=2005,
        )
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "staging.146.school.calmmage.com"
        assert parsed.path == "/donate"
        q = parse_qs(parsed.query)
        assert q["amount"] == ["4500"]
        assert q["frequency"] == ["once"]
        assert q["name"] == ["Тест"]
        assert q["surname"] == ["Тестов"]
        assert q["graduation_year"] == ["2005"]
        # Raw query must percent-encode non-ASCII
        assert "%" in parsed.query

    def test_strips_trailing_slash_on_base(self):
        url = build_pay_url("https://146.school/", 146, full_name="А Б")
        assert url.startswith("https://146.school/donate?")

    def test_omits_empty_year(self):
        url = build_pay_url("https://example.test", 2000, full_name="А Б")
        q = parse_qs(urlparse(url).query)
        assert "graduation_year" not in q


class TestPaymentDetailsTemplate:
    def test_default_contains_pay_url_and_screenshot(self):
        out = render(
            {},
            "payment_details",
            {
                "pay_url": "https://staging.146.school.calmmage.com/donate?amount=4500&frequency=once",
                "phone": "+7123",
                "name": "Маша",
            },
        )
        assert "4500" in out
        assert "frequency=once" in out or "donate?" in out
        assert "+7123" in out
        assert "Маша" in out
        assert "скриншот" in out.lower()
