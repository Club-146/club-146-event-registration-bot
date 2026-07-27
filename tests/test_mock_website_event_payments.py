"""Unit tests for the in-memory mock website payment service."""

from __future__ import annotations

import pytest

from dev.mock_website_event_payments.service import (
    MockConfig,
    MockWebsiteError,
    MockWebsiteEventPaymentService,
    formula_amount_for_tests,
)


def _payload(**overrides):
    payload = {
        "website_event_id": 1,
        "website_event_uid": "aug1-2026-perm",
        "bot_event_id": "6a599a17a37724d81b7eadc3",
        "source_registration_id": "abcdef0123456789",
        "registrant_name": "Иван Иванов",
        "registrant_telegram_id": 1,
        "payer_email": None,
        "legal_kind": "unclassified",
        "formula": {
            "base_rubles": 1400,
            "rate_rubles": 200,
            "reference_year": 2026,
            "graduation_year": 2016,
            "calculation_date": "2026-07-20",
            "step_years": 3,
            "attendee_type": "GRADUATE",
            "guest_price_minimum_rubles": 1500,
            "free_for_types": ["TEACHER", "ORGANIZER"],
            "early_bird_discount_rubles": 500,
            "early_bird_deadline": "2026-07-29",
            "version": "test-v1",
        },
        "guests": [
            {
                "display_name": "Анна",
                "fixed_amount_rubles": 1500,
                "pricing_version": "test-v1.guest",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_formula_matches_bot_example():
    formula = _payload()["formula"]
    # 1400 + 200 * floor(10/3) - 500 = 1500
    assert formula_amount_for_tests(formula) == 1500


def test_create_replay_conflict_confirm_revoke():
    svc = MockWebsiteEventPaymentService(
        MockConfig(api_token="tok", bot_event_id="6a599a17a37724d81b7eadc3")
    )
    created = svc.create_or_replay(_payload())
    assert created["created"] is True
    assert created["status"] == "pending"
    assert created["fixed_amount"] == "3000.00"

    replay = svc.create_or_replay(_payload())
    assert replay["created"] is False

    conflict = _payload()
    conflict["formula"] = {**conflict["formula"], "base_rubles": 9999}
    with pytest.raises(MockWebsiteError) as err:
        svc.create_or_replay(conflict)
    assert err.value.status_code == 409

    paid = svc.confirm(
        "abcdef0123456789",
        paid_amount=3000,
        evidence_reference="ev-1",
    )
    assert paid["status"] == "paid"
    assert paid["fixed_amount"] == "3000.00"
    assert all(row["admission_valid"] for row in paid["admissions"])

    revoked = svc.revoke(
        "abcdef0123456789",
        transition_kind="registration_cancelled",
        reason="bye",
        audit_reference="audit-1",
        occurred_at="2026-07-20T00:00:00+00:00",
    )
    assert revoked["status"] == "cancelled"
    assert all(not row["admission_valid"] for row in revoked["admissions"])


def test_zero_price_guest_rejected():
    svc = MockWebsiteEventPaymentService()
    payload = _payload(
        guests=[
            {
                "display_name": "Free",
                "fixed_amount_rubles": 0,
                "pricing_version": "v",
            }
        ]
    )
    with pytest.raises(MockWebsiteError) as err:
        svc.create_or_replay(payload)
    assert err.value.status_code == 422


def test_auth_required():
    svc = MockWebsiteEventPaymentService(MockConfig(api_token="secret"))
    with pytest.raises(MockWebsiteError) as err:
        svc.authorize("Bearer wrong")
    assert err.value.status_code == 401
    svc.authorize("Bearer secret")
