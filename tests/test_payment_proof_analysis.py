from types import SimpleNamespace

from src.payment_proof_analysis import (
    PaymentDestination,
    PaymentInfo,
    PaymentRecipient,
    ReceiptStatus,
    configured_payment_recipients,
    proof_analysis_payload,
    resolve_payment_method,
    validate_destination,
)


def test_configured_phone_match_makes_admin_destination_authoritative():
    info = PaymentInfo(
        amount=2400,
        is_valid=True,
        destination=PaymentDestination.WEBSITE,
        recipient_name="Мария Денисовна К.",
        recipient_phone="+7 (919) 488-89-10",
        receipt_status=ReceiptStatus.SUCCEEDED,
    )
    recipients = [
        PaymentRecipient(
            name="Мария Денисовна Корсакова",
            phone="8 919 488 89 10",
            label="Мария",
        )
    ]

    validated = validate_destination(info, recipients)

    assert validated.destination == PaymentDestination.EVENT_ADMIN
    assert validated.matched_admin == "Мария"


def test_unsubstantiated_admin_classification_is_downgraded_to_unknown():
    info = PaymentInfo(
        amount=1300,
        is_valid=True,
        destination=PaymentDestination.EVENT_ADMIN,
        recipient_name="Иван Иванов",
    )

    validated = validate_destination(
        info, [PaymentRecipient(name="Мария Корсакова", phone="+7 900 000-00-00")]
    )

    assert validated.destination == PaymentDestination.UNKNOWN
    assert validated.payment_method is None


def test_receipt_admin_overrides_claimed_website_but_unknown_does_not():
    admin_info = PaymentInfo(destination=PaymentDestination.EVENT_ADMIN)
    admin_resolution = resolve_payment_method("on_site", "user", admin_info)
    assert admin_resolution.effective_method == "to_admin"
    assert admin_resolution.source == "proof_recipient"
    assert admin_resolution.overridden is True

    unknown_resolution = resolve_payment_method("on_site", "user", PaymentInfo())
    assert unknown_resolution.effective_method == "on_site"
    assert unknown_resolution.source == "user"
    assert unknown_resolution.overridden is False


def test_event_recipients_override_legacy_global_recipient():
    settings = SimpleNamespace(payment_name="Мария", payment_phone_number="111")
    event = {
        "payment_recipients": [
            {"name": "Анна Петрова", "phone": "222", "label": "Анна"}
        ]
    }

    recipients = configured_payment_recipients(settings, event)

    assert [recipient.label for recipient in recipients] == ["Анна"]


def test_analysis_payload_keeps_status_and_precedence_decision():
    info = PaymentInfo(
        amount=1100,
        is_valid=True,
        destination=PaymentDestination.EVENT_ADMIN,
        receipt_status=ReceiptStatus.PENDING,
        matched_admin="Мария",
    )
    resolution = resolve_payment_method("on_site", "user", info)

    payload = proof_analysis_payload(info, resolution)

    assert payload["destination"] == "event_admin"
    assert payload["receipt_status"] == "pending"
    assert payload["claimed_method"] == "on_site"
    assert payload["effective_method"] == "to_admin"
    assert payload["overridden"] is True
