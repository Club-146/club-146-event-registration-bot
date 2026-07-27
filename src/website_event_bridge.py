"""Disabled-by-default bridge from the Aug 1 registry bot to 146.school.

The website owns payment-intent and admission-ticket state. The bot sends only
an immutable pricing snapshot captured during a *new* registration. Existing
rows without that snapshot are never reconstructed from mutable event config.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger

from botspot.utils import send_safe


REMOTE_PAID_STATUSES = frozenset({"paid", "waived"})
REMOTE_FINAL_REVOKED_STATUSES = frozenset({"cancelled", "refunded"})
REMOTE_STATUSES = frozenset(
    {"pending", *REMOTE_PAID_STATUSES, *REMOTE_FINAL_REVOKED_STATUSES}
)
ENTRY_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{7,39}$")
PRICING_TIMEZONE = ZoneInfo("Europe/Moscow")


class WebsiteBridgeError(RuntimeError):
    """Safe bridge error whose code contains no credential or opaque URL."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WebsiteSnapshotRequired(WebsiteBridgeError):
    """A legacy row cannot be reconstructed through the normal checkout path."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _event_id(event: dict | None) -> str:
    return str((event or {}).get("_id", ""))


def _source_registration_id(registration: dict) -> str:
    source_id = str(registration.get("_id", "")).strip()
    if not 8 <= len(source_id) <= 120:
        raise WebsiteBridgeError("invalid_source_registration_id")
    return source_id


def _configured(settings: Any) -> bool:
    """Use identity checks so loose MagicMock settings stay disabled in tests."""
    if getattr(settings, "event_payments_bridge_enabled", False) is not True:
        return False
    token = getattr(settings, "event_payments_website_api_token", None)
    get_secret_value = getattr(token, "get_secret_value", None)
    token_value = get_secret_value() if callable(get_secret_value) else ""
    try:
        _validated_base_url(
            getattr(settings, "event_payments_website_api_base_url", "")
        )
    except WebsiteBridgeError:
        return False
    return bool(
        token_value
        and getattr(settings, "event_payments_website_event_id", None)
        and _clean(getattr(settings, "event_payments_website_event_uid", ""))
        and _clean(getattr(settings, "event_payments_bot_event_id", ""))
    )


def _validated_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise WebsiteBridgeError("invalid_api_base_url")
    # Production/staging always HTTPS. Plain HTTP is accepted only on loopback
    # so the local mock server (dev/mock_website_event_payments) can be used
    # without certificates. Never for remote hosts.
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "https":
        return raw
    if parsed.scheme == "http" and loopback:
        return raw
    raise WebsiteBridgeError("invalid_api_base_url")


def bridge_requested(settings: Any) -> bool:
    """True only for an explicit literal enable flag."""
    return getattr(settings, "event_payments_bridge_enabled", False) is True


def bridge_enabled_for(settings: Any, event: dict | None) -> bool:
    """Return true only for the one explicitly mapped bot event."""
    if not bridge_requested(settings):
        return False
    if not _configured(settings):
        raise WebsiteBridgeError("bridge_configuration_incomplete")
    return _event_id(event) == _clean(
        getattr(settings, "event_payments_bot_event_id", "")
    )


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise WebsiteBridgeError("invalid_event_date")


def _pricing_version(event: dict, calculation_date: date) -> str:
    explicit = _clean(event.get("pricing_version"))
    if explicit:
        return explicit[:80]
    versioned = {
        "base": event.get("price_formula_base"),
        "rate": event.get("price_formula_rate"),
        "reference_year": event.get("price_formula_reference_year"),
        "step": event.get("price_formula_step", 1),
        "guest_minimum": event.get("guest_price_minimum", 0),
        "free_for_types": sorted(event.get("free_for_types", [])),
        "early_bird_discount": event.get("early_bird_discount", 0),
        "early_bird_deadline": _iso_date(event.get("early_bird_deadline")),
        "calculation_date": calculation_date.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(versioned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return f"registry-bot-formula-v1:{digest}"


def _formula_amount(formula: dict) -> int:
    attendee_type = formula["attendee_type"]
    if attendee_type in formula["free_for_types"]:
        return 0
    years_since = max(0, formula["reference_year"] - formula["graduation_year"])
    amount = formula["base_rubles"] + formula["rate_rubles"] * (
        years_since // formula["step_years"]
    )
    if attendee_type == "NON_GRADUATE":
        minimum = formula["guest_price_minimum_rubles"]
        amount = minimum or (
            formula["base_rubles"]
            + formula["rate_rubles"] * (15 // formula["step_years"])
        )
    deadline = formula["early_bird_deadline"]
    if (
        deadline
        and formula["calculation_date"] <= deadline
        and formula["early_bird_discount_rubles"] > 0
    ):
        amount -= formula["early_bird_discount_rubles"]
    return max(0, amount)


def build_new_intent_payload(
    settings: Any,
    registration: dict,
    event: dict,
    *,
    calculation_date: date,
) -> tuple[dict, int]:
    """Build the one payload that will be persisted and replayed verbatim."""
    if not bridge_enabled_for(settings, event):
        raise WebsiteBridgeError("bridge_not_enabled_for_event")
    if event.get("pricing_type", "formula") != "formula":
        raise WebsiteBridgeError("unsupported_pricing_type")

    source_id = _source_registration_id(registration)
    bot_event_id = _event_id(event)
    attendee_type = _clean(registration.get("graduate_type") or "GRADUATE").upper()
    formula = {
        "base_rubles": int(event.get("price_formula_base", 0)),
        "rate_rubles": int(event.get("price_formula_rate", 0)),
        "reference_year": int(event.get("price_formula_reference_year", 2026)),
        "graduation_year": int(registration["graduation_year"]),
        "calculation_date": calculation_date.isoformat(),
        "step_years": int(event.get("price_formula_step", 1)),
        "attendee_type": attendee_type,
        "guest_price_minimum_rubles": int(event.get("guest_price_minimum", 0)),
        "free_for_types": sorted(
            _clean(value).upper() for value in event.get("free_for_types", [])
        ),
        "early_bird_discount_rubles": int(event.get("early_bird_discount", 0)),
        "early_bird_deadline": _iso_date(event.get("early_bird_deadline")),
        "version": _pricing_version(event, calculation_date),
    }
    if formula["base_rubles"] < 1 or formula["step_years"] < 1:
        raise WebsiteBridgeError("invalid_formula")

    early_bird = bool(
        formula["early_bird_deadline"]
        and formula["calculation_date"] <= formula["early_bird_deadline"]
        and formula["early_bird_discount_rubles"] > 0
    )
    guests = []
    for guest in registration.get("guests", []):
        amount = guest.get("price_discounted") if early_bird else guest.get("price")
        if amount is None:
            amount = guest.get("price")
        amount = int(amount or 0)
        if amount < 1:
            raise WebsiteBridgeError("invalid_guest_price")
        # Website GuestTerms still accepts name+fixed amount only. Graduation
        # year/letter are bot-side provenance for pricing and display; they are
        # not part of the wire payload until the website expands GuestTerms.
        guests.append(
            {
                "display_name": _clean(guest.get("name")),
                "fixed_amount_rubles": amount,
                "pricing_version": f"{formula['version']}.guest"[:80],
            }
        )
    if len(guests) > 3 or any(not guest["display_name"] for guest in guests):
        raise WebsiteBridgeError("invalid_guests")

    expected_amount = _formula_amount(formula) + sum(
        guest["fixed_amount_rubles"] for guest in guests
    )
    # TODO(source-vocabulary): website still hardcodes
    # source_system="club146_registry_bot" and the create payload has no
    # source_system field. Shared dictionary (telegram_bot / website / vk /
    # manual_admin) is not landed on the website yet — do not send a new value
    # unilaterally; a mismatch fails the mapping check closed. Coordinate with
    # the website session before changing this.
    payload = {
        "website_event_id": int(settings.event_payments_website_event_id),
        "website_event_uid": _clean(settings.event_payments_website_event_uid),
        "bot_event_id": bot_event_id,
        "source_registration_id": source_id,
        "registrant_name": _clean(registration.get("full_name")),
        "registrant_telegram_id": registration.get("user_id"),
        "payer_email": None,
        "legal_kind": "unclassified",
        "formula": formula,
        "guests": guests,
    }
    if not payload["registrant_name"]:
        raise WebsiteBridgeError("invalid_registrant_name")
    return payload, expected_amount


async def _set_fields(app: Any, registration: dict, fields: dict[str, Any]) -> None:
    source_id = _source_registration_id(registration)
    mongo_fields = {
        f"website_event_bridge.{key}": value for key, value in fields.items()
    }
    await app.collection.update_one(
        {"_id": registration["_id"], "event_id": registration.get("event_id")},
        {"$set": mongo_fields},
    )
    bridge = registration.setdefault("website_event_bridge", {})
    bridge.update(fields)
    bridge["source_registration_id"] = source_id


async def freeze_new_registration_snapshot(
    app: Any,
    registration: dict,
    event: dict | None,
    *,
    calculation_date: date | None = None,
) -> dict | None:
    """Persist full pricing provenance only from the live registration flow."""
    if not bridge_enabled_for(app.settings, event):
        return None
    existing = registration.get("website_event_bridge", {}).get("intent_payload")
    if isinstance(existing, dict):
        return existing
    assert event is not None
    moment = calculation_date or datetime.now(PRICING_TIMEZONE).date()
    payload, expected = build_new_intent_payload(
        app.settings, registration, event, calculation_date=moment
    )
    await _set_fields(
        app,
        registration,
        {
            "source_registration_id": payload["source_registration_id"],
            "intent_payload": payload,
            "expected_amount_rubles": expected,
            "sync_state": "frozen",
            "frozen_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return payload


def _safe_path(value: Any, prefix: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    path = str(value or "")
    if (
        not path.startswith(prefix)
        or "?" in path
        or "#" in path
        or "://" in path
        or len(path) > 512
        or any(character.isspace() for character in path)
    ):
        raise WebsiteBridgeError("invalid_opaque_path")
    return path


def _normalise_admissions(raw: Any, *, expected_count: int | None) -> list[dict]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        raise WebsiteBridgeError("invalid_admissions")
    if expected_count is not None and len(raw) != expected_count:
        raise WebsiteBridgeError("admission_count_mismatch")
    admissions = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise WebsiteBridgeError("invalid_admissions")
        try:
            ordinal = int(value.get("ordinal", index))
        except (TypeError, ValueError) as exc:
            raise WebsiteBridgeError("invalid_admission_ordinals") from exc
        code = value.get("entry_code", value.get("ticket_code"))
        if code is not None:
            code = _clean(code)
            if not ENTRY_CODE_RE.fullmatch(code):
                raise WebsiteBridgeError("invalid_entry_code")
        admissions.append(
            {
                "ordinal": ordinal,
                "kind": _clean(value.get("kind")),
                "ticket_path": _safe_path(
                    value.get("ticket_path"), "/event-tickets/", optional=True
                ),
                "entry_code": code or None,
                "ticket_mode": _clean(value.get("ticket_mode")),
                "admission_valid": value.get("admission_valid") is True,
            }
        )
    if [row["ordinal"] for row in admissions] != list(range(len(admissions))):
        raise WebsiteBridgeError("invalid_admission_ordinals")
    if admissions[0]["kind"] != "registrant" or any(
        row["kind"] != "guest" for row in admissions[1:]
    ):
        raise WebsiteBridgeError("invalid_admission_kinds")
    return admissions


def _normalise_response(
    raw: Any,
    *,
    expected_amount: int | None,
    expected_admission_count: int | None,
) -> dict:
    if not isinstance(raw, dict):
        raise WebsiteBridgeError("invalid_response")
    status = _clean(raw.get("status"))
    if status not in REMOTE_STATUSES:
        raise WebsiteBridgeError("invalid_remote_status")
    amount = raw.get("fixed_amount")
    try:
        remote_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise WebsiteBridgeError("invalid_remote_amount") from exc
    if expected_amount is not None:
        if remote_amount != Decimal(expected_amount).quantize(Decimal("0.01")):
            raise WebsiteBridgeError("remote_amount_mismatch")
    return {
        "status": status,
        "fixed_amount": str(remote_amount),
        "group_status_path": _safe_path(raw.get("group_status_path"), "/event-pay/"),
        "admissions": _normalise_admissions(
            raw.get("admissions"), expected_count=expected_admission_count
        ),
    }


async def _persist_remote_response(
    app: Any, registration: dict, response: dict
) -> None:
    await _set_fields(
        app,
        registration,
        {
            "sync_state": "synced",
            "last_error_code": None,
            "remote_status": response["status"],
            "group_status_path": response["group_status_path"],
            "admissions": response["admissions"],
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    primary_code = response["admissions"][0].get("entry_code")
    if primary_code:
        await app.collection.update_one(
            {"_id": registration["_id"], "event_id": registration.get("event_id")},
            {"$set": {"ticket_code": primary_code}},
        )
        registration["ticket_code"] = primary_code


class WebsiteEventBridgeClient:
    """Small authenticated client that never logs request headers or bodies."""

    def __init__(self, settings: Any):
        self.base_url = _validated_base_url(
            settings.event_payments_website_api_base_url
        )
        token = settings.event_payments_website_api_token
        self.token = token.get_secret_value()
        self.timeout = float(settings.event_payments_api_timeout_seconds)
        if not 0 < self.timeout <= 30:
            raise WebsiteBridgeError("invalid_timeout")

    async def _post(self, endpoint: str, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False
            ) as client:
                response = await client.post(
                    f"{self.base_url}{endpoint}", json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise WebsiteBridgeError("timeout") from exc
        except httpx.HTTPError as exc:
            raise WebsiteBridgeError("network_error") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise WebsiteBridgeError(f"http_{response.status_code}")
        try:
            value = response.json()
        except ValueError as exc:
            raise WebsiteBridgeError("invalid_json") from exc
        if not isinstance(value, dict):
            raise WebsiteBridgeError("invalid_json")
        return value

    async def create_or_replay(self, payload: dict) -> dict:
        return await self._post("/api/internal/event-payment-intents", payload)

    async def confirm(
        self, source_id: str, *, paid_amount: int, evidence_reference: str
    ) -> dict:
        source = quote(source_id, safe="")
        return await self._post(
            f"/api/internal/event-payment-intents/{source}/confirm",
            {"paid_amount": paid_amount, "evidence_reference": evidence_reference},
        )

    async def revoke(
        self,
        source_id: str,
        *,
        transition_kind: str,
        reason: str,
        audit_reference: str,
        occurred_at: str,
    ) -> dict:
        source = quote(source_id, safe="")
        return await self._post(
            f"/api/internal/event-payment-intents/{source}/revoke",
            {
                "transition_kind": transition_kind,
                "reason": reason,
                "audit_reference": audit_reference,
                "occurred_at": occurred_at,
            },
        )


async def _record_failure(
    app: Any, registration: dict, *, operation: str, code: str
) -> None:
    await _set_fields(
        app,
        registration,
        {
            "sync_state": f"{operation}_retry_required",
            "last_error_code": code,
            "last_sync_attempt_at": datetime.now(timezone.utc).isoformat(),
        },
    )


async def create_or_replay_intent(
    app: Any,
    registration: dict,
    event: dict | None,
    *,
    client: WebsiteEventBridgeClient | None = None,
) -> dict | None:
    """Replay only the immutable payload already stored on the registration."""
    if not bridge_enabled_for(app.settings, event):
        return None
    bridge = registration.get("website_event_bridge", {})
    payload = bridge.get("intent_payload")
    if not isinstance(payload, dict):
        await _record_failure(
            app, registration, operation="create", code="snapshot_required"
        )
        raise WebsiteSnapshotRequired("snapshot_required")
    expected = bridge.get("expected_amount_rubles")
    try:
        expected_amount = int(expected)
    except (TypeError, ValueError) as exc:
        await _record_failure(
            app, registration, operation="create", code="snapshot_amount_required"
        )
        raise WebsiteSnapshotRequired("snapshot_amount_required") from exc
    try:
        raw = await (client or WebsiteEventBridgeClient(app.settings)).create_or_replay(
            payload
        )
        response = _normalise_response(
            raw,
            expected_amount=expected_amount,
            expected_admission_count=1 + len(payload.get("guests", [])),
        )
    except WebsiteBridgeError as exc:
        await _record_failure(app, registration, operation="create", code=exc.code)
        logger.warning("Website event bridge create/replay failed: {}", exc.code)
        raise
    await _persist_remote_response(app, registration, response)
    return response


def confirmation_reference(
    registration: dict, *, channel: str, chat_id: int, message_id: int
) -> str:
    """Globally unique, stable evidence key derived from a Telegram action."""
    source = _source_registration_id(registration)
    raw = f"club146-bot:{channel}:{source}:{chat_id}:{message_id}"
    return raw[:160]


async def confirm_registration_payment(
    app: Any,
    registration: dict,
    event: dict | None,
    *,
    paid_amount: int,
    evidence_reference: str,
    client: WebsiteEventBridgeClient | None = None,
) -> dict | None:
    if not bridge_enabled_for(app.settings, event):
        return None
    bridge = registration.setdefault("website_event_bridge", {})
    stored_reference = bridge.get("confirmation_reference")
    stored_amount = bridge.get("confirmation_amount_rubles")
    if stored_reference:
        try:
            stored_amount_int = int(stored_amount)
        except (TypeError, ValueError) as exc:
            await _record_failure(
                app,
                registration,
                operation="confirm",
                code="confirmation_amount_required",
            )
            raise WebsiteBridgeError("confirmation_amount_required") from exc
        evidence_reference = stored_reference
        paid_amount = stored_amount_int
    else:
        await _set_fields(
            app,
            registration,
            {
                "confirmation_reference": evidence_reference,
                "confirmation_amount_rubles": int(paid_amount),
                "sync_state": "confirm_pending",
            },
        )
    await create_or_replay_intent(app, registration, event, client=client)
    try:
        raw = await (client or WebsiteEventBridgeClient(app.settings)).confirm(
            _source_registration_id(registration),
            paid_amount=int(paid_amount),
            evidence_reference=evidence_reference,
        )
        payload = bridge.get("intent_payload", {})
        response = _normalise_response(
            raw,
            expected_amount=int(bridge["expected_amount_rubles"]),
            expected_admission_count=1 + len(payload.get("guests", [])),
        )
        if response["status"] not in REMOTE_PAID_STATUSES:
            raise WebsiteBridgeError("confirm_not_paid")
    except WebsiteBridgeError as exc:
        await _record_failure(app, registration, operation="confirm", code=exc.code)
        logger.warning("Website event bridge confirmation failed: {}", exc.code)
        raise
    await _persist_remote_response(app, registration, response)
    return response


async def sync_registration_from_website(
    app: Any,
    registration: dict,
    event: dict | None,
    *,
    client: WebsiteEventBridgeClient | None = None,
) -> dict | None:
    """Replay status and only promote local payment from website paid/waived."""
    response = await create_or_replay_intent(app, registration, event, client=client)
    if response is None:
        return None
    bridge = registration.get("website_event_bridge", {})
    if (
        response["status"] == "pending"
        and bridge.get("confirmation_reference")
        and bridge.get("confirmation_amount_rubles") is not None
    ):
        confirmed_response = await confirm_registration_payment(
            app,
            registration,
            event,
            paid_amount=int(bridge["confirmation_amount_rubles"]),
            evidence_reference=str(bridge["confirmation_reference"]),
            client=client,
        )
        if confirmed_response is None:
            raise WebsiteBridgeError("confirm_response_missing")
        response = confirmed_response
    if (
        response["status"] in REMOTE_PAID_STATUSES
        and registration.get("payment_status") != "confirmed"
    ):
        amount = int(
            Decimal(str(registration["website_event_bridge"]["expected_amount_rubles"]))
        )
        await app.collection.update_one(
            {"_id": registration["_id"], "payment_status": {"$ne": "confirmed"}},
            {
                "$set": {
                    "payment_status": "confirmed",
                    "payment_amount": amount,
                    "payment_verified_at": datetime.now(timezone.utc).isoformat(),
                    "payment_confirmation_source": "146.school",
                }
            },
        )
        registration["payment_status"] = "confirmed"
        registration["payment_amount"] = amount
    return response


async def _notify_automatic_confirmation(
    app: Any,
    registration: dict,
    event: dict | None,
    response: dict,
) -> bool:
    """Push one website-confirmed payment and its tickets exactly once."""
    bridge = registration.get("website_event_bridge", {})
    if bridge.get("paid_notification_sent_at"):
        return False
    if response.get("status") not in REMOTE_PAID_STATUSES:
        return False
    user_id = registration.get("user_id")
    if not isinstance(user_id, int) or user_id <= 0:
        raise WebsiteBridgeError("invalid_telegram_user_id")

    amount = int(bridge.get("expected_amount_rubles") or 0)
    if response["status"] == "waived":
        message = "✅ Взнос для участия не требуется. Ваши именные билеты готовы."
    else:
        message = (
            f"✅ Оплата участия {amount} ₽ подтверждена автоматически. "
            "Ваши именные билеты готовы."
        )
    await send_safe(user_id, message)
    await send_website_ticket_links(user_id, app, registration)

    # Keep the existing Telegram PNG as a recovery/display fallback. Website
    # admission links remain authoritative for the provider-paid path.
    from src.ticket_cards import send_paid_ticket_card

    await send_paid_ticket_card(user_id, registration, event)
    sent_at = datetime.now(timezone.utc).isoformat()
    await _set_fields(
        app,
        registration,
        {
            "paid_notification_sent_at": sent_at,
            "paid_notification_status": response["status"],
            "sync_state": "paid_notified",
        },
    )
    return True


async def _notify_automatic_revocation(
    app: Any,
    registration: dict,
    response: dict,
) -> bool:
    bridge = registration.get("website_event_bridge", {})
    if response.get("status") not in REMOTE_FINAL_REVOKED_STATUSES:
        return False
    if bridge.get("revocation_notification_sent_at"):
        return False
    user_id = registration.get("user_id")
    if not isinstance(user_id, int) or user_id <= 0:
        raise WebsiteBridgeError("invalid_telegram_user_id")
    await send_safe(
        user_id,
        "↩️ Оплата мероприятия отменена или возвращена. "
        "Именные билеты отозваны и больше не действуют.",
    )
    await _set_fields(
        app,
        registration,
        {
            "revocation_notification_sent_at": datetime.now(timezone.utc).isoformat(),
            "sync_state": "revocation_notified",
        },
    )
    return True


async def sync_pending_event_payments_once(
    app: Any,
    *,
    client: WebsiteEventBridgeClient | None = None,
) -> int:
    """Poll frozen intents once and push newly authoritative paid states."""
    if not bridge_requested(app.settings):
        return 0
    if not _configured(app.settings):
        raise WebsiteBridgeError("bridge_configuration_incomplete")

    event_id = _clean(app.settings.event_payments_bot_event_id)
    cursor = app.collection.find(
        {
            "event_id": event_id,
            "website_event_bridge.intent_payload": {"$exists": True},
            # Keep paid rows in the small polling set so a later provider refund
            # or reversal can still revoke local ticket presentation.
            "website_event_bridge.remote_status": {
                "$nin": sorted(REMOTE_FINAL_REVOKED_STATUSES)
            },
        }
    )
    registrations = await cursor.to_list(length=None)
    notified = 0
    shared_client = client or WebsiteEventBridgeClient(app.settings)
    for registration in registrations:
        try:
            event = await app.get_event_for_registration(registration)
            response = await sync_registration_from_website(
                app, registration, event, client=shared_client
            )
            if response:
                if await _notify_automatic_confirmation(
                    app, registration, event, response
                ):
                    notified += 1
                elif await _notify_automatic_revocation(app, registration, response):
                    notified += 1
        except WebsiteBridgeError as exc:
            logger.warning(
                "Automatic website payment sync failed for one registration: {}",
                exc.code,
            )
        except Exception:
            logger.exception("Automatic website payment sync failed unexpectedly")

    if notified and hasattr(app, "export_registered_users_to_google_sheets"):
        await app.export_registered_users_to_google_sheets()
    return notified


async def run_payment_sync_loop(app: Any) -> None:
    """Continuously reconcile provider-paid intents while the bot is polling."""
    interval = float(app.settings.event_payments_sync_interval_seconds)
    while True:
        try:
            await sync_pending_event_payments_once(app)
        except WebsiteBridgeError as exc:
            logger.error("Event payment sync loop is disabled: {}", exc.code)
        except Exception:
            logger.exception("Event payment sync loop failed unexpectedly")
        await asyncio.sleep(interval)


async def revoke_before_local_deletion(
    app: Any,
    registration: dict,
    event: dict | None,
    *,
    transition_kind: str,
    reason: str,
    client: WebsiteEventBridgeClient | None = None,
) -> dict | None:
    if not bridge_requested(app.settings):
        return None

    configured_event_id = _clean(
        getattr(app.settings, "event_payments_bot_event_id", "")
    )
    if not configured_event_id:
        raise WebsiteBridgeError("bridge_configuration_incomplete")
    if _clean(registration.get("event_id")) != configured_event_id:
        return None

    # For the exact configured event, a missing/mismatched event row must not
    # silently bypass revocation and leave an admission valid on the website.
    if event is None or _event_id(event) != configured_event_id:
        exc = WebsiteBridgeError("mapped_event_unavailable")
        await _record_failure(app, registration, operation="revoke", code=exc.code)
        await app.log_to_chat(
            "⚠️ Не удалось найти точное мероприятие для отзыва билета 146.school. "
            "Локальная регистрация сохранена; нужна проверка конфигурации "
            f"(код: {exc.code}).",
            "events",
        )
        raise exc
    try:
        if not bridge_enabled_for(app.settings, event):
            raise WebsiteBridgeError("bridge_not_enabled_for_event")
    except WebsiteBridgeError as exc:
        await _record_failure(app, registration, operation="revoke", code=exc.code)
        await app.log_to_chat(
            "⚠️ Конфигурация отзыва билета 146.school недоступна. "
            "Локальная регистрация сохранена; нужна проверка конфигурации "
            f"(код: {exc.code}).",
            "events",
        )
        raise
    bridge = registration.setdefault("website_event_bridge", {})
    source_id = _source_registration_id(registration)
    reference = bridge.get("revocation_reference") or f"club146-bot:cancel:{source_id}"
    occurred_at = (
        bridge.get("revocation_occurred_at") or datetime.now(timezone.utc).isoformat()
    )
    await _set_fields(
        app,
        registration,
        {
            "revocation_reference": reference,
            "revocation_occurred_at": occurred_at,
            "sync_state": "revoke_pending",
        },
    )
    try:
        raw = await (client or WebsiteEventBridgeClient(app.settings)).revoke(
            source_id,
            transition_kind=transition_kind,
            reason=_clean(reason)[:255],
            audit_reference=reference,
            occurred_at=occurred_at,
        )
        status = _clean(raw.get("status")) if isinstance(raw, dict) else ""
        admissions = raw.get("admissions", []) if isinstance(raw, dict) else []
        if status not in REMOTE_FINAL_REVOKED_STATUSES or any(
            row.get("admission_valid") is True
            for row in admissions
            if isinstance(row, dict)
        ):
            raise WebsiteBridgeError("revoke_not_final")
    except WebsiteBridgeError as exc:
        await _record_failure(app, registration, operation="revoke", code=exc.code)
        logger.error(
            "Website ticket revocation failed; local deletion blocked: {}", exc.code
        )
        await app.log_to_chat(
            "⚠️ Не удалось отозвать билет 146.school. Локальная регистрация сохранена; "
            f"нужен повтор синхронизации (код: {exc.code}).",
            "events",
        )
        raise
    await _set_fields(
        app,
        registration,
        {
            "sync_state": "revoked",
            "last_error_code": None,
            "remote_status": status,
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return raw


def _public_url(settings: Any, path: str) -> str:
    return f"{_validated_base_url(settings.event_payments_website_api_base_url)}{path}"


async def send_website_ticket_links(chat_id: int, app: Any, registration: dict) -> bool:
    """Send personalized website cards; the PNG ticket remains a fallback."""
    bridge = registration.get("website_event_bridge", {})
    if bridge.get("remote_status") not in REMOTE_PAID_STATUSES:
        return False
    admissions = bridge.get("admissions", [])
    buttons = []
    names = [registration.get("full_name", "Участник")] + [
        guest.get("name", "Гость") for guest in registration.get("guests", [])
    ]
    for index, admission in enumerate(admissions):
        path = admission.get("ticket_path") if isinstance(admission, dict) else None
        if not path:
            continue
        safe_path = _safe_path(path, "/event-tickets/")
        if safe_path is None:
            continue
        name = _clean(names[index] if index < len(names) else f"Участник {index + 1}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🎟 {name}"[:64],
                    url=_public_url(app.settings, safe_path),
                )
            ]
        )
    if not buttons:
        return False
    await send_safe(
        chat_id,
        "Ваши именные билеты. Каждый билет относится к одному участнику:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    return True
