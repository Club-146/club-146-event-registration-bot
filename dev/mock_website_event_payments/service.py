"""In-memory website event-payment domain used by the mock HTTP server and e2e tests.

Mirrors the create / confirm / revoke contract of 146.school's internal API
closely enough for bot-side integration tests. Not a full website clone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote


SOURCE_SYSTEM = "club146_registry_bot"  # website hardcodes this today
ATTENDEE_TYPES = frozenset({"GRADUATE", "TEACHER", "NON_GRADUATE", "ORGANIZER"})
REMOTE_PAID = frozenset({"paid", "waived"})
REMOTE_REVOKED = frozenset({"cancelled", "refunded"})


class MockWebsiteError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class MockConfig:
    website_event_id: int = 1
    website_event_uid: str = "aug1-2026-perm"
    bot_event_id: str = "6a599a17a37724d81b7eadc3"
    api_token: str = "test-dedicated-token"
    link_secret: str = "x" * 32
    mode: str = "test"


@dataclass
class AdmissionState:
    ordinal: int
    kind: str
    display_name: str
    fixed_amount: Decimal
    ticket_token: str | None = None
    ticket_status: str = "pending"
    admission_valid: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "kind": self.kind,
            "ticket_path": (
                f"/event-tickets/{self.ticket_token}" if self.ticket_token else None
            ),
            "entry_code": (
                self._entry_code()
                if self.ticket_token and self.admission_valid
                else None
            ),
            "ticket_mode": "test",
            "admission_valid": self.admission_valid,
            "checked_in_at": None,
        }

    def _entry_code(self) -> str:
        digest = (
            hashlib.sha256(f"{self.ticket_token}:{self.ordinal}".encode())
            .hexdigest()[:8]
            .upper()
        )
        return f"{digest[:4]}-{digest[4:]}"


@dataclass
class IntentState:
    source_registration_id: str
    payload: dict[str, Any]
    amount: Decimal
    status: str
    public_token: str
    admissions: list[AdmissionState]
    confirmation_reference: str | None = None
    confirmation_amount: Decimal | None = None
    revocation_reference: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _formula_amount(formula: dict[str, Any]) -> int:
    attendee_type = str(formula.get("attendee_type", "GRADUATE")).upper()
    free_for = {str(v).upper() for v in formula.get("free_for_types", [])}
    if attendee_type in free_for:
        return 0
    base = int(formula["base_rubles"])
    rate = int(formula["rate_rubles"])
    ref_year = int(formula["reference_year"])
    grad_year = int(formula["graduation_year"])
    step = int(formula.get("step_years", 1))
    years_since = max(0, ref_year - grad_year)
    amount = base + rate * (years_since // step)
    if attendee_type == "NON_GRADUATE":
        minimum = int(formula.get("guest_price_minimum_rubles", 0))
        amount = minimum or (base + rate * (15 // step))
    deadline = formula.get("early_bird_deadline")
    calc_date = formula["calculation_date"]
    discount = int(formula.get("early_bird_discount_rubles", 0))
    if deadline and calc_date <= deadline and discount > 0:
        amount -= discount
    return max(0, amount)


def _expected_group_amount(payload: dict[str, Any]) -> Decimal:
    primary = _formula_amount(payload["formula"])
    guests = sum(int(g["fixed_amount_rubles"]) for g in payload.get("guests", []))
    return Decimal(primary + guests).quantize(Decimal("0.01"))


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    # Stable identity for exact-replay / 409 conflict checks.
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


class MockWebsiteEventPaymentService:
    """Stateful mock of POST /api/internal/event-payment-intents*."""

    def __init__(self, config: MockConfig | None = None):
        self.config = config or MockConfig()
        self.intents: dict[str, IntentState] = {}

    def reset(self) -> None:
        self.intents.clear()

    def authorize(self, authorization: str | None) -> None:
        expected = f"Bearer {self.config.api_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise MockWebsiteError(401, "unauthorized")

    def _signed_group_path(self, public_token: str) -> str:
        digest = hmac.new(
            self.config.link_secret.encode(),
            f"event-payment-group:{public_token}".encode(),
            hashlib.sha256,
        ).digest()
        signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")[:22]
        return f"/event-pay/{public_token}.{signature}"

    def _validate_mapping(self, payload: dict[str, Any]) -> None:
        try:
            website_event_id = int(payload["website_event_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MockWebsiteError(422, "invalid website_event_id") from exc
        if (
            website_event_id != self.config.website_event_id
            or _clean(payload.get("website_event_uid")) != self.config.website_event_uid
            or _clean(payload.get("bot_event_id")) != self.config.bot_event_id
        ):
            raise MockWebsiteError(422, "event mapping mismatch")

    def _validate_create_payload(self, payload: dict[str, Any]) -> None:
        self._validate_mapping(payload)
        source_id = _clean(payload.get("source_registration_id"))
        if not 8 <= len(source_id) <= 120:
            raise MockWebsiteError(422, "invalid source_registration_id")
        if not _clean(payload.get("registrant_name")):
            raise MockWebsiteError(422, "registrant_name is required")
        formula = payload.get("formula")
        if not isinstance(formula, dict):
            raise MockWebsiteError(422, "formula is required")
        attendee = str(formula.get("attendee_type", "GRADUATE")).upper()
        if attendee not in ATTENDEE_TYPES:
            raise MockWebsiteError(422, "attendee type is invalid")
        free_for = {str(v).upper() for v in formula.get("free_for_types", [])}
        primary = _formula_amount(formula)
        if primary == 0 and attendee not in free_for:
            raise MockWebsiteError(422, "zero primary amount is not free-listed")
        guests = payload.get("guests") or []
        if not isinstance(guests, list) or len(guests) > 3:
            raise MockWebsiteError(422, "invalid guests")
        for guest in guests:
            if not isinstance(guest, dict) or not _clean(guest.get("display_name")):
                raise MockWebsiteError(422, "guest display name is required")
            try:
                amount = int(guest["fixed_amount_rubles"])
            except (KeyError, TypeError, ValueError) as exc:
                raise MockWebsiteError(422, "guest fixed amount is invalid") from exc
            if amount < 1:
                raise MockWebsiteError(422, "zero-price guests are rejected")
        legal = payload.get("legal_kind")
        if legal not in {"unclassified", "donation", "service"}:
            raise MockWebsiteError(422, "legal_kind is invalid")

    def create_or_replay(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_create_payload(payload)
        source_id = _clean(payload["source_registration_id"])
        stored = self.intents.get(source_id)
        fingerprint = _payload_fingerprint(payload)
        if stored is not None:
            if _payload_fingerprint(stored.payload) != fingerprint:
                raise MockWebsiteError(409, "intent conflict")
            return self._intent_response(stored, created=False)

        amount = _expected_group_amount(payload)
        formula = payload["formula"]
        primary_amount = Decimal(_formula_amount(formula)).quantize(Decimal("0.01"))
        admissions = [
            AdmissionState(
                ordinal=0,
                kind="registrant",
                display_name=_clean(payload["registrant_name"]),
                fixed_amount=primary_amount,
            )
        ]
        for index, guest in enumerate(payload.get("guests") or [], start=1):
            admissions.append(
                AdmissionState(
                    ordinal=index,
                    kind="guest",
                    display_name=_clean(guest["display_name"]),
                    fixed_amount=Decimal(int(guest["fixed_amount_rubles"])).quantize(
                        Decimal("0.01")
                    ),
                )
            )

        status = "pending"
        if amount == 0:
            # All-free / waived group gets tickets immediately.
            status = "waived"
            for admission in admissions:
                admission.ticket_token = secrets.token_urlsafe(18)
                admission.ticket_status = "issued"
                admission.admission_valid = True

        intent = IntentState(
            source_registration_id=source_id,
            payload=deepcopy(payload),
            amount=amount,
            status=status,
            public_token=secrets.token_urlsafe(18),
            admissions=admissions,
        )
        self.intents[source_id] = intent
        return self._intent_response(intent, created=True)

    def confirm(
        self,
        source_registration_id: str,
        *,
        paid_amount: Any,
        evidence_reference: str,
    ) -> dict[str, Any]:
        source_id = unquote(source_registration_id)
        intent = self.intents.get(source_id)
        if intent is None:
            raise MockWebsiteError(404, "payment intent not found")
        try:
            amount = Decimal(str(paid_amount)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MockWebsiteError(422, "paid_amount is invalid") from exc
        evidence = _clean(evidence_reference)
        if not evidence:
            raise MockWebsiteError(422, "evidence_reference is required")

        if intent.status in REMOTE_PAID:
            if intent.confirmation_reference == evidence:
                return self._confirm_response(intent)
            raise MockWebsiteError(409, "confirmation already applied")
        if intent.status in REMOTE_REVOKED:
            raise MockWebsiteError(409, "intent is revoked")
        if amount < intent.amount:
            raise MockWebsiteError(409, "underpayment")

        intent.confirmation_reference = evidence
        intent.confirmation_amount = amount
        intent.status = "paid"
        for admission in intent.admissions:
            admission.ticket_token = secrets.token_urlsafe(18)
            admission.ticket_status = "issued"
            admission.admission_valid = True
        return self._confirm_response(intent)

    def revoke(
        self,
        source_registration_id: str,
        *,
        transition_kind: str,
        reason: str,
        audit_reference: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        source_id = unquote(source_registration_id)
        intent = self.intents.get(source_id)
        if intent is None:
            raise MockWebsiteError(404, "payment intent not found")
        target = {
            "registration_cancelled": "cancelled",
            "admin_cancelled": "cancelled",
            "refund": "refunded",
            "reversal": "refunded",
        }.get(transition_kind)
        if target is None:
            raise MockWebsiteError(422, "invalid transition_kind")
        reference = _clean(audit_reference)
        if intent.revocation_reference == reference and intent.status in REMOTE_REVOKED:
            return self._revoke_response(
                intent, transition_kind, reference, created=False
            )
        if intent.status in REMOTE_REVOKED and intent.revocation_reference != reference:
            raise MockWebsiteError(409, "already revoked with different reference")

        intent.status = target
        intent.revocation_reference = reference
        for admission in intent.admissions:
            admission.admission_valid = False
            admission.ticket_status = "revoked"
        _ = reason, occurred_at
        return self._revoke_response(intent, transition_kind, reference, created=True)

    def mark_provider_paid(self, source_registration_id: str) -> dict[str, Any]:
        """Test helper: simulate CloudPayments webhook confirmation."""
        intent = self.intents.get(source_registration_id)
        if intent is None:
            raise MockWebsiteError(404, "payment intent not found")
        intent.status = "paid"
        for admission in intent.admissions:
            admission.ticket_token = admission.ticket_token or secrets.token_urlsafe(18)
            admission.ticket_status = "issued"
            admission.admission_valid = True
        return self._intent_response(intent, created=False)

    def mark_provider_refunded(self, source_registration_id: str) -> dict[str, Any]:
        intent = self.intents.get(source_registration_id)
        if intent is None:
            raise MockWebsiteError(404, "payment intent not found")
        intent.status = "refunded"
        for admission in intent.admissions:
            admission.admission_valid = False
            admission.ticket_status = "revoked"
        return self._intent_response(intent, created=False)

    def _intent_response(self, intent: IntentState, *, created: bool) -> dict[str, Any]:
        return {
            "created": created,
            "status": intent.status,
            "fixed_amount": f"{intent.amount:.2f}",
            "currency": "RUB",
            "group_status_path": self._signed_group_path(intent.public_token),
            "admissions": [row.public_dict() for row in intent.admissions],
            "source_system": SOURCE_SYSTEM,
            "source_registration_id": intent.source_registration_id,
        }

    def _confirm_response(self, intent: IntentState) -> dict[str, Any]:
        # Bot normaliser requires fixed_amount on every status-like response.
        # Live website confirm currently omits it — mock keeps the bot path green.
        return {
            "status": intent.status,
            "fixed_amount": f"{intent.amount:.2f}",
            "group_status_path": self._signed_group_path(intent.public_token),
            "admissions": [row.public_dict() for row in intent.admissions],
            "checked_in_at": None,
        }

    def _revoke_response(
        self,
        intent: IntentState,
        transition_kind: str,
        audit_reference: str,
        *,
        created: bool,
    ) -> dict[str, Any]:
        return {
            "created": created,
            "status": intent.status,
            "transition_kind": transition_kind,
            "audit_reference": audit_reference,
            "admissions": [
                {
                    "ordinal": row.ordinal,
                    "ticket_status": row.ticket_status,
                    "admission_valid": row.admission_valid,
                    "checked_in_at": None,
                }
                for row in intent.admissions
            ],
        }


def formula_amount_for_tests(formula: dict[str, Any]) -> int:
    """Public helper for unit assertions."""
    return _formula_amount(formula)
