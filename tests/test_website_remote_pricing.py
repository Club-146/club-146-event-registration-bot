"""The website as the single source of truth for event pricing.

Two separate defects motivate this module, and they are the same disease:
price was computed in more than one place, from more than one source, with
nothing comparing the results.

1. The bot read the pricing formula from its Mongo event document while the
   website read `event_registration_configs`. The website's `create_intent`
   does not validate the formula it is sent -- it trusts it verbatim -- so the
   two channels could price the same person differently, forever, undetected.

2. Inside the bot alone, the price *displayed* resolves early bird through
   `payment_timeline` (cutoff 06:00 on the deadline day) while the price
   *frozen* is re-evaluated by the website as `calculation_date <=
   early_bird_deadline` (the whole day). For the real Aug 1 2026 event that is
   a one-day disagreement.

`resolve_event_pricing` fixes authority; `_assert_matches_quoted_price` makes
any remaining disagreement loud instead of expensive.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from src.payment_timeline import is_early_bird_active
from src.website_event_bridge import (
    WebsiteBridgeError,
    build_new_intent_payload,
    freeze_new_registration_snapshot,
    merge_remote_pricing,
    remote_pricing_requested,
    resolve_event_pricing,
)

BOT_EVENT_ID = "6a599a17a37724d81b7eadc3"
REGISTRATION_ID = "7b699a17a37724d81b7eadc4"
WEBSITE_UID = "aug1-2026-perm"


def _settings(*, remote_pricing: bool = True, bridge: bool = True):
    return SimpleNamespace(
        event_payments_bridge_enabled=bridge,
        event_payments_remote_pricing_enabled=remote_pricing,
        event_payments_website_api_base_url="https://staging.example.test",
        event_payments_website_api_token=SecretStr("dedicated-test-token"),
        event_payments_website_event_id=1,
        event_payments_website_event_uid=WEBSITE_UID,
        event_payments_bot_event_id=BOT_EVENT_ID,
        event_payments_api_timeout_seconds=1.0,
    )


def _event(**overrides):
    """The Mongo event document, deliberately carrying a *stale* price."""
    event = {
        "_id": BOT_EVENT_ID,
        "date": datetime(2026, 8, 1),
        "pricing_type": "formula",
        "price_formula_base": 1400,
        "price_formula_rate": 200,
        "price_formula_reference_year": 2026,
        "price_formula_step": 1,
        "guest_price_minimum": 0,
        "free_for_types": ["TEACHER"],
        "early_bird_discount": 500,
        "early_bird_deadline": datetime(2026, 7, 29),
    }
    event.update(overrides)
    return event


def _config(**overrides):
    """What GET /api/internal/event-configs/by-bot-event/{id} returns."""
    config = {
        "website_event_id": 1,
        "website_event_uid": WEBSITE_UID,
        "bot_event_id": BOT_EVENT_ID,
        "title": "Встреча 1 августа",
        "registration_open": True,
        "pricing_type": "formula",
        # base differs from Mongo on purpose: this is the whole point
        "price_formula_base": 1600,
        "price_formula_rate": 200,
        "price_formula_reference_year": 2026,
        "price_formula_step": 1,
        "guest_price_minimum": 0,
        "guest_price_fixed": 1500,
        "free_for_types": ["teacher", "organizer"],
        "early_bird_discount": 500,
        "early_bird_deadline": "2026-07-29",
        "pricing_version": "website-config-v7",
    }
    config.update(overrides)
    return config


def _registration(**overrides):
    registration = {
        "_id": REGISTRATION_ID,
        "event_id": BOT_EVENT_ID,
        "user_id": 123456789,
        "full_name": "Иван Иванов",
        "graduation_year": 2010,
        "graduate_type": "GRADUATE",
        "guests": [],
    }
    registration.update(overrides)
    return registration


class FakeConfigClient:
    def __init__(self, config=None, error: Exception | None = None):
        self.config = config if config is not None else _config()
        self.error = error
        self.calls: list[str] = []

    async def get_event_config(self, bot_event_id):
        self.calls.append(bot_event_id)
        if self.error:
            raise self.error
        return self.config


def _app(settings):
    return SimpleNamespace(
        settings=settings,
        collection=SimpleNamespace(update_one=AsyncMock()),
        log_to_chat=AsyncMock(),
    )


class TestFlag:
    def test_only_a_literal_true_turns_remote_pricing_on(self):
        assert remote_pricing_requested(_settings(remote_pricing=True)) is True
        assert remote_pricing_requested(_settings(remote_pricing=False)) is False
        # A loose mock or a truthy string must not silently move pricing
        # authority to the network.
        assert remote_pricing_requested(SimpleNamespace()) is False
        assert (
            remote_pricing_requested(
                SimpleNamespace(event_payments_remote_pricing_enabled="yes")
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_disabled_leaves_the_mongo_event_untouched(self):
        settings = _settings(remote_pricing=False)
        client = FakeConfigClient()
        event = _event()
        resolved = await resolve_event_pricing(settings, event, client=client)
        assert resolved is event
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_unmapped_event_is_not_repriced(self):
        """The website holds no config for other events, so Mongo is not a
        divergence there -- and it must not be fetched or failed on."""
        settings = _settings()
        client = FakeConfigClient()
        other = _event(_id="000000000000000000000000")
        resolved = await resolve_event_pricing(settings, other, client=client)
        assert resolved is other
        assert client.calls == []


class TestMerge:
    @pytest.mark.asyncio
    async def test_website_config_overrides_the_mongo_formula(self):
        settings = _settings()
        client = FakeConfigClient()
        resolved = await resolve_event_pricing(settings, _event(), client=client)
        assert client.calls == [BOT_EVENT_ID]
        assert resolved["price_formula_base"] == 1600  # website, not Mongo's 1400
        # free_for_types is normalised to the bot's uppercase vocabulary
        assert resolved["free_for_types"] == ["TEACHER", "ORGANIZER"]
        # the ISO wire date becomes a real date; _iso_date() rejects strings
        assert resolved["early_bird_deadline"] == date(2026, 7, 29)
        # the website's own version string lands in the audit snapshot
        assert resolved["pricing_version"] == "website-config-v7"

    @pytest.mark.asyncio
    async def test_non_pricing_event_fields_survive(self):
        settings = _settings()
        event = _event(city="Пермь", guests_enabled=True)
        resolved = await resolve_event_pricing(
            settings, event, client=FakeConfigClient()
        )
        assert resolved["city"] == "Пермь"
        assert resolved["guests_enabled"] is True
        assert resolved["_id"] == BOT_EVENT_ID
        assert event["price_formula_base"] == 1400, "must not mutate the caller's dict"

    def test_the_frozen_amount_follows_the_website(self):
        settings = _settings()
        resolved = merge_remote_pricing(settings, _event(), _config())
        _, expected = build_new_intent_payload(
            settings,
            _registration(),
            resolved,
            calculation_date=date(2026, 7, 20),
        )
        # 1600 + 200*(2026-2010) - 500 early bird
        assert expected == 1600 + 3200 - 500

    @pytest.mark.parametrize(
        "override, code",
        [
            (
                {"bot_event_id": "deadbeefdeadbeefdeadbeef"},
                "remote_config_event_mismatch",
            ),
            ({"website_event_uid": "some-other-event"}, "remote_config_uid_mismatch"),
            ({"website_event_id": 99}, "remote_config_website_id_mismatch"),
            ({"pricing_type": "fixed_by_year"}, "unsupported_remote_pricing_type"),
            ({"pricing_type": "free"}, "unsupported_remote_pricing_type"),
            ({"price_formula_base": 0}, "invalid_remote_config"),
            ({"price_formula_base": "1600"}, "invalid_remote_config"),
            ({"price_formula_base": True}, "invalid_remote_config"),
            ({"price_formula_step": 0}, "invalid_remote_config"),
            ({"early_bird_discount": -100}, "invalid_remote_config"),
            ({"free_for_types": "TEACHER"}, "invalid_remote_config"),
            ({"free_for_types": [1, 2]}, "invalid_remote_config"),
            ({"early_bird_deadline": "not-a-date"}, "invalid_remote_config"),
            ({"early_bird_deadline": 20260729}, "invalid_remote_config"),
        ],
    )
    def test_every_unexpected_config_fails_closed(self, override, code):
        with pytest.raises(WebsiteBridgeError) as excinfo:
            merge_remote_pricing(_settings(), _event(), _config(**override))
        assert excinfo.value.code == code

    def test_a_mistyped_mapping_cannot_price_the_wrong_event(self):
        """The mapping triple is re-checked against the config the website
        actually served. This is the failure a fat-fingered EVENT_PAYMENTS_*
        value produces, and the only one that would silently charge the wrong
        price rather than erroring somewhere visible."""
        settings = _settings()
        settings.event_payments_website_event_uid = "typo-in-coolify-env"
        with pytest.raises(WebsiteBridgeError) as excinfo:
            merge_remote_pricing(settings, _event(), _config())
        assert excinfo.value.code == "remote_config_uid_mismatch"

    @pytest.mark.asyncio
    async def test_an_unreachable_website_does_not_fall_back_to_mongo(self):
        """No fallback on purpose: it would silently charge a stale price, and
        it buys no availability -- the next call is a POST to the same host."""
        client = FakeConfigClient(error=WebsiteBridgeError("http_503"))
        with pytest.raises(WebsiteBridgeError) as excinfo:
            await resolve_event_pricing(_settings(), _event(), client=client)
        assert excinfo.value.code == "http_503"


class TestQuotedPriceGuard:
    """Nobody may be charged an amount other than the one they were shown."""

    async def _freeze(self, registration, event, settings, client=None):
        return await freeze_new_registration_snapshot(
            _app(settings),
            registration,
            event,
            calculation_date=date(2026, 7, 20),
            client=client,
        )

    @pytest.mark.asyncio
    async def test_agreement_freezes_normally(self):
        settings = _settings(remote_pricing=False)
        # 1400 + 200*16 - 500 = 4100
        registration = _registration(discounted_payment_amount=4100)
        payload = await self._freeze(registration, _event(), settings)
        assert payload is not None
        assert payload["formula"]["base_rubles"] == 1400

    @pytest.mark.asyncio
    async def test_disagreement_refuses_to_freeze(self):
        settings = _settings(remote_pricing=False)
        registration = _registration(discounted_payment_amount=3900)
        with pytest.raises(WebsiteBridgeError) as excinfo:
            await self._freeze(registration, _event(), settings)
        assert excinfo.value.code == "quoted_amount_mismatch"

    @pytest.mark.asyncio
    async def test_an_unquoted_registration_is_still_allowed(self):
        """Admin-created and legacy rows never quoted a price, so there is no
        promise to contradict."""
        settings = _settings(remote_pricing=False)
        payload = await self._freeze(_registration(), _event(), settings)
        assert payload is not None

    @pytest.mark.asyncio
    async def test_a_non_integer_quote_is_refused_not_coerced(self):
        settings = _settings(remote_pricing=False)
        registration = _registration(discounted_payment_amount="4100")
        with pytest.raises(WebsiteBridgeError) as excinfo:
            await self._freeze(registration, _event(), settings)
        assert excinfo.value.code == "invalid_quoted_amount"

    @pytest.mark.asyncio
    async def test_stale_mongo_price_is_caught_once_the_website_leads(self):
        """The seam itself: the user was quoted from Mongo (1400 base) and the
        website now says 1600. Without the guard the website would mint an
        intent for 300 more than the bot promised."""
        settings = _settings(remote_pricing=True)
        registration = _registration(discounted_payment_amount=4100)  # Mongo price
        with pytest.raises(WebsiteBridgeError) as excinfo:
            await self._freeze(
                registration, _event(), settings, client=FakeConfigClient()
            )
        assert excinfo.value.code == "quoted_amount_mismatch"


class TestEarlyBirdWindow:
    """The Aug 1 2026 event, exactly as migration
    `summer_2026_food_flag_and_early_bird` leaves it: discount 500, deadline
    29 Jul. The bot's displayed cutoff is 06:00 that morning; the frozen
    formula is re-evaluated by the website for the whole day."""

    @staticmethod
    def _displayed(event, registration, now):
        base = event["price_formula_base"]
        rate = event["price_formula_rate"]
        ref = event["price_formula_reference_year"]
        regular = base + rate * (ref - registration["graduation_year"])
        if is_early_bird_active(event, now=now) and event["early_bird_discount"]:
            return regular - event["early_bird_discount"]
        return regular

    @pytest.mark.parametrize(
        "moment, discounted",
        [
            (datetime(2026, 7, 28, 10, 0), True),
            (datetime(2026, 7, 29, 5, 0), True),
            # 06:00 on the deadline day is the cutoff, shared with food and
            # named badges. This is the instant that used to disagree.
            (datetime(2026, 7, 29, 6, 0), False),
            (datetime(2026, 7, 29, 10, 0), False),
            (datetime(2026, 7, 30, 10, 0), False),
        ],
    )
    def test_displayed_and_frozen_agree_at_every_instant(self, moment, discounted):
        """The 06:00 cutoff is canonical (Petr, 27 Jul 2026). The frozen charge
        must equal the displayed price at every instant, including the one-day
        window that used to disagree: on 29 Jul after 06:00 the bot displayed
        4600 while the intent froze 4100.
        """
        settings = _settings(remote_pricing=False)
        event, registration = _event(), _registration()
        displayed = self._displayed(event, registration, moment)
        _, frozen = build_new_intent_payload(
            settings, registration, event, calculation_date=moment.date(), now=moment
        )
        assert displayed == frozen, (
            f"{moment:%d %b %H:%M}: displayed {displayed}, frozen {frozen}"
        )
        # 1400 + 200*(2026-2010) = 4600, less the 500 early bird while active
        assert frozen == (4100 if discounted else 4600)

    def test_the_deadline_stays_in_the_snapshot_after_it_lapses(self):
        """Zeroing the discount is what makes the website's whole-day
        comparison moot; the deadline itself is still recorded for audit."""
        settings = _settings(remote_pricing=False)
        payload, _ = build_new_intent_payload(
            settings,
            _registration(),
            _event(),
            calculation_date=date(2026, 7, 29),
            now=datetime(2026, 7, 29, 10, 0),
        )
        assert payload["formula"]["early_bird_deadline"] == "2026-07-29"
        assert payload["formula"]["early_bird_discount_rubles"] == 0

    def test_a_discount_with_no_explicit_deadline_uses_the_displayed_fallback(self):
        """`early_bird_deadline_at` falls back to D-3 when only a discount is
        configured. Reading the raw event field instead would display a discount
        and freeze a charge without one -- the same defect, other direction."""
        settings = _settings(remote_pricing=False)
        event = _event()
        del event["early_bird_deadline"]  # discount 500, D-3 of 1 Aug = 29 Jul
        payload, frozen = build_new_intent_payload(
            settings,
            _registration(),
            event,
            calculation_date=date(2026, 7, 28),
            now=datetime(2026, 7, 28, 10, 0),
        )
        assert payload["formula"]["early_bird_deadline"] == "2026-07-29"
        assert frozen == 4100

    @pytest.mark.asyncio
    async def test_a_disagreement_is_still_refused_rather_than_charged(self):
        """Belt and braces: the cutoff now agrees, but if any future change
        reintroduces a gap, the quoted-price guard still stops the charge."""
        settings = _settings(remote_pricing=False)
        event, registration = _event(), _registration()
        moment = datetime(2026, 7, 29, 10, 0)
        # quote the pre-fix (discounted) amount against the post-fix full charge
        registration["discounted_payment_amount"] = 4100
        with pytest.raises(WebsiteBridgeError) as excinfo:
            await freeze_new_registration_snapshot(
                _app(settings),
                registration,
                event,
                calculation_date=moment.date(),
                now=moment,
            )
        assert excinfo.value.code == "quoted_amount_mismatch"
