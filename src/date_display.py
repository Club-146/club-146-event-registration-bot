"""Russian date rendering for event cards and menus.

Lives in its own module because two very different layers need it: the admin
event editor (routers) and the website-event overlay (``website_db``). Importing
the routers package from the data layer would invert the dependency, and
duplicating the month table would let the two drift — which is exactly the class
of bug this whole change is about.
"""

from datetime import datetime

MONTH_NAMES_RU = {
    1: "Января",
    2: "Февраля",
    3: "Марта",
    4: "Апреля",
    5: "Мая",
    6: "Июня",
    7: "Июля",
    8: "Августа",
    9: "Сентября",
    10: "Октября",
    11: "Ноября",
    12: "Декабря",
}

DAY_OF_WEEK_RU = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


def make_date_display(dt: datetime) -> str:
    """`datetime(2026, 8, 1)` -> `"1 Августа, Сб"`."""
    day_name = DAY_OF_WEEK_RU.get(dt.weekday(), "")
    month_name = MONTH_NAMES_RU.get(dt.month, "")
    return f"{dt.day} {month_name}, {day_name}"


def make_time_display(dt: datetime) -> str:
    """`datetime(2026, 8, 1, 18, 0)` -> `"18:00"`.

    The bot's own ``time_display`` is free text (operators write things like
    "18:00-00:00"), so this is only used when the website supplies the time and
    therefore owns it.
    """
    return f"{dt.hour:02d}:{dt.minute:02d}"
