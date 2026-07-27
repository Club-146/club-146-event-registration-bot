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

1. **Confirm response `fixed_amount` — RESOLVED 2026-07-27, website side.**
   The bot's `_normalise_response` reads exactly four keys off every
   status-shaped response and rejects the whole exchange if any is missing:

   | key | meaning |
   |---|---|
   | `status` | one of `REMOTE_STATUSES` |
   | `fixed_amount` | the **frozen expected total**, never the sum actually paid |
   | `group_status_path` | must be under `/event-pay/` |
   | `admissions` | one entry per human, count must match the snapshot |

   Live `confirm_event_payment_intent` returned status/admissions/path only,
   while the mock returned `fixed_amount` — so the bot suite was green and the
   first real confirmation would have failed with `invalid_remote_amount`.
   The website now returns `fixed_amount` from `confirm` and from
   `import-legacy-confirmation`, pinned by
   `test_confirm_response_carries_the_frozen_amount_not_the_paid_one` in
   `newsite/tests/test_event_payments.py` — a test against the **real route**,
   which is the part that was missing.

   The bot was deliberately *not* loosened to fall back to the frozen amount:
   re-deriving the value locally would make `remote_amount_mismatch`
   unfalsifiable, and that check is what detects a website that has silently
   repriced a group.

   **Rule this leaves behind:** a mock is not evidence. Any field the bot
   requires must be asserted against the website's own route, or the two
   drift again in exactly this direction.
2. **`source_system` vocabulary.** Website hardcodes
   `SOURCE_SYSTEM = "club146_registry_bot"`. Shared dictionary
   (`telegram_bot` / `website` / `vk` / `manual_admin`) is not landed yet. Bot
   does not send `source_system` in the create payload; admin manual
   registrations stamp bot-side `start_source=manual_admin` only. Do not change
   the value the website stores until both sides land the dictionary together.
3. **Guest wire payload.** Guests still go over the wire as name + fixed amount
   only. Graduation year/letter are stored on the bot registration for pricing
   and display; expanding website `GuestTerms` is a follow-up.

   **Guest pricing rule — DECIDED 27 Jul 2026 (Petr).** A guest who gives a
   school year is priced *exactly like a registrant of that year*; a guest with
   no school year (a «друг») pays the flat guest rate. This side is canonical
   (`App.calculate_guest_price`) and the website was changed to match.

   It closes a real loophole: the website used to charge every guest a flat
   `guest_price_fixed`, so a 1995 alum cost 1500 as somebody's guest versus
   7600 as themselves — a 6100₽ discount for using the guest field.

   **This side needs no code change, but it does need config:**
   `guest_price_minimum` must be set to the intended flat rate (1500). That one
   field is both the floor for alum guests and the price for a «друг»; left at
   0, a «друг» falls through to "the formula for someone who left 15 years ago"
   (4400 for the Aug 1 config).

   Two details that are easy to get subtly wrong, and are pinned by tests:
   - free attendee types return 0 **before** the minimum applies, so a teacher
     brought as a guest is not floored up to 1500.
   - the minimum floors the *regular* amount and the early-bird discount comes
     off afterwards, so the final amount can sit below the minimum
     (1500 - 500 = 1000).

   The agreed numbers live in a table duplicated in both repos:
   `tests/test_guest_pricing_parity.py` here and
   `newsite/tests/test_guest_pricing_parity.py` on the website. Change guest
   pricing in one repo and the other repo's suite fails — deliberately, since
   the two services share no package.
4. **Pricing config endpoint — RESOLVED 2026-07-27.** It exists:
   `GET /api/internal/event-configs/by-bot-event/{bot_event_id}` (also
   `by-uid/{website_event_uid}`, a bare list, and a `PUT` for upserts). It
   landed in the same window as the mock work, which is why the two sessions
   missed each other.

   The bot now reads it behind `EVENT_PAYMENTS_REMOTE_PRICING_ENABLED`
   (`resolve_event_pricing`), separate from the bridge flag so pricing
   authority can move — and roll back — without touching checkout.

   - The overlaid keys keep the **Mongo document's own names**, so display,
     guest quotes, the admin quote and the frozen snapshot all keep reading one
     dict. Authority moves; call sites do not.
   - **Fails closed, no Mongo fallback.** A fallback would silently charge a
     stale price and buys no availability: the next call is a POST to the same
     host, so a website that cannot serve config cannot mint the intent either.
   - The **whole mapping triple** (`bot_event_id`, `website_event_uid`,
     `website_event_id`) is re-checked against the config actually served. A
     mistyped `EVENT_PAYMENTS_*` is the one failure that would otherwise price
     the wrong event silently.
   - Only `pricing_type == "formula"` is accepted; `free` and `fixed_by_year`
     have no intent-payload representation, so they are refused rather than
     coerced.
   - Existing registrations still never rebuild from remote config
     (`WebsiteSnapshotRequired`) — that is unchanged and must stay.

   ⚠️ The website's `create_intent` does **not** validate the formula it is
   sent; it trusts it verbatim. So nothing except this endpoint makes the two
   channels agree — there is no server-side backstop.

5. **Displayed price vs frozen price — one open decision.** The bot computes
   price for display (`calculate_event_payment`) and the bridge computes it
   again to freeze the charge, and the two resolve early bird differently:
   display uses `payment_timeline`, whose cutoff is **06:00 on the deadline
   day**; the frozen formula is re-evaluated by the website as
   `calculation_date <= early_bird_deadline`, i.e. **the whole day**.

   For the real Aug 1 2026 event (`early_bird_deadline = 29 Jul`, set by
   migration `summer_2026_food_flag_and_early_bird`) that is a genuine one-day
   window: on 29 Jul after 06:00 the bot displays 4600 and the intent freezes
   4100. Verified, and pinned as a characterisation test in
   `tests/test_website_remote_pricing.py::TestEarlyBirdWindow`.

   Until the canonical cutoff is chosen, `_assert_matches_quoted_price` refuses
   to freeze any snapshot whose total differs from the amount the registration
   was quoted (`discounted_payment_amount`). Nobody is charged a price they
   were not shown; the row is left `snapshot_re_registration_required` for an
   admin instead. Rows that never quoted anything (admin-created, legacy) are
   exempt — there is no promise to contradict.
6. **Payment confirmation ≠ attendance.** Mock and bot never set check-in.
