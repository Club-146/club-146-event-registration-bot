"""Tests for human-entered ruble amount parsing."""

from datetime import datetime

import pytest
from aiogram.types import Chat, Message

from src.amount_parse import message_text, parse_rubles_amount


def _msg(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=1, type="private"),
        text=text,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1900", 1900),
        ("1900 руб", 1900),
        ("1900 руб.", 1900),
        ("1900р", 1900),
        ("1900 ₽", 1900),
        ("1900,00", 1900),
        ("1900.00", 1900),
        ("1 900", 1900),
        ("1 900,00", 1900),
        ("1 900 руб", 1900),
        ("1.900,00", 1900),
        ("1,900.00", 1900),
        ("  2500  ", 2500),
        ("2500.6", 2501),  # rounds half-up-ish via float+round
        ("0", 0),
        ("0,00 руб", 0),
    ],
)
def test_parse_rubles_amount_accepted(raw, expected):
    assert parse_rubles_amount(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "руб",
        "abc",
        "-100",
        "сто",
        None,
    ],
)
def test_parse_rubles_amount_rejected(raw):
    assert parse_rubles_amount(raw) is None


def test_parse_rubles_amount_from_message():
    """Regression: str(Message) is a dump — must use .text."""
    msg = _msg("1900")
    assert parse_rubles_amount(msg) == 1900
    assert parse_rubles_amount(_msg("1900 руб")) == 1900
    assert parse_rubles_amount(_msg("1900,00")) == 1900
    # str(Message) itself must not parse as a valid amount
    assert parse_rubles_amount(str(msg)) is None


def test_message_text_handles_message_and_str():
    assert message_text(_msg("hello")) == "hello"
    assert message_text("plain") == "plain"
    assert message_text(None) is None
    assert message_text(_msg("")) == ""
