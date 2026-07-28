"""The website as the single source of truth for where and when an event is.

The defect this closes: the site and the bot each stored the event's place, the
bot's admin could edit its copy, and nothing reconciled them. On 28.07.2026 the
site advertised ул. Встречная while the bot told 46 registrants ул. Самаркандская.
Neither was "wrong" — there were simply two truths and no rule saying which won.

The fix is read-through, not sync. A sync job copies, and a copy can be stale;
it also needs a conflict rule the moment both sides can write. Here the bot holds
no copy at all: it reads the website's row and overlays it onto an in-memory copy
of the Mongo document, so there is nothing to drift.

Two properties matter and are pinned below:

1. Nothing is ever written back to Mongo. If it were, we would be back to two
   stores that can disagree.
2. The bot's own admin can no longer edit these fields. Without that, an edit
   would be silently discarded on the next read — worse than the old bug,
   because the admin would believe it had worked.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from src.website_db import (
    WebsiteEventReader,
    merge_website_event,
    website_db_requested,
)

WEBSITE_UID = "event-1@146.school"


def _settings(*, enabled: bool = True, url: str = "postgresql://ro@db/club146"):
    return SimpleNamespace(
        website_db_enabled=enabled,
        website_database_url=SecretStr(url),
        website_db_timeout_seconds=5.0,
        website_db_pool_size=3,
    )


def _mongo_event(**overrides):
    event = {
        "_id": "6a599a17a37724d81b7eadc3",
        "name": "Летняя встреча выпускников",
        "city": "Пермь",
        "date": datetime(2026, 8, 1, 18, 0),
        "date_display": "1 Августа, Сб",
        "time_display": "18:00-00:00",
        "venue": "Старое место",
        "address": "ул. Встречная 28",
        "website_event_uid": WEBSITE_UID,
        "status": "upcoming",
        "enabled": True,
    }
    event.update(overrides)
    return event


def _website_row(**overrides):
    row = {
        "uid": WEBSITE_UID,
        "title": "Летняя встреча 146 в Перми",
        "starts_at": datetime(2026, 8, 1, 18, 0),
        "venue": "Беседка «Банкетная»",
        "address": "г.Пермь, ул. Самаркандская 2",
        "url": "https://146.school/events",
        "published": True,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# merge_website_event
# --------------------------------------------------------------------------


class TestMergeWebsiteEvent:
    def test_the_reported_divergence_is_resolved_towards_the_website(self):
        merged = merge_website_event(_mongo_event(), _website_row())
        assert merged["venue"] == "Беседка «Банкетная»"
        assert merged["address"] == "г.Пермь, ул. Самаркандская 2"

    def test_mongo_document_is_not_mutated(self):
        """The whole point: the bot keeps no second copy that could drift."""
        event = _mongo_event()
        merge_website_event(event, _website_row())
        assert event["venue"] == "Старое место"
        assert event["address"] == "ул. Встречная 28"

    def test_name_comes_from_the_website_title(self):
        merged = merge_website_event(_mongo_event(), _website_row())
        assert merged["name"] == "Летняя встреча 146 в Перми"

    def test_date_display_is_derived_not_copied(self):
        """A moved date with a stale display string is the same bug again."""
        merged = merge_website_event(
            _mongo_event(), _website_row(starts_at=datetime(2026, 9, 5, 19, 30))
        )
        assert merged["date"] == datetime(2026, 9, 5, 19, 30)
        assert merged["date_display"] == "5 Сентября, Сб"

    def test_operator_written_time_display_is_left_alone(self):
        """`time_display` is free text the operators own ("18:00-00:00")."""
        merged = merge_website_event(_mongo_event(), _website_row())
        assert merged["time_display"] == "18:00-00:00"

    def test_time_display_is_filled_when_mongo_has_none(self):
        merged = merge_website_event(
            _mongo_event(time_display=""), _website_row()
        )
        assert merged["time_display"] == "18:00"

    def test_optional_venue_becomes_none_rather_than_empty_string(self):
        """venue is optional on the website by decision."""
        merged = merge_website_event(_mongo_event(), _website_row(venue=""))
        assert merged["venue"] is None
        assert merged["address"] == "г.Пермь, ул. Самаркандская 2"

    def test_venue_and_address_move_together(self):
        """Taking one from the site and one from Mongo renders a place that
        exists in neither system."""
        merged = merge_website_event(
            _mongo_event(), _website_row(venue="", address="")
        )
        assert merged["venue"] == "Старое место"
        assert merged["address"] == "ул. Встречная 28"

    def test_blank_title_does_not_erase_the_name(self):
        merged = merge_website_event(_mongo_event(), _website_row(title=""))
        assert merged["name"] == "Летняя встреча выпускников"

    def test_unrelated_bot_owned_fields_survive(self):
        merged = merge_website_event(_mongo_event(), _website_row())
        assert merged["enabled"] is True
        assert merged["status"] == "upcoming"
        assert merged["_id"] == "6a599a17a37724d81b7eadc3"


# --------------------------------------------------------------------------
# WebsiteEventReader.resolve_event
# --------------------------------------------------------------------------


class TestResolveEvent:
    @pytest.mark.asyncio
    async def test_flag_off_returns_the_mongo_event_untouched(self):
        reader = WebsiteEventReader(_settings(enabled=False))
        event = _mongo_event()
        assert await reader.resolve_event(event) is event

    @pytest.mark.asyncio
    async def test_missing_dsn_disables_it(self):
        reader = WebsiteEventReader(_settings(url=""))
        assert reader.enabled() is False

    @pytest.mark.asyncio
    async def test_unlinked_event_is_returned_untouched(self):
        """No website_event_uid means Mongo is its only possible source, which
        is not a divergence."""
        reader = WebsiteEventReader(_settings())
        reader.get_event_by_uid = AsyncMock()
        event = _mongo_event(website_event_uid=None)
        assert await reader.resolve_event(event) is event
        reader.get_event_by_uid.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_linked_event_is_overlaid(self):
        reader = WebsiteEventReader(_settings())
        reader.get_event_by_uid = AsyncMock(return_value=_website_row())
        merged = await reader.resolve_event(_mongo_event())
        assert merged["address"] == "г.Пермь, ул. Самаркандская 2"

    @pytest.mark.asyncio
    async def test_missing_website_row_falls_back_to_mongo(self):
        reader = WebsiteEventReader(_settings())
        reader.get_event_by_uid = AsyncMock(return_value=None)
        event = _mongo_event()
        assert await reader.resolve_event(event) is event

    @pytest.mark.asyncio
    async def test_database_failure_falls_back_to_mongo_and_does_not_raise(self):
        """Fails CLOSED to Mongo, unlike pricing which has no fallback.

        A stale address during a database outage beats showing a registrant an
        error; a stale price would be an actual wrong charge.
        """
        reader = WebsiteEventReader(_settings())

        async def _boom(*_args, **_kwargs):
            raise OSError("connection refused")

        reader._get_pool = _boom
        event = _mongo_event()
        assert await reader.resolve_event(event) is event

    @pytest.mark.asyncio
    async def test_none_event_stays_none(self):
        reader = WebsiteEventReader(_settings())
        assert await reader.resolve_event(None) is None


class TestRequestedFlag:
    def test_only_a_literal_true_enables_it(self):
        assert website_db_requested(SimpleNamespace(website_db_enabled=True)) is True
        assert website_db_requested(SimpleNamespace(website_db_enabled=1)) is False
        assert website_db_requested(SimpleNamespace(website_db_enabled="true")) is False
        assert website_db_requested(SimpleNamespace()) is False


# --------------------------------------------------------------------------
# The bot's second write path is closed
# --------------------------------------------------------------------------


class TestAdminCannotEditWebsiteOwnedFields:
    def _app(self, *, enabled=True):
        return SimpleNamespace(
            settings=SimpleNamespace(
                website_db_enabled=enabled,
                payment_site_base_url="https://146.school",
            ),
            update_event=AsyncMock(),
        )

    def test_linked_event_is_website_owned(self):
        from src.routers._events_helpers import website_owns_event_calendar

        assert website_owns_event_calendar(self._app(), _mongo_event()) is True

    def test_unlinked_event_stays_bot_owned(self):
        from src.routers._events_helpers import website_owns_event_calendar

        event = _mongo_event(website_event_uid=None)
        assert website_owns_event_calendar(self._app(), event) is False

    def test_feature_off_keeps_the_bot_in_charge(self):
        """Rolling the flag back must restore the old editing behaviour."""
        from src.routers._events_helpers import website_owns_event_calendar

        app = self._app(enabled=False)
        assert website_owns_event_calendar(app, _mongo_event()) is False

    @pytest.mark.asyncio
    async def test_site_owned_fields_are_removed_from_the_edit_menu(self):
        from unittest.mock import patch

        from src.routers import _events_helpers as helpers

        seen = {}

        async def _capture_choice(_chat_id, prompt, choices, **_kwargs):
            seen["prompt"] = prompt
            seen["choices"] = choices
            return "back"

        with patch.object(helpers, "ask_user_choice", _capture_choice):
            await helpers._handle_edit_event(
                1, None, self._app(), _mongo_event(), "e1", 2, "admin"
            )

        for blocked in ("name", "date", "venue", "address"):
            assert blocked not in seen["choices"]
        # Bot-owned settings must still be editable.
        for kept in ("time", "pricing", "guests", "templates"):
            assert kept in seen["choices"]
        assert "сайт" in seen["prompt"].lower()

    @pytest.mark.asyncio
    async def test_stale_callback_for_a_site_owned_field_writes_nothing(self):
        """The guard that actually protects Mongo, independent of the menu."""
        from unittest.mock import patch

        from src.routers import _events_helpers as helpers

        app = self._app()

        async def _pick_venue(*_args, **_kwargs):
            return "venue"

        with patch.object(helpers, "ask_user_choice", _pick_venue), \
                patch.object(helpers, "send_safe", AsyncMock()) as send:
            await helpers._handle_edit_event(
                1, None, app, _mongo_event(), "e1", 2, "admin"
            )

        app.update_event.assert_not_awaited()
        assert "сайт" in send.await_args.args[1].lower()

    @pytest.mark.asyncio
    async def test_unlinked_event_can_still_be_edited_in_the_bot(self):
        """Events with no website counterpart keep the old admin flow."""
        from unittest.mock import patch

        from src.routers import _events_helpers as helpers

        seen = {}

        async def _capture_choice(_chat_id, prompt, choices, **_kwargs):
            seen["choices"] = choices
            return "back"

        with patch.object(helpers, "ask_user_choice", _capture_choice):
            await helpers._handle_edit_event(
                1, None, self._app(), _mongo_event(website_event_uid=None),
                "e1", 2, "admin",
            )

        for key in ("name", "date", "venue", "address"):
            assert key in seen["choices"]
