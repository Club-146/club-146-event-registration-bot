from datetime import datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import FSInputFile, Message

from src.event_images import (
    MARIA_PERM_SUMMER_2026_CANONICAL_URL,
    MARIA_PERM_SUMMER_2026_SHA256,
    MARIA_PERM_SUMMER_2026_SOURCE,
    event_image_url_from_message,
    resolve_event_image,
    send_event_image,
)


def _maria_event(**overrides):
    event = {
        "city": "Пермь",
        "date": datetime(2026, 8, 1),
        "name": "Пермь (Летняя встреча 2026)",
    }
    event.update(overrides)
    return event


def _message(text: str | None = None) -> MagicMock:
    message = MagicMock(spec=Message)
    message.chat = MagicMock()
    message.chat.id = 12345
    message.message_id = 678
    message.text = text
    message.photo = []
    message.document = None
    return message


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_photo = AsyncMock(return_value=MagicMock(name="sent_photo"))
    dependencies = MagicMock(bot=bot)

    with patch(
        "src.event_images.get_dependency_manager",
        return_value=dependencies,
    ):
        yield bot


def test_resolve_event_image_uses_maria_read_only_bundled_fallback():
    image = resolve_event_image(_maria_event())

    assert image is not None
    assert image["kind"] == "bundled"
    assert image["source_ref"] == MARIA_PERM_SUMMER_2026_SOURCE
    assert image["path"].lower().endswith(".png")
    assert image["canonical_url"] == (
        "https://146.school/static/img/events/summer-alumni-meetup-2026.png"
    )
    assert image["canonical_url"] == MARIA_PERM_SUMMER_2026_CANONICAL_URL


def test_bundled_maria_image_matches_recovered_provenance_hash():
    asset = (
        Path(__file__).parents[1] / "src" / "assets" / "events" / "perm-summer-2026.png"
    )

    assert sha256(asset.read_bytes()).hexdigest() == MARIA_PERM_SUMMER_2026_SHA256


def test_resolve_event_image_does_not_match_unrelated_perm_event_same_day():
    event = _maria_event(name="Пермь (Закрытая встреча 2026)")

    assert resolve_event_image(event) is None


def test_resolve_event_image_prefers_explicit_url_over_maria_fallback():
    explicit_image = {
        "kind": "url",
        "url": "https://146.school/static/img/events/replacement.png",
        "canonical_url": "https://146.school/static/img/events/replacement.png",
        "source_ref": "manual:test-override",
    }

    assert resolve_event_image(_maria_event(image=explicit_image)) == explicit_image


def test_resolve_event_image_explicit_none_suppresses_maria_fallback():
    assert resolve_event_image(_maria_event(image=None)) is None


def test_event_image_url_accepts_absolute_https_146_school_url():
    image = event_image_url_from_message(
        _message(
            "  https://146.school/static/img/events/meeting.png?version=2#preview  "
        )
    )

    assert image == {
        "kind": "url",
        "url": "https://146.school/static/img/events/meeting.png?version=2#preview",
        "canonical_url": (
            "https://146.school/static/img/events/meeting.png?version=2#preview"
        ),
        "source_ref": "tg-bot:12345:message:678",
    }


@pytest.mark.parametrize(
    "value",
    [
        "http://146.school/static/img/events/meeting.png",
        "/static/img/events/meeting.png",
        "146.school/static/img/events/meeting.png",
        "https://example.com/meeting.png",
        "https://www.146.school/meeting.png",
        "https://cdn.146.school/meeting.png",
        "https://146.school.evil/meeting.png",
        "https://146.school@evil.example/meeting.png",
        "https://user@146.school/meeting.png",
        "https://146.school:8443/meeting.png",
        "https://146.school/path with space.png",
        "not a URL",
        "",
    ],
)
def test_event_image_url_rejects_noncanonical_input(value):
    assert event_image_url_from_message(_message(value)) is None


def test_event_image_url_rejects_telegram_photo_or_document():
    photo_message = _message()
    photo_message.photo = [MagicMock(file_id="photo-id")]
    document_message = _message()
    document_message.document = MagicMock(file_id="document-id", mime_type="image/png")

    assert event_image_url_from_message(photo_message) is None
    assert event_image_url_from_message(document_message) is None


@pytest.mark.asyncio
async def test_send_event_image_sends_bundled_maria_image_as_photo(mock_bot):
    sent = await send_event_image(777, _maria_event(), caption="Летняя встреча")

    assert sent is True
    call = mock_bot.send_photo.await_args
    assert call is not None
    assert call.kwargs["chat_id"] == 777
    assert call.kwargs["caption"] == "Летняя встреча"
    assert call.kwargs["parse_mode"] is None
    assert isinstance(call.kwargs["photo"], FSInputFile)
    assert Path(call.kwargs["photo"].path).is_file()


@pytest.mark.asyncio
async def test_send_event_image_sends_146_school_url(mock_bot):
    url = "https://146.school/static/img/events/meeting.png"

    sent = await send_event_image(778, {"image": {"kind": "url", "url": url}})

    assert sent is True
    mock_bot.send_photo.assert_awaited_once_with(chat_id=778, photo=url)


@pytest.mark.asyncio
async def test_send_event_image_skips_event_without_image(mock_bot):
    sent = await send_event_image(779, _maria_event(image=None))

    assert sent is False
    mock_bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_event_image_rejects_unsupported_image_kind(mock_bot):
    sent = await send_event_image(
        780, {"image": {"kind": "telegram_photo", "file_id": "legacy-id"}}
    )

    assert sent is False
    mock_bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_event_image_rejects_off_domain_url(mock_bot):
    sent = await send_event_image(
        781,
        {"image": {"kind": "url", "url": "https://example.com/meeting.png"}},
    )

    assert sent is False
    mock_bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_event_image_fails_open_on_telegram_error(mock_bot):
    mock_bot.send_photo.side_effect = RuntimeError("Telegram unavailable")

    sent = await send_event_image(
        782,
        {
            "image": {
                "kind": "url",
                "url": "https://146.school/static/img/events/meeting.png",
            }
        },
    )

    assert sent is False
