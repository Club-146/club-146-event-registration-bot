"""Build personal 146.school /donate pay links for meetup contributions."""

from __future__ import annotations

from typing import Optional, Tuple, Union
from urllib.parse import urlencode

from src.name_order import parse_fio_line, split_for_donate_form


def split_full_name(full_name: str) -> Tuple[str, str]:
    """Map bot FIO (Фамилия Имя [Отчество]) to donate form (name, surname).

    Bot registration stores Russian order: first token is surname, rest is
    given name (+ optional patronymic). Donate form expects name/surname fields.
    Single token → name only, empty surname.

    Also corrects clear Western-order input (Имя Фамилия) via name_order heuristics
    so pay links do not land with inverted fields.
    """
    return split_for_donate_form(full_name)


def normalize_full_name(full_name: str) -> str:
    """Return storage form Фамилия Имя [Отчество], auto-fixing clear Western order."""
    g = parse_fio_line(full_name)
    return g.full_name or " ".join((full_name or "").split())


def build_pay_url(
    base_url: str,
    amount: Union[int, float],
    full_name: str = "",
    graduation_year: Optional[Union[int, str]] = None,
) -> str:
    """Return `{base}/donate?amount=…&frequency=once&no_upsell=1&name=…&surname=…&graduation_year=…`.

    Amount must be the same total shown to the user (early-bird + guests included).
    no_upsell=1 tells the site not to prompt converting the one-off event fee
    into a monthly school subscription.
    Cyrillic is URL-encoded via urlencode.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("payment site base_url is empty")

    name, surname = split_full_name(full_name)
    params = {
        "amount": str(int(amount)),
        "frequency": "once",
        # Event contribution stays one-off — do not upsell monthly support.
        "no_upsell": "1",
        "name": name,
        "surname": surname,
    }
    if graduation_year is not None and str(graduation_year).strip() != "":
        params["graduation_year"] = str(graduation_year).strip()

    return f"{base}/donate?{urlencode(params)}"
