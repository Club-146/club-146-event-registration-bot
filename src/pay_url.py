"""Build personal 146.school /donate pay links for meetup contributions."""

from __future__ import annotations

from typing import Optional, Tuple, Union
from urllib.parse import urlencode


def split_full_name(full_name: str) -> Tuple[str, str]:
    """Map bot FIO (Фамилия Имя [Отчество]) to donate form (name, surname).

    Bot registration stores Russian order: first token is surname, rest is
    given name (+ optional patronymic). Donate form expects name/surname fields.
    Single token → name only, empty surname.
    """
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    surname = parts[0]
    name = " ".join(parts[1:])
    return name, surname


def build_pay_url(
    base_url: str,
    amount: Union[int, float],
    full_name: str = "",
    graduation_year: Optional[Union[int, str]] = None,
) -> str:
    """Return `{base}/donate?amount=…&frequency=once&name=…&surname=…&graduation_year=…`.

    Amount must be the same total shown to the user (early-bird + guests included).
    Cyrillic is URL-encoded via urlencode.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("payment site base_url is empty")

    name, surname = split_full_name(full_name)
    params = {
        "amount": str(int(amount)),
        "frequency": "once",
        "name": name,
        "surname": surname,
    }
    if graduation_year is not None and str(graduation_year).strip() != "":
        params["graduation_year"] = str(graduation_year).strip()

    return f"{base}/donate?{urlencode(params)}"
