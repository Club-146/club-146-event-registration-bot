from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from src.website_event_bridge import (
    WebsiteBridgeError,
    WebsiteEventBridgeClient,
    bridge_enabled_for,
    build_new_intent_payload,
    confirm_registration_payment,
    create_or_replay_intent,
    freeze_new_registration_snapshot,
    revoke_before_local_deletion,
    send_website_ticket_links,
    sync_pending_event_payments_once,
    sync_registration_from_website,
)
from src.app import App


BOT_EVENT_ID = "6a599a17a37724d81b7eadc3"
REGISTRATION_ID = "7b699a17a37724d81b7eadc4"


def _settings(*, enabled: bool = True):
    return SimpleNamespace(
        event_payments_bridge_enabled=enabled,
        event_payments_website_api_base_url="https://staging.example.test",
        event_payments_website_api_token=SecretStr("dedicated-test-token"),
        event_payments_website_event_id=1,
        event_payments_website_event_uid="aug1-2026-perm",
        event_payments_bot_event_id=BOT_EVENT_ID,
        event_payments_api_timeout_seconds=1.0,
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


def _registration():
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
        "guests": [
            {"name": "Анна Иванова", "price": 2000, "price_discounted": 1500},
            {"name": "Пётр Петров", "price": 2000, "price_discounted": 1500},
        ],
    }


def _app(*, enabled: bool = True):
    return SimpleNamespace(
        settings=_settings(enabled=enabled),
        collection=SimpleNamespace(update_one=AsyncMock()),
        log_to_chat=AsyncMock(),
    )


def _remote_response(*, status="pending", amount="4500.00", tickets=False):
    paths = [
        "/event-tickets/opaque-person",
        "/event-tickets/opaque-guest-1",
        "/event-tickets/opaque-guest-2",
    ]
    return {
        "status": status,
        "fixed_amount": amount,
        "group_status_path": "/event-pay/opaque-group",
        "admissions": [
            {
                "ordinal": index,
                "kind": "registrant" if index == 0 else "guest",
                "ticket_path": paths[index] if tickets else None,
                "ticket_mode": "test",
                "admission_valid": status in ("paid", "waived"),
                "entry_code": f"ABCD-000{index}",
            }
            for index in range(3)
        ],
    }


class FakeClient:
    def __init__(self):
        self.create_calls = []
        self.confirm_calls = []
        self.revoke_calls = []
        self.create_response = _remote_response()
        self.confirm_response = _remote_response(status="paid", tickets=True)
        self.revoke_response = {
            "status": "cancelled",
            "admissions": [{"admission_valid": False}],
        }

    async def create_or_replay(self, payload):
        self.create_calls.append(payload)
        return self.create_response

    async def confirm(self, source_id, *, paid_amount, evidence_reference):
        self.confirm_calls.append((source_id, paid_amount, evidence_reference))
        return self.confirm_response

    async def revoke(self, source_id, **kwargs):
        self.revoke_calls.append((source_id, kwargs))
        return self.revoke_response


def test_payload_freezes_formula_total_guests_and_legal_kind():
    payload, total = build_new_intent_payload(
        _settings(),
        _registration(),
        _event(),
        calculation_date=date(2026, 7, 20),
        now=datetime(2026, 7, 20, 12),
    )

    # 1400 + 200 * floor((2026 - 2016) / 3) - 500 = 1500,
    # plus two named early-bird guests at 1500 each.
    assert total == 4500
    assert payload["formula"] == {
        "base_rubles": 1400,
        "rate_rubles": 200,
        "reference_year": 2026,
        "graduation_year": 2016,
        "calculation_date": "2026-07-20",
        "step_years": 3,
        "attendee_type": "GRADUATE",
        "guest_price_minimum_rubles": 1500,
        "free_for_types": ["ORGANIZER", "TEACHER"],
        "early_bird_discount_rubles": 500,
        "early_bird_deadline": "2026-07-29",
        "version": payload["formula"]["version"],
    }
    assert payload["legal_kind"] == "unclassified"
    assert [guest["display_name"] for guest in payload["guests"]] == [
        "Анна Иванова",
        "Пётр Петров",
    ]
    assert [guest["fixed_amount_rubles"] for guest in payload["guests"]] == [1500, 1500]


@pytest.mark.asyncio
async def test_replay_uses_persisted_snapshot_not_mutated_event():
    app = _app()
    registration = _registration()
    event = _event()
    client = FakeClient()

    await freeze_new_registration_snapshot(
        app,
        registration,
        event,
        calculation_date=date(2026, 7, 20),
        now=datetime(2026, 7, 20, 12),
    )
    frozen = registration["website_event_bridge"]["intent_payload"]
    event["price_formula_base"] = 999_999

    response = await create_or_replay_intent(app, registration, event, client=client)

    assert response["status"] == "pending"
    assert client.create_calls == [frozen]
    assert client.create_calls[0]["formula"]["base_rubles"] == 1400
    assert (
        registration["website_event_bridge"]["group_status_path"]
        == "/event-pay/opaque-group"
    )


@pytest.mark.asyncio
async def test_confirm_reuses_first_durable_evidence_and_stores_codes():
    app = _app()
    registration = _registration()
    event = _event()
    client = FakeClient()
    await freeze_new_registration_snapshot(
        app,
        registration,
        event,
        calculation_date=date(2026, 7, 20),
        now=datetime(2026, 7, 20, 12),
    )

    await confirm_registration_payment(
        app,
        registration,
        event,
        paid_amount=5000,
        evidence_reference="club146-bot:first-proof",
        client=client,
    )
    await confirm_registration_payment(
        app,
        registration,
        event,
        paid_amount=6000,
        evidence_reference="club146-bot:different-retry",
        client=client,
    )

    assert client.confirm_calls == [
        (REGISTRATION_ID, 5000, "club146-bot:first-proof"),
        (REGISTRATION_ID, 5000, "club146-bot:first-proof"),
    ]
    admissions = registration["website_event_bridge"]["admissions"]
    assert [row["entry_code"] for row in admissions] == [
        "ABCD-0000",
        "ABCD-0001",
        "ABCD-0002",
    ]
    assert registration["ticket_code"] == "ABCD-0000"
    assert all(row["ticket_path"].startswith("/event-tickets/") for row in admissions)


@pytest.mark.asyncio
async def test_revoke_failure_records_retry_and_raises_before_delete():
    app = _app()
    registration = _registration()
    event = _event()
    client = FakeClient()

    async def fail_revoke(*args, **kwargs):
        raise WebsiteBridgeError("timeout")

    client.revoke = fail_revoke

    with pytest.raises(WebsiteBridgeError, match="timeout"):
        await revoke_before_local_deletion(
            app,
            registration,
            event,
            transition_kind="registration_cancelled",
            reason="Участник отменил регистрацию",
            client=client,
        )

    bridge = registration["website_event_bridge"]
    assert bridge["sync_state"] == "revoke_retry_required"
    assert bridge["last_error_code"] == "timeout"
    assert bridge["revocation_reference"] == f"club146-bot:cancel:{REGISTRATION_ID}"
    app.log_to_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_deletion_does_not_archive_when_exact_event_revoke_fails():
    registration = _registration()

    class Cursor:
        async def to_list(self, *, length):
            return [registration]

    app = SimpleNamespace(
        settings=_settings(),
        collection=SimpleNamespace(
            find=MagicMock(return_value=Cursor()), update_one=AsyncMock()
        ),
        get_event_for_registration=AsyncMock(return_value=_event()),
        save_event_log=AsyncMock(),
        move_user_to_deleted=AsyncMock(),
        log_to_chat=AsyncMock(),
    )

    with patch(
        "src.website_event_bridge.WebsiteEventBridgeClient.revoke",
        new_callable=AsyncMock,
        side_effect=WebsiteBridgeError("timeout"),
    ):
        with pytest.raises(WebsiteBridgeError, match="timeout"):
            await App.delete_user_registration(
                app, registration["user_id"], registration["event_id"]
            )

    app.save_event_log.assert_not_awaited()
    app.move_user_to_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_event_deletion_fails_closed_when_event_row_is_missing():
    app = _app()
    registration = _registration()

    with pytest.raises(WebsiteBridgeError, match="mapped_event_unavailable"):
        await revoke_before_local_deletion(
            app,
            registration,
            None,
            transition_kind="registration_cancelled",
            reason="Участник отменил регистрацию",
        )

    assert registration["website_event_bridge"]["sync_state"] == (
        "revoke_retry_required"
    )
    app.log_to_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_ticket_link_ux_is_one_person_per_button():
    app = _app()
    registration = _registration()
    registration["website_event_bridge"] = {
        "remote_status": "paid",
        "admissions": _remote_response(status="paid", tickets=True)["admissions"],
    }

    with patch("src.website_event_bridge.send_safe", new_callable=AsyncMock) as send:
        sent = await send_website_ticket_links(123456789, app, registration)

    assert sent is True
    markup = send.await_args.kwargs["reply_markup"]
    assert [row[0].text for row in markup.inline_keyboard] == [
        "🎟 Иван Иванов",
        "🎟 Анна Иванова",
        "🎟 Пётр Петров",
    ]
    assert all("/event-tickets/" in row[0].url for row in markup.inline_keyboard)


@pytest.mark.asyncio
async def test_feature_off_makes_no_client_call_and_preserves_behavior():
    app = _app(enabled=False)
    registration = _registration()
    event = _event()
    client = FakeClient()

    assert bridge_enabled_for(app.settings, event) is False
    assert await freeze_new_registration_snapshot(app, registration, event) is None
    assert (
        await create_or_replay_intent(app, registration, event, client=client) is None
    )
    assert client.create_calls == []
    app.collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_sync_promotes_only_authoritative_paid_status():
    app = _app()
    registration = _registration()
    event = _event()
    client = FakeClient()
    await freeze_new_registration_snapshot(
        app,
        registration,
        event,
        calculation_date=date(2026, 7, 20),
        now=datetime(2026, 7, 20, 12),
    )
    app.collection.update_one.reset_mock()

    client.create_response = _remote_response(status="pending")
    await sync_registration_from_website(app, registration, event, client=client)
    assert registration["payment_status"] == "not paid"
    assert all(
        "payment_status" not in call.args[1].get("$set", {})
        for call in app.collection.update_one.await_args_list
    )

    app.collection.update_one.reset_mock()
    client.create_response = _remote_response(status="paid", tickets=True)
    await sync_registration_from_website(app, registration, event, client=client)
    assert registration["payment_status"] == "confirmed"
    payment_updates = [
        call.args[1]["$set"]
        for call in app.collection.update_one.await_args_list
        if "payment_status" in call.args[1].get("$set", {})
    ]
    assert payment_updates == [
        {
            "payment_status": "confirmed",
            "payment_amount": 4500,
            "payment_verified_at": payment_updates[0]["payment_verified_at"],
            "payment_confirmation_source": "146.school",
        }
    ]


@pytest.mark.asyncio
async def test_payment_message_has_one_fixed_website_button_and_receipt_fallback():
    from src.routers import payment

    message = MagicMock()
    message.chat.id = 123456789
    payment.app.settings.payment_phone_number = "+7 900 000-00-00"
    payment.app.settings.payment_name = "Мария"
    payment.app.settings.event_payments_website_api_base_url = (
        "https://staging.example.test"
    )
    event = _event()
    event["city"] = "Пермь"
    event["date"] = datetime(2026, 8, 1)

    with (
        patch("src.routers.payment.asyncio.sleep", new_callable=AsyncMock),
        patch("src.routers.payment.send_safe", new_callable=AsyncMock) as send,
    ):
        await payment._send_payment_info_messages(
            message,
            "Пермь",
            event,
            "GRADUATE",
            2000,
            1500,
            [],
            2000,
            1500,
            website_checkout={
                "fixed_amount": "1500.00",
                "group_status_path": "/event-pay/opaque-group",
            },
            website_bridge_active=True,
        )

    final_call = send.await_args_list[-1]
    assert "Запасной вариант" in final_call.args[1]
    markup = final_call.kwargs["reply_markup"]
    assert len(markup.inline_keyboard) == 1
    assert markup.inline_keyboard[0][0].text == "Оплатить 1500 ₽"
    assert markup.inline_keyboard[0][0].url == (
        "https://staging.example.test/event-pay/opaque-group"
    )


@pytest.mark.asyncio
async def test_payment_receipt_flow_keeps_full_group_totals():
    from src.routers import payment

    registration = _registration()
    event = _event()
    event.update({"city": "Пермь", "date": datetime(2026, 8, 1)})
    message = MagicMock()
    message.chat.id = registration["user_id"]
    state = AsyncMock()

    with (
        patch(
            "src.routers.payment._resolve_user_identity",
            new_callable=AsyncMock,
            return_value=(registration["user_id"], "ivan"),
        ),
        patch(
            "src.routers.payment._load_registration_and_event",
            new_callable=AsyncMock,
            return_value=(registration, event),
        ),
        patch("src.routers.payment.bridge_enabled_for", return_value=False),
        patch("src.payment_timeline.is_early_bird_active", return_value=True),
        patch(
            "src.routers.payment._send_payment_info_messages",
            new_callable=AsyncMock,
        ),
        patch(
            "src.routers.payment.ask_user_choice_raw",
            new_callable=AsyncMock,
            return_value="pay_later",
        ),
        patch(
            "src.routers.payment._handle_pay_later", new_callable=AsyncMock
        ) as pay_later,
        patch.object(payment.app, "save_event_log", new_callable=AsyncMock),
    ):
        await payment.process_payment(
            message,
            state,
            BOT_EVENT_ID,
            registration["graduation_year"],
            guests=registration["guests"],
        )

    args = pay_later.await_args.args
    assert args[5:8] == (4500, 6000, 6000)


@pytest.mark.asyncio
async def test_background_sync_pushes_provider_confirmation_exactly_once():
    registration = _registration()
    event = _event()
    app = _app()
    app.get_event_for_registration = AsyncMock(return_value=event)
    app.export_registered_users_to_google_sheets = AsyncMock()

    class Cursor:
        async def to_list(self, *, length):
            return [registration]

    app.collection.find = MagicMock(return_value=Cursor())
    client = FakeClient()
    await freeze_new_registration_snapshot(
        app,
        registration,
        event,
        calculation_date=date(2026, 7, 20),
        now=datetime(2026, 7, 20, 12),
    )
    client.create_response = _remote_response(status="paid", tickets=True)

    with (
        patch("src.website_event_bridge.send_safe", new_callable=AsyncMock) as send,
        patch(
            "src.website_event_bridge.send_website_ticket_links",
            new_callable=AsyncMock,
            return_value=True,
        ) as links,
        patch("src.ticket_cards.send_paid_ticket_card", new_callable=AsyncMock) as card,
    ):
        first = await sync_pending_event_payments_once(app, client=client)
        second = await sync_pending_event_payments_once(app, client=client)

    assert first == 1
    assert second == 0
    assert registration["payment_status"] == "confirmed"
    assert registration["website_event_bridge"]["remote_status"] == "paid"
    assert registration["website_event_bridge"]["paid_notification_sent_at"]
    send.assert_awaited_once()
    assert "подтверждена автоматически" in send.await_args.args[1]
    links.assert_awaited_once_with(registration["user_id"], app, registration)
    card.assert_awaited_once_with(registration["user_id"], registration, event)
    app.export_registered_users_to_google_sheets.assert_awaited_once()
    query = app.collection.find.call_args.args[0]
    assert query["event_id"] == BOT_EVENT_ID
    assert "paid" not in query["website_event_bridge.remote_status"]["$nin"]


@pytest.mark.asyncio
async def test_background_sync_pushes_provider_revocation_once():
    registration = _registration()
    registration["payment_status"] = "confirmed"
    registration["website_event_bridge"] = {
        "intent_payload": {"guests": []},
        "expected_amount_rubles": 1500,
        "remote_status": "paid",
        "paid_notification_sent_at": "2026-07-20T09:00:00+00:00",
    }
    event = _event()
    app = _app()
    app.get_event_for_registration = AsyncMock(return_value=event)
    app.export_registered_users_to_google_sheets = AsyncMock()

    class Cursor:
        async def to_list(self, *, length):
            return [registration]

    app.collection.find = MagicMock(return_value=Cursor())
    client = FakeClient()
    client.create_response = _remote_response(
        status="paid", amount="1500.00", tickets=True
    )
    client.create_response["admissions"] = client.create_response["admissions"][:1]
    client.create_response["status"] = "refunded"
    for admission in client.create_response["admissions"]:
        admission["admission_valid"] = False

    with patch("src.website_event_bridge.send_safe", new_callable=AsyncMock) as send:
        first = await sync_pending_event_payments_once(app, client=client)
        second = await sync_pending_event_payments_once(app, client=client)

    assert first == 1
    assert second == 0
    assert registration["website_event_bridge"]["remote_status"] == "refunded"
    assert registration["website_event_bridge"]["revocation_notification_sent_at"]
    send.assert_awaited_once()
    assert "билеты отозваны" in send.await_args.args[1]


@pytest.mark.asyncio
async def test_status_sync_restores_website_links_and_visual_fallback():
    from src.router import status_handler

    registration = _registration()
    event = _event()
    event.update({"city": "Пермь", "date_display": "1 Августа, Сб"})
    app = SimpleNamespace(
        save_event_log=AsyncMock(),
        get_user_active_registrations=AsyncMock(return_value=[registration]),
        get_event_for_registration=AsyncMock(return_value=event),
        get_enabled_events=AsyncMock(return_value=[event]),
        is_event_passed=MagicMock(return_value=False),
    )
    message = MagicMock()
    message.from_user.id = registration["user_id"]
    message.from_user.username = "ivan"
    message.chat.id = registration["user_id"]
    message.chat.type = "private"
    message.text = "/status"

    async def sync_side_effect(*args, **kwargs):
        registration["payment_status"] = "confirmed"
        registration["website_event_bridge"] = {
            "remote_status": "paid",
            "admissions": _remote_response(status="paid", tickets=True)["admissions"],
        }
        return _remote_response(status="paid", tickets=True)

    with (
        patch(
            "src.router.sync_registration_from_website",
            new_callable=AsyncMock,
            side_effect=sync_side_effect,
        ) as sync,
        patch("src.router.send_website_ticket_links", new_callable=AsyncMock) as links,
        patch("src.router.send_paid_ticket_card", new_callable=AsyncMock) as card,
        patch("src.router.send_safe", new_callable=AsyncMock) as send,
    ):
        await status_handler(message, AsyncMock(), app)

    sync.assert_awaited_once_with(app, registration, event)
    links.assert_awaited_once_with(message.chat.id, app, registration)
    card.assert_awaited_once_with(message.chat.id, registration, event)
    assert "Оплачено" in send.await_args_list[-1].args[1]


@pytest.mark.asyncio
async def test_status_never_sends_entry_card_for_remote_pending_or_revoked():
    from src.router import status_handler

    registration = _registration()
    registration["payment_status"] = "confirmed"
    event = _event()
    event.update({"city": "Пермь", "date_display": "1 Августа, Сб"})
    app = SimpleNamespace(
        save_event_log=AsyncMock(),
        get_user_active_registrations=AsyncMock(return_value=[registration]),
        get_event_for_registration=AsyncMock(return_value=event),
        get_enabled_events=AsyncMock(return_value=[event]),
        is_event_passed=MagicMock(return_value=False),
    )
    message = MagicMock()
    message.from_user.id = registration["user_id"]
    message.from_user.username = "ivan"
    message.chat.id = registration["user_id"]
    message.chat.type = "private"
    message.text = "/status"

    for remote_status in ("pending", "cancelled"):
        registration["website_event_bridge"] = {"remote_status": remote_status}
        with (
            patch(
                "src.router.sync_registration_from_website",
                new_callable=AsyncMock,
            ),
            patch("src.router.send_website_ticket_links", new_callable=AsyncMock),
            patch("src.router.send_paid_ticket_card", new_callable=AsyncMock) as card,
            patch("src.router.send_safe", new_callable=AsyncMock) as send,
        ):
            await status_handler(message, AsyncMock(), app)

        card.assert_not_awaited()
        status_text = send.await_args_list[-1].args[1]
        assert "Именной билет действителен для входа" not in status_text


@pytest.mark.asyncio
async def test_http_client_never_exposes_token_in_bridge_error():
    client = WebsiteEventBridgeClient(_settings())
    request = MagicMock()
    response = MagicMock(status_code=503, request=request)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
        with pytest.raises(WebsiteBridgeError) as error:
            await client.create_or_replay({"safe": "payload"})

    assert error.value.code == "http_503"
    assert "dedicated-test-token" not in str(error.value)
