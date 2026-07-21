"""Deterministic personalized entry cards for confirmed registrations."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from botspot.core.dependency_manager import get_dependency_manager
from botspot.utils.internal import get_logger


logger = get_logger()

CARD_SIZE = (1200, 760)
_TICKET_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{7,39}$")


class TicketCardError(ValueError):
    """Raised when a registration must not receive an entry card."""


class TicketCardData(BaseModel):
    """Validated display data; no payment-provider secrets are rendered."""

    full_name: str = Field(min_length=1, max_length=160)
    graduate_label: str = Field(min_length=1, max_length=80)
    event_name: str = Field(min_length=1, max_length=160)
    event_date: str = Field(min_length=1, max_length=80)
    event_time: str = Field(min_length=1, max_length=40)
    venue: str = Field(min_length=1, max_length=160)
    address: str = Field(min_length=1, max_length=240)
    guest_names: list[str] = Field(default_factory=list, max_length=3)
    ticket_code: str = Field(pattern=_TICKET_CODE_PATTERN.pattern)


class TicketCardArtifact(BaseModel):
    """Telegram-ready deterministic PNG plus its user-facing metadata."""

    image: bytes
    filename: str
    caption: str
    ticket_code: str


def is_ticket_unlocked(registration: Mapping[str, Any]) -> bool:
    """The current bot contract: only exact confirmed status unlocks entry."""

    return registration.get("payment_status") == "confirmed"


def _clean_text(value: Any, default: str, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    return (text or default)[:max_length]


def _object_id(value: Any, field: str) -> str:
    text = _clean_text(value, "", 160)
    if not text:
        raise TicketCardError(f"Missing {field}")
    return text


def _event_date_display(event: Mapping[str, Any]) -> str:
    displayed = _clean_text(event.get("date_display"), "", 80)
    if displayed:
        return displayed

    value = event.get("date")
    if isinstance(value, datetime | date):
        return value.strftime("%d.%m.%Y")
    return "Дата уточняется"


def _graduate_label(registration: Mapping[str, Any]) -> str:
    graduate_type = registration.get("graduate_type", "GRADUATE")
    if graduate_type == "TEACHER":
        return "Учитель"
    if graduate_type == "ORGANIZER":
        return "Организатор"
    if graduate_type == "NON_GRADUATE":
        return "Друг школы"

    year = _clean_text(registration.get("graduation_year"), "", 8)
    class_letter = _clean_text(registration.get("class_letter"), "", 12)
    if year and class_letter:
        return f"Выпуск {year} • класс {class_letter}"
    if year:
        return f"Выпуск {year}"
    return "Выпускник школы 146"


def _guest_names(registration: Mapping[str, Any]) -> list[str]:
    raw_guests = registration.get("guests") or []
    if not isinstance(raw_guests, list):
        raise TicketCardError("Registration guests must be a list")
    if len(raw_guests) > 3:
        raise TicketCardError("Entry card supports at most three registered guests")

    names: list[str] = []
    for guest in raw_guests:
        if not isinstance(guest, Mapping):
            raise TicketCardError("Each guest must be a named registration record")
        name = _clean_text(guest.get("name"), "", 80)
        if not name:
            raise TicketCardError("Each guest must have a name")
        names.append(name)
    return names


def _ticket_code(registration: Mapping[str, Any], event_id: str) -> str:
    """Use a future website code when present; otherwise make a stable visual ID.

    The derived fallback is deliberately only a visual registration reference,
    not a cryptographic proof. A website-issued opaque code can be written to
    ``ticket_code`` later without changing rendering or `/status` delivery.
    """

    explicit = registration.get("ticket_code")
    if explicit is not None:
        code = _clean_text(explicit, "", 40)
        if not _TICKET_CODE_PATTERN.fullmatch(code):
            raise TicketCardError("Invalid website-issued ticket_code")
        return code

    registration_id = _object_id(registration.get("_id"), "registration _id")
    user_id = _object_id(registration.get("user_id"), "Telegram user_id")
    payload = f"ticket-card-v1|{registration_id}|{event_id}|{user_id}".encode()
    digest = hashlib.sha256(payload).hexdigest().upper()[:12]
    return f"146-{digest[:4]}-{digest[4:8]}-{digest[8:]}"


def build_ticket_card_data(
    registration: Mapping[str, Any], event: Mapping[str, Any] | None
) -> TicketCardData:
    """Build a card only when registration, person, and event bindings agree."""

    if not is_ticket_unlocked(registration):
        raise TicketCardError("Entry card requires payment_status == confirmed")
    if event is None:
        raise TicketCardError("Entry card requires an event")

    registration_event_id = _object_id(registration.get("event_id"), "event_id")
    event_id = _object_id(event.get("_id"), "event _id")
    if registration_event_id != event_id:
        raise TicketCardError("Registration event_id does not match the event")

    return TicketCardData(
        full_name=_clean_text(registration.get("full_name"), "", 160),
        graduate_label=_graduate_label(registration),
        event_name=_clean_text(
            event.get("name") or event.get("title"), "Мероприятие клуба 146", 160
        ),
        event_date=_event_date_display(event),
        event_time=_clean_text(event.get("time_display"), "Время уточняется", 40),
        venue=_clean_text(event.get("venue"), "Место уточняется", 160),
        address=_clean_text(event.get("address"), "Адрес уточняется", 240),
        guest_names=_guest_names(registration),
        ticket_code=_ticket_code(registration, event_id),
    )


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> Path:
    """Use Matplotlib's bundled DejaVu font on every deployment platform."""

    spec = importlib.util.find_spec("matplotlib")
    if spec is None or not spec.submodule_search_locations:
        raise TicketCardError("Matplotlib's bundled ticket font is unavailable")
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = (
        Path(next(iter(spec.submodule_search_locations)))
        / "mpl-data/fonts/ttf"
        / filename
    )
    if not path.is_file():
        raise TicketCardError("Matplotlib's bundled ticket font is unavailable")
    return path


@lru_cache(maxsize=64)
def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(bold), size=size)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    preferred_size: int,
    minimum_size: int,
):
    for size in range(preferred_size, minimum_size - 1, -2):
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return _font(minimum_size)


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    shortened = text
    while shortened:
        candidate = shortened.rstrip() + "…"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            return candidate
        shortened = shortened[:-1]
    return "…"


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
    max_lines: int,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        lines.append(_ellipsize(draw, current, font, max_width))
        current = word
        if len(lines) == max_lines - 1:
            break
    if current and len(lines) < max_lines:
        remaining_start = len(" ".join(lines + [current]).split())
        remaining = " ".join(words[remaining_start:])
        final = f"{current} {remaining}".strip()
        lines.append(_ellipsize(draw, final, font, max_width))
    return lines or [""]


def render_ticket_card(data: TicketCardData) -> bytes:
    """Render the same validated input to byte-identical PNG output."""

    image = Image.new("RGB", CARD_SIZE, "#0B2433")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((28, 28, 1172, 732), radius=34, fill="#F7F2E8")
    draw.rounded_rectangle((28, 28, 1172, 124), radius=34, fill="#143F56")
    draw.rectangle((28, 88, 1172, 124), fill="#143F56")
    draw.text(
        (68, 57),
        "КЛУБ ДРУЗЕЙ ШКОЛЫ 146",
        font=_font(34, bold=True),
        fill="#FFFFFF",
    )
    draw.rounded_rectangle((969, 49, 1125, 103), radius=18, fill="#F0AA36")
    draw.text((1011, 62), "ВХОД", font=_font(25, bold=True), fill="#152A35")

    draw.text(
        (68, 154),
        "ИМЕННОЙ БИЛЕТ • БЕЙДЖ",
        font=_font(30, bold=True),
        fill="#B46B14",
    )
    name_font = _fit_font(draw, data.full_name, 1060, 68, 38)
    name = _ellipsize(draw, data.full_name, name_font, 1060)
    draw.text((68, 210), name, font=name_font, fill="#102E3D")
    draw.text((71, 302), data.graduate_label, font=_font(30), fill="#4E6671")

    details_top = 365
    if data.guest_names:
        guests = f"С вами: {', '.join(data.guest_names)}"
        guests_font = _fit_font(draw, guests, 1060, 24, 19)
        guests = _ellipsize(draw, guests, guests_font, 1060)
        draw.text((71, 342), guests, font=guests_font, fill="#4E6671")
        details_top = 390

    draw.line((68, details_top, 1132, details_top), fill="#C8D1D2", width=3)
    event_font = _fit_font(draw, data.event_name, 1060, 40, 28)
    event_name = _ellipsize(draw, data.event_name, event_font, 1060)
    draw.text((68, details_top + 32), event_name, font=event_font, fill="#102E3D")
    draw.text(
        (68, details_top + 97),
        f"{data.event_date} • {data.event_time}",
        font=_font(30),
        fill="#143F56",
    )

    details_font = _font(25)
    place = f"{data.venue} • {data.address}"
    for index, line in enumerate(
        _wrap_lines(draw, place, details_font, 1060, max_lines=2)
    ):
        draw.text(
            (68, details_top + 151 + index * 38),
            line,
            font=details_font,
            fill="#4E6671",
        )

    draw.rounded_rectangle((68, 636, 570, 698), radius=18, fill="#DCE8E4")
    draw.text(
        (91, 650),
        "ОПЛАТА ПОДТВЕРЖДЕНА",
        font=_font(26, bold=True),
        fill="#176747",
    )
    code_label = f"КОД: {data.ticket_code}"
    code_font = _fit_font(draw, code_label, 510, 27, 20)
    draw.text((622, 652), code_label, font=code_font, fill="#143F56")

    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def make_ticket_card(
    registration: Mapping[str, Any], event: Mapping[str, Any] | None
) -> TicketCardArtifact:
    """Build the Telegram artifact after enforcing the confirmed-payment gate."""

    data = build_ticket_card_data(registration, event)
    return TicketCardArtifact(
        image=render_ticket_card(data),
        filename=f"club-146-ticket-{data.ticket_code}.png",
        caption=(
            "🎟 Ваш именной билет и бейдж. "
            "Покажите эту карточку на входе.\n"
            f"Код: {data.ticket_code}"
        ),
        ticket_code=data.ticket_code,
    )


async def send_paid_ticket_card(
    chat_id: int,
    registration: Mapping[str, Any],
    event: Mapping[str, Any] | None,
) -> bool:
    """Send a confirmed card; unpaid/invalid data fails closed without a send."""

    if not is_ticket_unlocked(registration):
        return False

    try:
        artifact = make_ticket_card(registration, event)
        bot = get_dependency_manager().bot
        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(artifact.image, filename=artifact.filename),
            caption=artifact.caption,
            parse_mode=None,
        )
    except Exception as exc:
        logger.warning(f"Could not send paid ticket card: {exc}")
        return False
    return True
