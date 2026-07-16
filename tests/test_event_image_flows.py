from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _message(chat_id: int = 123) -> MagicMock:
    message = MagicMock()
    message.chat.id = chat_id
    message.chat.type = "private"
    message.from_user.id = 456
    message.from_user.username = "admin"
    message.text = "/start"
    message.message_id = 789
    return message


def _event_url() -> dict:
    url = "https://146.school/static/img/events/meeting.png"
    return {
        "kind": "url",
        "url": url,
        "canonical_url": url,
        "source_ref": "tg-bot:123:message:789",
    }


@pytest.mark.asyncio
async def test_collect_event_image_can_skip():
    from src.routers._events_helpers import _collect_event_image

    with (
        patch(
            "src.routers._events_helpers.ask_user_choice_raw",
            new=AsyncMock(return_value="skip"),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()) as send_safe,
    ):
        result = await _collect_event_image(123, MagicMock())

    assert result is None
    send_safe.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_event_image_accepts_146_school_url():
    from src.routers._events_helpers import _collect_event_image

    raw_message = _message()
    raw_message.text = _event_url()["url"]
    with (
        patch(
            "src.routers._events_helpers.ask_user_choice_raw",
            new=AsyncMock(return_value=raw_message),
        ),
        patch(
            "src.routers._events_helpers.send_event_image",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await _collect_event_image(123, MagicMock())

    assert result == _event_url()


@pytest.mark.asyncio
async def test_collect_event_image_rejects_off_domain_url_before_preview():
    from src.routers._events_helpers import _collect_event_image

    raw_message = _message()
    raw_message.text = "https://example.com/meeting.png"
    with (
        patch(
            "src.routers._events_helpers.ask_user_choice_raw",
            new=AsyncMock(return_value=raw_message),
        ),
        patch(
            "src.routers._events_helpers.send_event_image",
            new=AsyncMock(),
        ) as send_event_image,
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()) as send_safe,
    ):
        result = await _collect_event_image(123, MagicMock())

    assert result is None
    send_event_image.assert_not_awaited()
    error_call = send_safe.await_args
    assert error_call is not None
    assert "146.school" in error_call.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["attach", "replace"])
async def test_admin_can_attach_or_replace_event_image(action):
    from src.routers._events_helpers import _handle_edit_image

    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)
    app.save_event_log = AsyncMock()
    event = (
        {
            "city": "Пермь",
            "date": datetime(2026, 8, 1),
            "name": "Пермь (Летняя встреча 2026)",
        }
        if action == "replace"
        else {"city": "Москва", "date": datetime(2026, 9, 1)}
    )
    response = _message()
    response.text = _event_url()["url"]

    with (
        patch(
            "src.routers._events_helpers.ask_user_choice",
            new=AsyncMock(return_value=action),
        ),
        patch(
            "src.routers._events_helpers.ask_user_raw",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "src.routers._events_helpers.send_event_image",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_image(123, MagicMock(), app, event, "event-id", 456, "admin")

    app.update_event.assert_awaited_once_with("event-id", {"image": _event_url()})
    app.save_event_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_can_remove_bundled_event_image():
    from src.routers._events_helpers import _handle_edit_image

    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)
    app.save_event_log = AsyncMock()
    event = {
        "city": "Пермь",
        "date": datetime(2026, 8, 1),
        "name": "Пермь (Летняя встреча 2026)",
    }

    with (
        patch(
            "src.routers._events_helpers.ask_user_choice",
            new=AsyncMock(return_value="remove"),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_image(123, MagicMock(), app, event, "event-id", 456, "admin")

    app.update_event.assert_awaited_once_with("event-id", {"image": None})
    log_data = app.save_event_log.await_args.kwargs["data"]
    assert log_data["old_source"].endswith("message:355467")
    assert log_data["new_source"] is None


@pytest.mark.asyncio
async def test_admin_does_not_save_image_that_preview_cannot_deliver():
    from src.routers._events_helpers import _handle_edit_image

    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)
    app.save_event_log = AsyncMock()
    response = _message()
    response.text = _event_url()["url"]

    with (
        patch(
            "src.routers._events_helpers.ask_user_choice",
            new=AsyncMock(return_value="attach"),
        ),
        patch(
            "src.routers._events_helpers.ask_user_raw",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "src.routers._events_helpers.send_event_image",
            new=AsyncMock(return_value=False),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_image(
            123,
            MagicMock(),
            app,
            {"city": "Москва"},
            "event-id",
            456,
            "admin",
        )

    app.update_event.assert_not_awaited()
    app.save_event_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_rejects_off_domain_url_before_preview_or_update():
    from src.routers._events_helpers import _handle_edit_image

    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)
    app.save_event_log = AsyncMock()
    response = _message()
    response.text = "https://146.school.evil/meeting.png"

    with (
        patch(
            "src.routers._events_helpers.ask_user_choice",
            new=AsyncMock(return_value="attach"),
        ),
        patch(
            "src.routers._events_helpers.ask_user_raw",
            new=AsyncMock(return_value=response),
        ),
        patch(
            "src.routers._events_helpers.send_event_image",
            new=AsyncMock(),
        ) as send_event_image,
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_image(
            123,
            MagicMock(),
            app,
            {"city": "Москва"},
            "event-id",
            456,
            "admin",
        )

    send_event_image.assert_not_awaited()
    app.update_event.assert_not_awaited()
    app.save_event_log.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("image", "confirmed", "expected_image"),
    [
        (_event_url(), True, _event_url()),
        (None, True, None),
        (_event_url(), False, None),
    ],
)
async def test_create_event_stores_only_confirmed_146_school_url(
    image, confirmed, expected_image
):
    from src.routers.events import create_event_handler

    message = _message()
    state = MagicMock()
    app = MagicMock()
    app.create_event = AsyncMock(return_value="event-id")
    app.save_event_log = AsyncMock()
    event_date = datetime(2026, 8, 1, 18, 0)
    with (
        patch(
            "src.routers.events._collect_city",
            new=AsyncMock(return_value=("Пермь", "Перми")),
        ),
        patch(
            "src.routers.events._collect_date_and_name",
            new=AsyncMock(
                return_value=(event_date, "18:00", "Пермь (Летняя встреча 2026)")
            ),
        ),
        patch(
            "src.routers.events._collect_venue_info",
            new=AsyncMock(return_value=(None, None)),
        ),
        patch(
            "src.routers.events._collect_event_image",
            new=AsyncMock(return_value=image),
        ),
        patch(
            "src.routers.events._collect_pricing_config",
            new=AsyncMock(return_value={"pricing_type": "free"}),
        ),
        patch(
            "src.routers.events._collect_free_for_types",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "src.routers.events._collect_early_bird",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "src.routers.events._collect_guest_settings",
            new=AsyncMock(return_value={"guests_enabled": False}),
        ),
        patch(
            "src.routers.events.ask_user_confirmation",
            new=AsyncMock(return_value=confirmed),
        ),
        patch("src.routers.events.send_safe", new=AsyncMock()),
    ):
        await create_event_handler(message, state, app)

    if not confirmed:
        app.create_event.assert_not_awaited()
        return

    create_call = app.create_event.await_args
    assert create_call is not None
    created_event = create_call.args[0]
    assert created_event["image"] == expected_image


@pytest.mark.asyncio
async def test_date_edit_does_not_materialize_bundled_fallback():
    from src.routers._events_helpers import _handle_edit_field_date

    event = {
        "city": "Пермь",
        "date": datetime(2026, 8, 1, 18, 0),
        "date_display": "1 Августа, Сб",
        "name": "Пермь (Летняя встреча 2026)",
    }
    response = MagicMock(text="08.08.2026")
    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)

    with (
        patch(
            "src.routers._events_helpers.ask_user_raw",
            new=AsyncMock(return_value=response),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_field_date(123, MagicMock(), app, event, "event-id")

    update_call = app.update_event.await_args
    assert update_call is not None
    updates = update_call.args[1]
    assert updates["date"] == datetime(2026, 8, 8, 18, 0)
    assert "image" not in updates


@pytest.mark.asyncio
async def test_name_edit_does_not_materialize_bundled_fallback():
    from src.routers._events_helpers import _handle_edit_field_name

    event = {
        "city": "Пермь",
        "date": datetime(2026, 8, 1, 18, 0),
        "name": "Пермь (Летняя встреча 2026)",
    }
    app = MagicMock()
    app.update_event = AsyncMock(return_value=True)
    app.save_event_log = AsyncMock()

    with (
        patch(
            "src.routers._events_helpers.ask_user_raw",
            new=AsyncMock(return_value=MagicMock(text="Новое название")),
        ),
        patch("src.routers._events_helpers.send_safe", new=AsyncMock()),
    ):
        await _handle_edit_field_name(
            123, MagicMock(), app, event, "event-id", 456, "admin"
        )

    update_call = app.update_event.await_args
    assert update_call is not None
    assert update_call.args == ("event-id", {"name": "Новое название"})


@pytest.mark.asyncio
@pytest.mark.parametrize("image", [_event_url(), None])
async def test_single_event_welcome_handles_image_and_no_image(image):
    from src.router import _show_single_event_welcome

    message = _message()
    event = {
        "city": "Москва",
        "name": "Тестовая встреча",
        "date": datetime(2026, 9, 1),
        "image": image,
    }
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    dependencies = MagicMock(bot=bot)

    with (
        patch("src.event_images.get_dependency_manager", return_value=dependencies),
        patch("src.router.ask_user_choice", new=AsyncMock(return_value="cancel")),
        patch("src.router.send_safe", new=AsyncMock()),
    ):
        await _show_single_event_welcome(message, MagicMock(), MagicMock(), event, None)

    if image:
        bot.send_photo.assert_awaited_once_with(
            chat_id=123,
            photo=_event_url()["url"],
            caption="Тестовая встреча",
            parse_mode=None,
        )
    else:
        bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_info_flow_sends_only_images_that_exist():
    from src.router import info_handler

    message = _message()
    message.text = "/info"
    app = MagicMock()
    app.save_event_log = AsyncMock()
    app.get_active_events = AsyncMock(
        return_value=[
            {
                "city": "Москва",
                "name": "С изображением",
                "date": datetime(2026, 9, 1),
                "image": _event_url(),
            },
            {
                "city": "Казань",
                "name": "Без изображения",
                "date": datetime(2026, 9, 2),
                "image": None,
            },
        ]
    )
    app.is_event_passed = MagicMock(return_value=False)
    bot = MagicMock()
    bot.send_photo = AsyncMock()
    dependencies = MagicMock(bot=bot)

    with (
        patch("src.event_images.get_dependency_manager", return_value=dependencies),
        patch("src.router.send_safe", new=AsyncMock()) as send_safe,
    ):
        await info_handler(message, MagicMock(), app)

    bot.send_photo.assert_awaited_once_with(
        chat_id=123,
        photo=_event_url()["url"],
        caption="С изображением",
        parse_mode=None,
    )
    send_call = send_safe.await_args
    assert send_call is not None
    assert "С изображением" in send_call.args[1]
    assert "Без изображения" in send_call.args[1]
