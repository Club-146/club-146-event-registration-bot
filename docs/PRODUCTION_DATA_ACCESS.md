# Production registration and payment data

The production event registry and the website payment ledger are separate systems:

| Source | Live store | Safe read path |
| --- | --- | --- |
| Registration bot | MongoDB used by Coolify app `raa8wuc20q0leqf7svr2tj83` on `new-c.calmmage.com` | Run a read-only `pymongo` query/export inside the current app container, using its existing env without printing it |
| 146.school | Yandex Managed PostgreSQL | `cd ~/work/projects/146.school && ./scripts/db/prod-ro.sh ...` (`club146_ai_ro`, SELECT-only) |

The website PostgreSQL path was already documented in `~/work/projects/146.school/docs/prod-db-read-access.md` and implemented by `scripts/db/prod-ro.sh`. The missing piece was a bot-side pointer and the cross-system reconciliation boundary.

For MongoDB, resolve the current container by the stable Coolify application prefix; the deployment suffix changes:

```bash
ssh root@new-c.calmmage.com \
  'docker ps --format "{{.Names}}" | grep "^raa8wuc20q0leqf7svr2tj83-"'
```

Inside that container, use `uv run python` + `pymongo.MongoClient` with `BOTSPOT_MONGO_DATABASE_CONN_STR` and `BOTSPOT_MONGO_DATABASE_DATABASE`. Do not echo, inspect, copy, or log either value. Queries must be reads only. Important collections:

- `events` — event identity and pricing;
- `registered_users` — current registrations, guests, payment status/amount/method;
- `deleted_users` — cancelled registration history;
- `event_logs` — payment and deletion transitions;
- export all collections when a complete audit is requested, not only the four above.

Reconciliation rules:

- `payment_method=to_maria` is only a bot claim. It cannot be independently verified from the website database; Maria's bank statement is a separate source.
- Website event-payment tables are `event_payment_intents`, `event_admissions`, and `event_payment_audit_events`, separate from `donations` and `subscriptions`.
- As observed on 2026-08-03, those three event-payment tables had zero production rows. Historical website payment matching therefore used `donations`.
- Production `donations.event_id` was blank for the relevant CloudPayments rows. A match by person + time + exact allowed amount is evidence-backed inference, not a registration-bound provider record.
- People are duplicated in the website database. Do not join on one `person_id` alone; reconcile duplicate identities using strong identifiers (Telegram/email/phone, then full name) and account for every website payment row exactly once.
- A bot `confirmed` status is not proof of a website payment. Compare the successful CloudPayments amount with the bot's recorded amount and the event's regular/early-bird total, including guests.

Exports contain real PII. Keep local directories `0700`, files `0600`, never commit them, and remove remote temporary copies immediately after download and checksum verification.
