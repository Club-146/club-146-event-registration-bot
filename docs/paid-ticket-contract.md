# Paid entry ticket contract

Current TEST-first gate:

- The bot sends/resends a personalized card only when the matching Mongo
  registration has the exact value `payment_status == "confirmed"`.
- `pending`, `declined`, missing status, and legacy unpaid strings never receive
  a card.
- `/status` is the recovery path. The existing admin receipt-confirmation path
  sends the card immediately and `/status` can resend it later.
- The fallback `146-XXXX-XXXX-XXXX` code is derived deterministically from the
  bot registration ID, bot event ID, and Telegram user ID. It is a visual
  registration reference, not cryptographic proof. Door staff must pair it
  with the person's name and the bot's confirmed-registration list.

The website payment bridge can be added without changing the renderer. After a
signed CloudPayments webhook becomes authoritative, its bot-facing status API
must return these fields:

```json
{
  "schema_version": 1,
  "payment_status": "confirmed",
  "payment_kind": "event_attendance_donation",
  "bot_registration_id": "Mongo registered_users._id",
  "telegram_user_id": 123456789,
  "bot_event_id": "Mongo events._id",
  "website_event_uid": "stable Event.uid",
  "amount_minor": 250000,
  "currency": "RUB",
  "paid_at": "2026-07-20T12:34:56Z",
  "provider_payment_id": "opaque provider transaction id",
  "admissions": [
    {
      "admission_id": "stable opaque id",
      "role": "registrant",
      "display_name": "Лавров Петр",
      "ticket_code": "opaque uppercase code",
      "ticket_verification_url": "https://146.school/tickets/verify/opaque-token"
    },
    {
      "admission_id": "stable opaque id for guest 1",
      "role": "guest",
      "guest_index": 0,
      "display_name": "Имя гостя",
      "ticket_code": "different opaque uppercase code",
      "ticket_verification_url": "https://146.school/tickets/verify/another-token"
    }
  ]
}
```

Bot validation before writing `confirmed`:

1. `bot_registration_id`, `telegram_user_id`, and `bot_event_id` must all match
   one existing registration; never match by name, username, or email.
2. `website_event_uid` must equal the bot event's future
   `website_event_uid` field.
3. Amount/currency must equal the immutable payment intent; browser-return
   query parameters are never authoritative.
4. `provider_payment_id` must be idempotent. A repeated webhook/status response
   must not add payment twice or issue a second ticket.
5. Only `payment_status: confirmed` unlocks admission. The website returns one
   `admissions` item per human: registrant plus each named guest. Admission IDs
   and ticket codes must be distinct so check-in, attendance, and later profile
   achievements remain person-specific.
6. For the immediate pre-website bridge, the bot renders one group-style card
   containing the registrant and up to three current guest names. The bot stores
   a future primary `ticket_code` on the registration; the renderer automatically
   prefers it over the transitional visual code.
7. Every `ticket_verification_url` must be HTTPS and contain a signed, revocable,
   non-personal token. Add it as a QR code in a later additive slice.

The intent-creation request from bot to website needs the same three binding
IDs (`bot_registration_id`, `telegram_user_id`, `bot_event_id`),
`website_event_uid`, immutable `amount_minor`/`currency`, attendee name, and an
email only if CloudPayments requires it. Event, amount, purpose, and payment
frequency must not be editable through public URL parameters.
