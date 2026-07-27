"""End-to-end bridge tests against the local mock website HTTP API.

Spins ``dev.mock_website_event_payments`` on an ephemeral loopback port and
drives the real ``WebsiteEventBridgeClient`` (no FakeClient).
"""

from __future__ import annotations

import threading
from datetime import date, datetime
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from dev.mock_website_event_payments.server import make_handler
from dev.mock_website_event_payments.service import (
    MockConfig,
    MockWebsiteEventPaymentService,
)
from src.website_event_bridge import (
    WebsiteEventBridgeClient,
    confirm_registration_payment,
    create_or_replay_intent,
    freeze_new_registration_snapshot,
    revoke_before_local_deletion,
    sync_pending_event_payments_once,
    sync_registration_from_website,
)


BOT_EVENT_ID = "6a599a17a37724d81b7eadc3"
REGISTRATION_ID = "7b699a17a37724d81b7eadc4"
TOKEN = "e2e-dedicated-token"


@pytest.fixture
def mock_service():
    return MockWebsiteEventPaymentService(
        MockConfig(
            website_event_id=1,
            website_event_uid="aug1-2026-perm",
            bot_event_id=BOT_EVENT_ID,
            api_token=TOKEN,
        )
    )


@pytest.fixture
def mock_http(mock_service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mock_service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        yield base_url, mock_service
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _settings(base_url: str):
    return SimpleNamespace(
        event_payments_bridge_enabled=True,
        event_payments_website_api_base_url=base_url,
        event_payments_website_api_token=SecretStr(TOKEN),
        event_payments_website_event_id=1,
        event_payments_website_event_uid="aug1-2026-perm",
        event_payments_bot_event_id=BOT_EVENT_ID,
        event_payments_api_timeout_seconds=2.0,
    )


def _event():
    return {
        "_id": BOT_EVENT_ID,
        "pricing_type": "formula",
        "price_formula_base": 1400,
        "price_formula_rate": 200,
        "price_formula_reference_year": 2026,
        "price_formula_step": 3,
        "guest_price_minimum": 1500,
        "free_for_types": ["TEACHER", "ORGANIZER"],
        "early_bird_discount": 500,
        "early_bird_deadline": datetime(2026, 7, 29),
    }


def _registration(*, with_priced_guest: bool = True):
    guests = []
    if with_priced_guest:
        guests = [
            {
                "name": "Анна Иванова",
                "graduation_year": 2018,
                "class_letter": "Б",
                "graduate_type": "GRADUATE",
                "price": 2000,
                "price_discounted": 1500,
            }
        ]
    return {
        "_id": REGISTRATION_ID,
        "event_id": BOT_EVENT_ID,
        "user_id": 123456789,
        "full_name": "Иван Иванов",
        "graduation_year": 2016,
        "class_letter": "А",
        "target_city": "Пермь",
        "graduate_type": "GRADUATE",
        "payment_status": "not paid",
        "guests": guests,
    }


def _app(base_url: str):
    return SimpleNamespace(
        settings=_settings(base_url),
        collection=SimpleNamespace(update_one=AsyncMock()),
        log_to_chat=AsyncMock(),
        get_event_for_registration=AsyncMock(return_value=_event()),
        export_registered_users_to_google_sheets=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_e2e_create_replay_confirm_revoke_over_http(mock_http):
    base_url, service = mock_http
    app = _app(base_url)
    registration = _registration()
    event = _event()
    client = WebsiteEventBridgeClient(app.settings)

    await freeze_new_registration_snapshot(
        app, registration, event, calculation_date=date(2026, 7, 20)
    )
    # 1400 + 200*floor(10/3) - 500 = 1500 registrant + 1500 guest
    assert registration["website_event_bridge"]["expected_amount_rubles"] == 3000

    created = await create_or_replay_intent(app, registration, event, client=client)
    assert created["status"] == "pending"
    assert created["fixed_amount"] == "3000.00"
    assert len(created["admissions"]) == 2
    assert REGISTRATION_ID in service.intents

    # Exact replay is idempotent.
    replayed = await create_or_replay_intent(app, registration, event, client=client)
    assert replayed["status"] == "pending"
    assert len(service.intents) == 1

    confirmed = await confirm_registration_payment(
        app,
        registration,
        event,
        paid_amount=3000,
        evidence_reference="club146-bot:e2e:confirm-1",
        client=client,
    )
    assert confirmed["status"] == "paid"
    assert all(row["admission_valid"] for row in confirmed["admissions"])
    assert all(row["entry_code"] for row in confirmed["admissions"])
    assert registration["ticket_code"]

    revoked = await revoke_before_local_deletion(
        app,
        registration,
        event,
        transition_kind="registration_cancelled",
        reason="e2e cancel",
        client=client,
    )
    assert revoked["status"] == "cancelled"
    assert all(not row["admission_valid"] for row in revoked["admissions"])


@pytest.mark.asyncio
async def test_e2e_provider_paid_sync_promotes_local_status(mock_http):
    base_url, service = mock_http
    app = _app(base_url)
    registration = _registration(with_priced_guest=False)
    event = _event()
    client = WebsiteEventBridgeClient(app.settings)

    await freeze_new_registration_snapshot(
        app, registration, event, calculation_date=date(2026, 7, 20)
    )
    await create_or_replay_intent(app, registration, event, client=client)
    service.mark_provider_paid(REGISTRATION_ID)

    response = await sync_registration_from_website(
        app, registration, event, client=client
    )
    assert response["status"] == "paid"
    assert registration["payment_status"] == "confirmed"
    payment_sets = [
        call.args[1]["$set"]
        for call in app.collection.update_one.await_args_list
        if "payment_status" in call.args[1].get("$set", {})
    ]
    assert payment_sets
    assert payment_sets[-1]["payment_confirmation_source"] == "146.school"


@pytest.mark.asyncio
async def test_e2e_background_sync_notifies_once(mock_http):
    base_url, service = mock_http
    app = _app(base_url)
    registration = _registration(with_priced_guest=False)
    event = _event()
    client = WebsiteEventBridgeClient(app.settings)

    class Cursor:
        async def to_list(self, *, length):
            return [registration]

    app.collection.find = lambda *a, **k: Cursor()

    await freeze_new_registration_snapshot(
        app, registration, event, calculation_date=date(2026, 7, 20)
    )
    await create_or_replay_intent(app, registration, event, client=client)
    service.mark_provider_paid(REGISTRATION_ID)

    with (
        patch("src.website_event_bridge.send_safe", new_callable=AsyncMock) as send,
        patch(
            "src.website_event_bridge.send_website_ticket_links",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("src.ticket_cards.send_paid_ticket_card", new_callable=AsyncMock),
    ):
        first = await sync_pending_event_payments_once(app, client=client)
        second = await sync_pending_event_payments_once(app, client=client)

    assert first == 1
    assert second == 0
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_e2e_zero_price_guest_rejected_by_mock(mock_http):
    base_url, _service = mock_http
    app = _app(base_url)
    registration = _registration()
    registration["guests"] = [
        {
            "name": "Free Guest",
            "graduation_year": 2010,
            "class_letter": "А",
            "price": 0,
            "price_discounted": 0,
        }
    ]
    event = _event()

    with pytest.raises(Exception) as exc:
        await freeze_new_registration_snapshot(
            app, registration, event, calculation_date=date(2026, 7, 20)
        )
    assert "invalid_guest_price" in str(exc.value)


@pytest.mark.asyncio
async def test_e2e_manual_admin_registration_shape_freezes(mock_http):
    """Manual admin row (user_id=None) + priced guest still builds a valid intent."""
    base_url, service = mock_http
    app = _app(base_url)
    registration = _registration()
    registration["user_id"] = None
    registration["start_source"] = "manual_admin"
    event = _event()
    client = WebsiteEventBridgeClient(app.settings)

    await freeze_new_registration_snapshot(
        app, registration, event, calculation_date=date(2026, 7, 20)
    )
    response = await create_or_replay_intent(app, registration, event, client=client)
    assert response["status"] == "pending"
    stored = service.intents[REGISTRATION_ID]
    assert stored.payload["registrant_telegram_id"] is None
    assert len(stored.admissions) == 2


@pytest.mark.asyncio
async def test_loopback_http_base_url_is_accepted(mock_http):
    base_url, _ = mock_http
    client = WebsiteEventBridgeClient(_settings(base_url))
    assert client.base_url.startswith("http://127.0.0.1:")


def test_non_loopback_http_base_url_rejected():
    from src.website_event_bridge import WebsiteBridgeError, _validated_base_url

    with pytest.raises(WebsiteBridgeError, match="invalid_api_base_url"):
        _validated_base_url("http://staging.example.test")
