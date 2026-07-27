# Report: bot-side payment/registration bridge — mock, e2e, admin reg

Date: 2026-07-27  
Branch: `agent/payment-bridge-mock-and-admin-reg` (from `dev`)  
Plan item: inbox `146 bot and website plans - 27 jul 2026` #3 + org feedback §4/§5 gaps

## What landed

1. **Mock website API** — `dev/mock_website_event_payments/`
   - In-memory create / confirm / revoke matching the internal contract
   - Standalone: `make mock-website-api` or `uv run python -m dev.mock_website_event_payments.server`
   - README in that folder

2. **E2E tests** — real `WebsiteEventBridgeClient` over loopback HTTP to the mock
   - freeze → create → replay → confirm → revoke
   - provider-paid sync promotes local `payment_status`
   - background notify-once
   - zero-price guest rejected at freeze
   - `user_id=None` + priced guest still valid intent

3. **Admin manual registration** — admin menu → Управление →
   «Зарегистрировать человека вручную»
   - optional Telegram (`user_id` / @username or none)
   - `start_source=manual_admin`
   - freezes website snapshot when bridge is configured
   - guests with year/letter

4. **Guest graduation year + class letter**
   - collected in registration / edit-guests flows
   - `App.calculate_guest_price(event, graduation_year, graduate_type)` uses the
     guest's own formula year, floored at `guest_price_minimum`
   - zero free-type guests still rejected by bridge intent (website slice rule)

5. **Source vocabulary** — TODO only; bot does not unilaterally change what the
   website stores (`club146_registry_bot`). Admin stamps `start_source=manual_admin`.

6. **Pricing config from website** — **skipped**. No documented read-only endpoint
   yet. Mongo event config remains the formula source for *new* snapshots only.

## Assumptions (flag for Petr if wrong)

- Guest price = formula on *guest* year/type, then floor at `guest_price_minimum`
  (no longer `max(minimum, registrant_price)`).
- Non-parseable guest year input defaults to «друг» / NON_GRADUATE.
- Free guests (teacher/organizer types) remain unsupported in the bridge intent
  slice (zero amount → reject).
- Loopback HTTP for mock is OK; remote still HTTPS-only.

## Still untestable without live website

- CloudPayments signed webhook and live checkout flags
- PostgreSQL unique-constraint races and FOR UPDATE locking
- Real HMAC public `/event-pay/` HTML pages
- Confirm JSON from production website code (see contract contradiction)
- End-to-end against staging Coolify (infra session owns that; do not touch)

## Contract contradiction to fix on website (or bot fallback)

Live `POST .../confirm` response omits `fixed_amount`; bot `_normalise_response`
requires it. Mock includes it. Prefer website adding `fixed_amount` to confirm
(and import-legacy) responses.

## Ordered steps to enable on dev/staging (human, after infra session)

1. Confirm staging website has event-payment tables +
   `EVENT_PAYMENTS_SCHEMA_READY=1` + `EVENT_PAYMENTS_ENABLED=1` + long
   `EVENT_PAYMENTS_API_TOKEN` + `EVENT_PAYMENTS_LINK_SECRET` (≥32).
2. Backfill / verify website event UID and bot `website_event_uid` match the
   configured triple.
3. On **dev bot only** (not prod), set env (Coolify, not committed files):
   - `EVENT_PAYMENTS_BRIDGE_ENABLED=true`
   - `EVENT_PAYMENTS_WEBSITE_API_BASE_URL=https://<staging host>`
   - `EVENT_PAYMENTS_WEBSITE_API_TOKEN=<same dedicated token>`
   - `EVENT_PAYMENTS_WEBSITE_EVENT_ID` / `_UID` / `EVENT_PAYMENTS_BOT_EVENT_ID`
4. Redeploy/restart **one** poller for the dev token (avoid Telegram 409).
5. Smoke: new registration → pay button → mock or staging checkout → `/status`
   and background sync → ticket links; admin manual reg; cancel → revoke.
6. Keep prod bridge off until the Aug 1 event path is signed off.

## Non-negotiables respected

- Bridge still disabled by default; no committed env enable
- No Coolify / live bot / prod Mongo touch
- No secrets in files
- No check-in writes
- calmlib/botspot pins untouched
