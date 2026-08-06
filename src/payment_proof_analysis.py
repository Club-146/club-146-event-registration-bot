"""Structured receipt analysis and evidence-first payment-method resolution."""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from litellm import acompletion
from loguru import logger
from pydantic import BaseModel


DEFAULT_PAYMENT_PARSE_MODEL = "anthropic/claude-sonnet-4-6"
ANALYZER_VERSION = "receipt-destination-v2"


class PaymentDestination(str, Enum):
    EVENT_ADMIN = "event_admin"
    WEBSITE = "website"
    UNKNOWN = "unknown"


class ReceiptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PENDING = "pending"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PaymentRecipient(BaseModel):
    name: str = ""
    phone: str = ""
    label: str = "event admin"


class PaymentInfo(BaseModel):
    amount: Optional[int] = None
    is_valid: bool = False
    destination: PaymentDestination = PaymentDestination.UNKNOWN
    receipt_status: ReceiptStatus = ReceiptStatus.UNKNOWN
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    merchant_name: Optional[str] = None
    matched_admin: Optional[str] = None
    method_reason: Optional[str] = None

    @property
    def payment_method(self) -> str | None:
        if self.destination == PaymentDestination.EVENT_ADMIN:
            return "to_admin"
        if self.destination == PaymentDestination.WEBSITE:
            return "on_site"
        return None

    @property
    def paid_to_maria(self) -> bool:
        """Compatibility for old display call sites; new storage uses to_admin."""
        return self.destination == PaymentDestination.EVENT_ADMIN


class PaymentMethodResolution(BaseModel):
    claimed_method: str | None
    proof_method: str | None
    effective_method: str | None
    source: str | None
    overridden: bool = False
    override_reason: str | None = None


def _normalise_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", text))


def _phone_digits(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits[0] in "78":
        return digits[-10:]
    return digits


def _name_matches(receipt_name: str, configured_name: str) -> bool:
    receipt = _normalise_text(receipt_name)
    configured = _normalise_text(configured_name)
    if not receipt or not configured:
        return False
    if receipt == configured or receipt in configured or configured in receipt:
        return True
    receipt_tokens = receipt.split()
    configured_tokens = configured.split()
    if not receipt_tokens or not configured_tokens:
        return False
    first_name_matches = receipt_tokens[0] == configured_tokens[0]
    surname_initial_matches = receipt_tokens[-1][0] == configured_tokens[-1][0]
    return first_name_matches and surname_initial_matches


def configured_payment_recipients(
    settings: Any, event: dict | None = None
) -> list[PaymentRecipient]:
    """Return event-specific recipients, falling back to the legacy Maria env pair."""
    raw = (event or {}).get("payment_recipients")
    recipients: list[PaymentRecipient] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            recipient = PaymentRecipient.model_validate(item)
            if recipient.name.strip() or recipient.phone.strip():
                recipients.append(recipient)
    if recipients:
        return recipients

    name = str(getattr(settings, "payment_name", "") or "").strip()
    phone = str(getattr(settings, "payment_phone_number", "") or "").strip()
    if name or phone:
        return [PaymentRecipient(name=name, phone=phone, label=name or "event admin")]
    return []


def _match_admin_recipient(
    info: PaymentInfo, recipients: list[PaymentRecipient]
) -> PaymentRecipient | None:
    observed_phone = _phone_digits(info.recipient_phone)
    for recipient in recipients:
        configured_phone = _phone_digits(recipient.phone)
        if observed_phone and configured_phone and observed_phone == configured_phone:
            return recipient
        if info.recipient_name and _name_matches(info.recipient_name, recipient.name):
            return recipient
    return None


def validate_destination(
    info: PaymentInfo, recipients: list[PaymentRecipient]
) -> PaymentInfo:
    """Require extracted recipient evidence before accepting an admin destination."""
    matched = _match_admin_recipient(info, recipients)
    if matched:
        reason = (
            info.method_reason or "receipt recipient matches configured event admin"
        )
        return info.model_copy(
            update={
                "destination": PaymentDestination.EVENT_ADMIN,
                "matched_admin": matched.label,
                "method_reason": reason,
            }
        )
    if info.destination == PaymentDestination.EVENT_ADMIN:
        return info.model_copy(
            update={
                "destination": PaymentDestination.UNKNOWN,
                "matched_admin": None,
                "method_reason": "admin destination rejected: recipient does not match configuration",
            }
        )
    return info


def resolve_payment_method(
    claimed_method: str | None,
    claimed_source: str | None,
    info: PaymentInfo,
) -> PaymentMethodResolution:
    """Positive receipt evidence for an admin overrides a website button click."""
    canonical_claim = "to_admin" if claimed_method == "to_maria" else claimed_method
    proof_method = info.payment_method
    if proof_method == "to_admin":
        overridden = canonical_claim not in (None, "to_admin")
        return PaymentMethodResolution(
            claimed_method=canonical_claim,
            proof_method=proof_method,
            effective_method="to_admin",
            source="proof_recipient",
            overridden=overridden,
            override_reason="configured_event_admin_recipient" if overridden else None,
        )
    if canonical_claim in ("on_site", "to_admin"):
        return PaymentMethodResolution(
            claimed_method=canonical_claim,
            proof_method=proof_method,
            effective_method=canonical_claim,
            source=claimed_source or "user",
        )
    if proof_method == "on_site":
        return PaymentMethodResolution(
            claimed_method=None,
            proof_method=proof_method,
            effective_method="on_site",
            source="proof_merchant",
        )
    return PaymentMethodResolution(
        claimed_method=canonical_claim,
        proof_method=proof_method,
        effective_method=None,
        source=None,
    )


def proof_analysis_payload(
    info: PaymentInfo, resolution: PaymentMethodResolution
) -> dict[str, Any]:
    return {
        "analyzer_version": ANALYZER_VERSION,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        **info.model_dump(mode="json"),
        "claimed_method": resolution.claimed_method,
        "proof_method": resolution.proof_method,
        "effective_method": resolution.effective_method,
        "method_source": resolution.source,
        "overridden": resolution.overridden,
        "override_reason": resolution.override_reason,
    }


async def extract_payment_from_image(
    file_bytes: bytes,
    file_type: str = "image/jpeg",
    *,
    recipients: list[PaymentRecipient] | None = None,
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    model: str | None = None,
) -> PaymentInfo:
    """Extract amount, status, merchant, and actual recipient from a receipt."""
    try:
        configured = list(recipients or [])
        if not configured and (recipient_name or recipient_phone):
            configured = [
                PaymentRecipient(
                    name=(recipient_name or "").strip(),
                    phone=(recipient_phone or "").strip(),
                    label=(recipient_name or "event admin").strip(),
                )
            ]
        recipient_lines = (
            "\n".join(
                f"- {recipient.label}: name={recipient.name or '(none)'}, phone={recipient.phone or '(none)'}"
                for recipient in configured
            )
            or "- none configured"
        )
        system_prompt = f"""You analyze Russian payment receipts for an event.
Extract only what is visible in the receipt:
- amount: integer rubles, or null
- is_valid: whether one clear payment amount is visible
- destination: event_admin | website | unknown
- receipt_status: succeeded | pending | failed | unknown
- recipient_name and recipient_phone exactly as shown, when present
- merchant_name exactly as shown, when present
- method_reason: one short evidence phrase

Configured event-admin recipients:
{recipient_lines}

Use destination=event_admin only when the receipt's recipient fields match a
configured admin. Use destination=website for a merchant/card/acquiring payment
such as 146.school or CloudPayments. Use unknown when cropped or ambiguous.
Do not infer success from the presence of an amount: preserve pending/failed status."""

        if file_type not in {"image/jpeg", "image/png", "application/pdf"}:
            raise ValueError(f"Unsupported file type: {file_type}")
        encoded_file = base64.b64encode(file_bytes).decode("utf-8")
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract receipt amount, status, merchant, and actual recipient.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{file_type};base64,{encoded_file}"},
                    },
                ],
            },
        ]
        response = await acompletion(
            model=model or DEFAULT_PAYMENT_PARSE_MODEL,
            messages=messages,
            max_tokens=300,
            response_format=PaymentInfo,
        )
        parsed = PaymentInfo(**json.loads(response.choices[0].message.content))  # type: ignore[union-attr]
        return validate_destination(parsed, configured)
    except Exception as exc:
        logger.error(f"Error extracting payment proof: {exc}")
        return PaymentInfo()
