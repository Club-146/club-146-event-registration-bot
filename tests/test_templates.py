"""Tests for per-event editable message templates."""

import pytest

from src.templates import (
    TEMPLATE_SPECS,
    get_template,
    render,
    validate_template,
)


def spec(key):
    return TEMPLATE_SPECS[key]


class TestValidation:
    def test_default_templates_are_valid(self):
        # Every shipped default must pass the same gate admins are held to.
        for key, s in TEMPLATE_SPECS.items():
            assert validate_template(s, s.default) == [], f"{key} default invalid"

    def test_accepts_good_edit(self):
        text = "<b>{price_label}: {regular_amount} руб.</b>\nДо встречи на {season} встрече!"
        assert validate_template(spec("payment_price_regular"), text) == []

    def test_rejects_empty(self):
        errors = validate_template(spec("payment_price_regular"), "   ")
        assert any("пустым" in e for e in errors)

    def test_rejects_unknown_placeholder(self):
        errors = validate_template(spec("payment_price_regular"), "Цена: {bogus}")
        assert any("Неизвестные подстановки" in e for e in errors)
        assert any("{bogus}" in e for e in errors)

    def test_rejects_missing_required_placeholders(self):
        errors = validate_template(
            spec("payment_price_regular"), "Цена: {regular_amount}"
        )
        assert any("Обязательные подстановки пропущены" in e for e in errors)

    def test_rejects_placeholder_valid_for_another_template(self):
        # {deadline} belongs to the early-bird template only.
        errors = validate_template(spec("payment_price_regular"), "До {deadline}")
        assert any("Неизвестные подстановки" in e for e in errors)

    def test_rejects_stray_brace(self):
        errors = validate_template(
            spec("payment_price_regular"), "Цена: {regular_amount"
        )
        assert any("скобка" in e for e in errors)

    def test_rejects_unsupported_html_tag(self):
        errors = validate_template(spec("payment_price_regular"), "<div>{season}</div>")
        assert any("не поддерживает теги" in e for e in errors)

    def test_rejects_unclosed_tag(self):
        errors = validate_template(spec("payment_price_regular"), "<b>{season}")
        assert any("Незакрытые теги" in e for e in errors)

    def test_rejects_stray_closing_tag(self):
        errors = validate_template(spec("payment_price_regular"), "{season}</b>")
        assert any("Лишние закрывающие теги" in e for e in errors)

    def test_escaped_lt_is_fine(self):
        text = "{price_label}: {regular_amount} &lt; 2000, {season}"
        assert validate_template(spec("payment_price_regular"), text) == []

    def test_nested_tags_are_fine(self):
        text = "<b>{price_label}: <b>{regular_amount}</b> — {season}</b>"
        assert validate_template(spec("payment_price_regular"), text) == []

    @pytest.mark.parametrize(
        "text",
        [
            "<b><i>{price_label}</b> {regular_amount} {season}",
            "<b/>{price_label} {regular_amount} {season}",
            '<b onclick="x">{price_label}</b> {regular_amount} {season}',
            "<!--x-->{price_label} {regular_amount} {season}",
            "{price_label} &bogus; {regular_amount} {season}",
            "{price_label} < {regular_amount} {season}",
        ],
    )
    def test_rejects_telegram_invalid_html(self, text):
        assert validate_template(spec("payment_price_regular"), text)


class TestRender:
    def test_default_used_when_event_has_no_templates(self):
        out = render(
            {},
            "payment_price_regular",
            {
                "price_label": "Минимальный взнос",
                "regular_amount": 1500,
                "season": "летней",
            },
        )
        assert "<b>Минимальный взнос: 1500 руб.</b>" in out
        assert "на летней встрече" in out

    def test_admin_override_is_used(self):
        event = {
            "templates": {
                "payment_price_regular": "{price_label}: {regular_amount}, {season}"
            }
        }
        out = render(
            event,
            "payment_price_regular",
            {"price_label": "x", "regular_amount": 999, "season": "летней"},
        )
        assert out == "x: 999, летней"

    def test_context_values_are_html_escaped(self):
        event = {
            "templates": {
                "payment_price_regular": "{price_label}: {regular_amount}, {season}"
            }
        }
        out = render(
            event,
            "payment_price_regular",
            {"price_label": "A < B & C", "regular_amount": 999, "season": "летней"},
        )
        assert out == "A &lt; B &amp; C: 999, летней"

    def test_invalid_stored_template_falls_back_to_default(self):
        # A bad edit must never brick the payment flow.
        event = {"templates": {"payment_price_regular": "<div>broken {bogus}"}}
        out = render(
            event,
            "payment_price_regular",
            {"price_label": "Взнос", "regular_amount": 1500, "season": "летней"},
        )
        assert "<b>Взнос: 1500 руб.</b>" in out
        assert "broken" not in out

    def test_missing_context_key_leaves_placeholder_literal(self):
        event = {
            "templates": {
                "payment_price_regular": "{price_label} {season} {regular_amount}"
            }
        }
        out = render(event, "payment_price_regular", {"season": "летней"})
        assert out == "{price_label} летней {regular_amount}"

    def test_render_does_not_evaluate_format_expressions(self):
        # str.format on admin text would expose attribute access. Attribute
        # syntax isn't a placeholder, so it is rejected as a stray brace and
        # never evaluated.
        event = {"templates": {"payment_price_regular": "{regular_amount.__class__}"}}
        out = render(
            event,
            "payment_price_regular",
            {"price_label": "Взнос", "regular_amount": 1500, "season": "летней"},
        )
        assert "class" not in out
        assert "<b>Взнос: 1500 руб.</b>" in out  # fell back to the default

    def test_dunder_access_is_rejected_at_edit_time(self):
        errors = validate_template(
            spec("payment_price_regular"), "{regular_amount.__class__}"
        )
        assert errors, "attribute-access syntax must not be accepted"

    def test_get_template_returns_default_for_empty_string(self):
        event = {"templates": {"payment_price_regular": ""}}
        assert (
            get_template(event, "payment_price_regular")
            == spec("payment_price_regular").default
        )

    def test_none_event_uses_default(self):
        assert get_template(None, "payment_intro") == spec("payment_intro").default


class TestSpecs:
    @pytest.mark.parametrize("key", sorted(TEMPLATE_SPECS))
    def test_default_only_uses_declared_placeholders(self, key):
        import re

        s = TEMPLATE_SPECS[key]
        used = set(re.findall(r"\{(\w+)\}", s.default))
        assert used <= s.placeholders, (
            f"{key} default uses undeclared {used - s.placeholders}"
        )
