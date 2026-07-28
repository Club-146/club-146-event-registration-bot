"""Parse human-entered ruble amounts from admin/payment chat input."""

from __future__ import annotations

import re
from typing import Any, Optional

# Trailing currency markers: "руб", "руб.", "рублей", "р", "₽", "RUB", ...
_CURRENCY_SUFFIX = re.compile(
    r"(?i)\s*(?:"
    r"руб(?:\.|лей|ля|ль)?"
    r"|р\.?"
    r"|₽"
    r"|rub(?:les?)?"
    r")\s*$"
)
# Spaces used as thousand separators (regular, NBSP, narrow NBSP)
_THOUSAND_SPACES = re.compile(r"[\s\u00a0\u202f]+")


def message_text(value: Any) -> Optional[str]:
    """Extract text from an aiogram Message, a plain str, or None.

    ``ask_user_raw`` returns a Message; tests often mock a bare string.
    ``str(Message)`` is the full object dump and must never be parsed as input.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    text = getattr(value, "text", None)
    if text is None:
        return None
    return str(text)


def parse_rubles_amount(raw: Any) -> Optional[int]:
    """Parse a ruble amount into a non-negative integer.

    Accepted examples:
      - ``1900``
      - ``1900 руб`` / ``1900р`` / ``1900 ₽``
      - ``1900,00`` / ``1900.00``
      - ``1 900`` / ``1 900,00 руб``
      - ``1.900,00`` (EU) / ``1,900.00`` (US)

    Returns None if the value cannot be parsed or is negative.
    Fractional rubles are rounded to the nearest integer.
    """
    text = message_text(raw)
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    text = _CURRENCY_SUFFIX.sub("", text).strip()
    text = _THOUSAND_SPACES.sub("", text)
    if not text:
        return None

    text = _normalize_decimal_separators(text)
    if text is None:
        return None

    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0 or value != value:  # NaN
        return None
    return int(round(value))


def _normalize_decimal_separators(text: str) -> Optional[str]:
    """Turn a digit string with ,/. thousand/decimal separators into a float-ready form."""
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # European: 1.900,00
            text = text.replace(".", "").replace(",", ".")
        else:
            # US: 1,900.00
            text = text.replace(",", "")
        return text if _is_float_literal(text) else None

    if "," in text:
        parts = text.split(",")
        if len(parts) == 2 and parts[1].isdigit() and 1 <= len(parts[1]) <= 2:
            text = f"{parts[0]}.{parts[1]}"
        else:
            text = text.replace(",", "")
        return text if _is_float_literal(text) else None

    if "." in text:
        parts = text.split(".")
        if not all(p.isdigit() for p in parts if p != ""):
            # allow leading empty only for ".5" — we don't need that for rubles
            return text if _is_float_literal(text) else None
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            # decimal: 1900.00
            return text if _is_float_literal(text) else None
        if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]) and parts[0].isdigit():
            # thousand separators: 1.900 or 1.900.000
            text = "".join(parts)
            return text if text.isdigit() else None
        return text if _is_float_literal(text) else None

    return text if _is_float_literal(text) else None


def _is_float_literal(text: str) -> bool:
    if not text or text in {".", "-", "+"}:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False
