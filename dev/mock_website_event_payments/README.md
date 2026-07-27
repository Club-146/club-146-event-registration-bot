# Mock website event-payment API

In-memory stand-in for `146.school` internal endpoints the bot bridge calls:

- `POST /api/internal/event-payment-intents`
- `POST /api/internal/event-payment-intents/{source_id}/confirm`
- `POST /api/internal/event-payment-intents/{source_id}/revoke`
- `GET /healthz`

## Run standalone

```bash
# from repo root
uv run python -m dev.mock_website_event_payments.server
# or
uv run python -m dev.mock_website_event_payments.server --port 8766
```

Optional env:

| env | default |
| --- | --- |
| `MOCK_EVENT_PAYMENTS_TOKEN` | `test-dedicated-token` |
| `MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_ID` | `1` |
| `MOCK_EVENT_PAYMENTS_WEBSITE_EVENT_UID` | `aug1-2026-perm` |
| `MOCK_EVENT_PAYMENTS_BOT_EVENT_ID` | `6a599a17a37724d81b7eadc3` |

## Point a *local* bot at it

Only for a throwaway local process. Never Coolify/prod tokens.

```bash
export EVENT_PAYMENTS_BRIDGE_ENABLED=true
export EVENT_PAYMENTS_WEBSITE_API_BASE_URL=http://127.0.0.1:8765
export EVENT_PAYMENTS_WEBSITE_API_TOKEN=test-dedicated-token
export EVENT_PAYMENTS_WEBSITE_EVENT_ID=1
export EVENT_PAYMENTS_WEBSITE_EVENT_UID=aug1-2026-perm
export EVENT_PAYMENTS_BOT_EVENT_ID=<mongo events._id of the mapped event>
```

Loopback plain HTTP is allowed only for `127.0.0.1` / `localhost` / `::1`.
Any non-loopback base URL still requires HTTPS.

## Automated tests

`tests/test_website_event_bridge_e2e.py` starts this server on an ephemeral port
and drives the real `WebsiteEventBridgeClient` over HTTP.

In-process service helpers:

```python
from dev.mock_website_event_payments import MockWebsiteEventPaymentService
svc = MockWebsiteEventPaymentService()
svc.create_or_replay(payload)
svc.mark_provider_paid(source_id)  # simulate CloudPayments
```

## Known intentional differences vs live website

- Confirm responses include `fixed_amount` (bot normaliser requires it; live
  website confirm JSON currently omits it — flag for website session).
- No PostgreSQL, no CloudPayments, no signed public HTML pages.
- `source_system` remains the website's current literal
  `club146_registry_bot` (shared dictionary not landed on website yet).
