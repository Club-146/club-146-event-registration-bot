# Paid entry ticket contract

Current payment/ticket safety gate:

- The bot sends/resends a personalized card only when the matching Mongo
  registration has the exact value `payment_status == "confirmed"`.
- `pending`, `declined`, missing status, and legacy unpaid strings never receive
  a card.
- The background website sync is the automatic path. `/status` remains the
  user-triggered recovery path, and the existing admin receipt-confirmation
  path remains the manual fallback.
- The fallback `146-XXXX-XXXX-XXXX` code is derived deterministically from the
  bot registration ID, bot event ID, and Telegram user ID. It is a visual
  registration reference, not cryptographic proof. Door staff must pair it
  with the person's name and the bot's confirmed-registration list.

## August 1 website bridge

The additive website bridge is implemented but disabled by default. It uses a
dedicated bearer token and an exact configured tuple of bot event ID, website
event ID, and website event UID. It never matches a person or event by name,
username, email, title, date, or city.

For a new registration, the bot persists the complete formula inputs,
calculation date, pricing version, attendee type, free-type allowlist, and one
fixed price/name per guest before creating the website intent. `/pay` and
`/status` replay that exact payload. A legacy registration without the snapshot
fails closed in this normal path; it must use the website's separately gated,
audited legacy import.

When enabled, the payment message contains one fixed
`Оплатить N ₽` button to the opaque event-payment page. The bank-transfer and
receipt-review flow remains as fallback, but the event is never routed through
the website's generic Donation ledger. A startup-managed background loop polls
the authenticated website API every
`EVENT_PAYMENTS_SYNC_INTERVAL_SECONDS` seconds (15 by default). It may promote
the local payment state only when the exact registration-bound response says
`paid` or `waived`; `pending` never unlocks a ticket. On promotion it sends the
participant's website ticket links automatically and then the current visual
PNG card as fallback. The notification marker makes retries idempotent.

Both manual confirmation paths use a durable Telegram-derived evidence key and
call the website confirmation endpoint idempotently. Cancellation calls the
website revocation endpoint before moving the Mongo record to deleted users. A
revocation error records retry state, alerts the events/admin chat, and blocks
the local deletion.

The website's signed CloudPayments webhook is authoritative for provider
payments. The bot does not accept browser-return parameters or provider calls
directly; it consumes the website's authenticated, registration-bound status.
It continues polling paid rows so a later refund/reversal revokes the local
entitlement and notifies the participant once. The status response retains the
following binding fields:

```json
{
  "schema_version": 1,
  "payment_status": "confirmed",
  "payment_kind": "event_attendance_payment",
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
8. `payment_kind` remains the accounting-neutral `event_attendance_payment`.
   Petr calls this a donation in user-facing copy, but it must not enter generic
   donation, endowment, or fundraising totals until Club 146 accounting confirms
   its classification.

The intent-creation request from bot to website needs the same three binding
IDs (`bot_registration_id`, `telegram_user_id`, `bot_event_id`),
`website_event_uid`, immutable `amount_minor`/`currency`, attendee name, and an
email only if CloudPayments requires it. Event, amount, purpose, and payment
frequency must not be editable through public URL parameters.

## Local mock + what is tested

Bot-side mock of the website internal API lives at
`dev/mock_website_event_payments/` (stdlib HTTP server, no FastAPI).

```bash
make mock-website-api
# or: uv run python -m dev.mock_website_event_payments.server --port 8765
```

Point a throwaway **local** process only:

```bash
EVENT_PAYMENTS_BRIDGE_ENABLED=true
EVENT_PAYMENTS_WEBSITE_API_BASE_URL=http://127.0.0.1:8765
EVENT_PAYMENTS_WEBSITE_API_TOKEN=test-dedicated-token
EVENT_PAYMENTS_WEBSITE_EVENT_ID=1
EVENT_PAYMENTS_WEBSITE_EVENT_UID=aug1-2026-perm
EVENT_PAYMENTS_BOT_EVENT_ID=<mapped mongo events._id>
```

Loopback plain HTTP (`127.0.0.1` / `localhost` / `::1`) is accepted so the mock
can run without TLS. Non-loopback base URLs still require HTTPS. Do not enable
the bridge in committed env, docker-compose, Dockerfile, or Coolify while the
infra session is rewiring the dev bot.

Covered by automated tests:

- Unit bridge behaviour (`tests/test_website_event_bridge.py`) with an in-process
  FakeClient.
- Mock domain service (`tests/test_mock_website_event_payments.py`): formula,
  idempotent create/replay, 409 conflict, confirm, revoke, zero-price guest
  rejection.
- HTTP e2e (`tests/test_website_event_bridge_e2e.py`): real
  `WebsiteEventBridgeClient` against the mock server — freeze snapshot → create
  → replay → confirm → revoke; provider-paid background sync; manual-admin
  registration shape (`user_id=None`) with a priced guest; loopback HTTP URL
  rule.
- Guest year/letter pricing (`App.calculate_guest_price(event, year, type)`);
  admin manual registration flow.

Still untestable without the live/staging website: CloudPayments webhook,
HMAC group-page HTML, real PostgreSQL uniqueness races, and the website's
confirm JSON shape under production code (see contradiction note below).

### Contract notes found during mock work

1. **Confirm response `fixed_amount`.** The bot's `_normalise_response` requires
   `fixed_amount` on create *and* confirm. Live website
   `confirm_event_payment_intent` currently returns status/admissions/path only.
   The mock includes `fixed_amount` so the bot path stays green; website should
   add it (or the bot should fall back to the frozen expected amount).
2. **`source_system` vocabulary.** Website hardcodes
   `SOURCE_SYSTEM = "club146_registry_bot"`. Shared dictionary
   (`telegram_bot` / `website` / `vk` / `manual_admin`) is not landed yet. Bot
   does not send `source_system` in the create payload; admin manual
   registrations stamp bot-side `start_source=manual_admin` only. Do not change
   the value the website stores until both sides land the dictionary together.
3. **Guest wire payload.** Guests still go over the wire as name + fixed amount
   only. Graduation year/letter are stored on the bot registration for pricing
   and display; expanding website `GuestTerms` is a follow-up.
4. **Pricing config endpoint.** No read-only website pricing-config endpoint is
   documented/available yet. Bot keeps Mongo event pricing as the source of
   formula inputs for new snapshots. Existing registrations never rebuild from
   remote config (`WebsiteSnapshotRequired`).
5. **Payment confirmation ≠ attendance.** Mock and bot never set check-in.
