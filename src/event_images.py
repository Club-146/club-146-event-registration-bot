"""Read-only bundled event images and 146.school URL delivery helpers."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from aiogram.types import FSInputFile, Message

from botspot.core.dependency_manager import get_dependency_manager
from botspot.utils.internal import get_logger

logger = get_logger()

MARIA_PERM_SUMMER_2026_SOURCE = "tg:291560340:peer:884517699:message:355467"
MARIA_PERM_SUMMER_2026_SHA256 = (
    "38fc4ba37460b921db5ddf3b7a640a6601ea0f00af4cda034a1f1d9a1480be0d"
)
MARIA_PERM_SUMMER_2026_CANONICAL_URL = (
    "https://146.school/static/img/events/summer-alumni-meetup-2026.png"
)

_SOURCE_ROOT = Path(__file__).resolve().parent
_MARIA_PERM_SUMMER_2026_IMAGE = {
    "kind": "bundled",
    "path": "assets/events/perm-summer-2026.png",
    "content_type": "image/png",
    "file_name": "146 КАРТИНКА_СОЦСЕТИ ВВ ПР.png",
    "sha256": MARIA_PERM_SUMMER_2026_SHA256,
    "source_ref": MARIA_PERM_SUMMER_2026_SOURCE,
    "canonical_url": MARIA_PERM_SUMMER_2026_CANONICAL_URL,
}


def _event_date(event: Mapping[str, Any]) -> date | None:
    value = event.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def resolve_event_image(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve an explicit image or Maria's read-only bundled fallback.

    An explicit ``image: None`` is intentional: it lets an admin remove the
    fallback without changing or deleting the provenance-bearing bundled asset.
    """

    if "image" in event:
        image = event.get("image")
        return dict(image) if isinstance(image, Mapping) else None

    event_name = str(event.get("name", "")).casefold()
    if (
        event.get("city") == "Пермь"
        and _event_date(event) == date(2026, 8, 1)
        and "летн" in event_name
        and "2026" in event_name
    ):
        return dict(_MARIA_PERM_SUMMER_2026_IMAGE)
    return None


def _message_source_ref(message: Message) -> str:
    return f"tg-bot:{message.chat.id}:message:{message.message_id}"


def _is_146_school_https_url(url: str) -> bool:
    if not url or any(character.isspace() for character in url):
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False

    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == "146.school"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
    )


def event_image_url_from_message(message: Any) -> dict[str, Any] | None:
    """Accept only an absolute HTTPS image URL hosted on 146.school."""

    text = getattr(message, "text", None)
    if not isinstance(text, str):
        return None

    url = text.strip()
    if not _is_146_school_https_url(url):
        return None

    return {
        "kind": "url",
        "url": url,
        "canonical_url": url,
        "source_ref": _message_source_ref(message),
    }


def _bundled_path(image: Mapping[str, Any]) -> Path | None:
    relative_path = image.get("path")
    if not isinstance(relative_path, str):
        return None

    candidate = (_SOURCE_ROOT / relative_path).resolve()
    if candidate != _SOURCE_ROOT and _SOURCE_ROOT not in candidate.parents:
        logger.warning("Refusing event image path outside the source tree")
        return None
    if not candidate.is_file():
        logger.warning(f"Bundled event image is missing: {candidate}")
        return None
    return candidate


async def send_event_image(
    chat_id: int,
    event: Mapping[str, Any],
    caption: str | None = None,
) -> bool:
    """Send a bundled or 146.school event image; fail open for text flows."""

    image = resolve_event_image(event)
    if not image:
        return False

    kwargs: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = None

    try:
        bot = get_dependency_manager().bot
        kind = image.get("kind")
        if kind == "bundled":
            path = _bundled_path(image)
            if path is None:
                return False
            await bot.send_photo(
                photo=FSInputFile(path, filename=image.get("file_name")), **kwargs
            )
        elif kind == "url":
            url = image.get("url")
            if not isinstance(url, str) or not _is_146_school_https_url(url):
                return False
            await bot.send_photo(photo=url, **kwargs)
        else:
            logger.warning(f"Unknown event image kind: {kind}")
            return False
    except Exception as exc:
        logger.warning(
            f"Could not send image for event {event.get('name', event.get('city', '?'))}: {exc}"
        )
        return False

    return True
