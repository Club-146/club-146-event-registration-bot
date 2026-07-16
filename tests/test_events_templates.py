"""Admin flow for editing per-event message texts (/manage_events → Тексты сообщений)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import src.routers._events_helpers as H


@pytest.fixture
def event():
    return {"templates": {}}


@pytest.fixture
def app(event):
    app = MagicMock()

    async def update_event(event_id, updates):
        for key, value in updates.items():
            if key.startswith("templates."):
                event["templates"][key.split(".", 1)[1]] = value
        return True

    app.update_event = update_event
    return app


def _run_editor(sent, choices, reply_text=None):
    """Patch the ask/send layer and return a configured context manager stack."""
    it = iter(choices)

    async def fake_send_safe(chat_id, text, **kwargs):
        sent.append(text)
        return MagicMock()

    reply = MagicMock(text=reply_text) if reply_text is not None else None
    return (
        patch.object(H, "send_safe", fake_send_safe),
        patch.object(H, "ask_user_choice", AsyncMock(side_effect=lambda *a, **k: next(it))),
        patch.object(H, "ask_user_raw", AsyncMock(return_value=reply)),
    )


async def _edit(app, event, choices, reply_text=None):
    sent = []
    a, b, c = _run_editor(sent, choices, reply_text)
    with a, b, c:
        await H._handle_edit_message_templates(1, MagicMock(), app, event, "eid")
    return sent


@pytest.mark.asyncio
async def test_good_edit_is_saved(app, event):
    text = "<b>{price_label}: {regular_amount} руб.</b>\nЖдём на {season} встрече!"
    sent = await _edit(app, event, ["payment_price_regular", "edit"], text)

    assert event["templates"]["payment_price_regular"] == text
    assert "обновлён" in sent[-1]


@pytest.mark.asyncio
async def test_unsupported_html_is_rejected_and_not_stored(app, event):
    sent = await _edit(app, event, ["payment_price_regular", "edit"], "<div>{season}</div>")

    assert event["templates"] == {}, "invalid text must not reach the DB"
    assert "не сохранён" in sent[-1]
    assert "не поддерживает теги" in sent[-1]


@pytest.mark.asyncio
async def test_unknown_placeholder_is_rejected(app, event):
    sent = await _edit(app, event, ["payment_price_regular", "edit"], "Цена {bogus}")

    assert event["templates"] == {}
    assert "Неизвестные подстановки" in sent[-1]


@pytest.mark.asyncio
async def test_reset_restores_default(app, event):
    event["templates"]["payment_price_regular"] = "<b>custom {season}</b>"
    sent = await _edit(app, event, ["payment_price_regular", "reset"])

    assert event["templates"]["payment_price_regular"] == ""
    assert "стандартный текст" in sent[-1]


@pytest.mark.asyncio
async def test_back_from_template_choice_changes_nothing(app, event):
    sent = await _edit(app, event, ["back"])

    assert event["templates"] == {}
    assert sent == []


@pytest.mark.asyncio
async def test_preview_escapes_tags_so_admin_sees_raw_markup(app, event):
    sent = await _edit(app, event, ["payment_price_regular", "back"])

    preview = sent[0]
    # The admin must see the <b> tags as text, not as formatting.
    assert "&lt;b&gt;" in preview
    assert "{price_label}" in preview


@pytest.mark.asyncio
async def test_no_reply_leaves_template_untouched(app, event):
    await _edit(app, event, ["payment_price_regular", "edit"], reply_text=None)

    assert event["templates"] == {}
