# `website_event_uid` — link from a bot event to the website event

Implements the «Контракт по событиям» section of
`146.school/docs/events-people-data-integration.md`.

## The rule

The website's SQL `events` table is the **source of truth** for events. An
event is created on the website (its admin already exists); every other system
references it. The cross-system key is the website's stable `Event.uid` — no
new column on the website side was needed.

A bot event document therefore carries an optional `website_event_uid`
pointing at that value. Registrations (`registered_users`) keep referencing the
bot's own `event_id`; their link to the website is transitive through the event
document.

Calendar fields (name, date, venue, address) are **read from the website, not
synced**. There is no copy in Mongo to go stale: `App` reads the website's row
and overlays it onto an in-memory copy of the event document on every read (see
`src/website_db.py`). Pricing, registration status, and the registrations
themselves stay operational bot data.

> **The bot connects to the website's PostgreSQL directly** — decided 28.07.2026
> by Petr. Earlier notes in this repo said the opposite («a `DATABASE_URL`
> appearing in this repo means wrong path»); that rule is superseded. Reads go
> over SQL as the least-privilege `club146_bot_ro` role (SELECT on `events`,
> `event_registration_configs`, `person_telegram_links` — and nothing else;
> `people` and `donations` are deliberately ungranted). **Writes** —
> intents, confirm, revoke — still go through the website's internal HTTP API,
> which owns idempotency, validation and the audit log.
>
> Why read-through rather than a sync job: a sync job copies, a copy can be
> stale, and the moment both sides can write it needs a conflict rule. On
> 28.07.2026 the site advertised ул. Встречная while the bot told 46 registrants
> ул. Самаркандская — two stores, two write paths, nothing reconciling them.
> Holding no second copy removes the failure mode instead of monitoring it.

Because the website is the owner, the bot's admin **no longer offers** название,
дата, место or адрес for a linked event; it points at the site's admin instead.
An edit there would be discarded on the next read, which is worse than the old
divergence — the admin would believe it had worked.

Reads fail **closed to Mongo**: if the database is unreachable the bot keeps
serving the last known values and logs. That is the opposite of remote pricing,
which has no fallback on purpose. Showing a slightly stale address during an
outage beats showing a registrant an error; charging a stale price does not.

## Two steps, deliberately separate

**1. Declare the field — migration `010_add_website_event_uid`.**
Sets `website_event_uid: None` on every event document that lacks it. It
asserts no mapping, so it is safe to run identically in every environment and
is a no-op on re-run.

**2. Bind an actual value — `dev/set_website_event_uid.py`.**
Per-environment operational configuration, run by a human.

```sh
# dry run (the default — prints what would change, writes nothing)
python dev/set_website_event_uid.py \
    --bot-event-id 6a599a17a37724d81b7eadc3 \
    --website-event-uid event-1@146.school

# then, having read the output
python dev/set_website_event_uid.py \
    --bot-event-id 6a599a17a37724d81b7eadc3 \
    --website-event-uid event-1@146.school --apply
```

The binding is **not** in the migration on purpose: staging must never inherit
production's website UID. A staging bot pointed at the production event would
mint real admissions from test registrations.

The script refuses to bind a website UID that another bot event already claims,
so two registration funnels cannot both mint admissions for one website event.

## Current August 1 mapping

- website: SQL `events.id = 1`, `uid = event-1@146.school`
- bot: Mongo `_id = 6a599a17a37724d81b7eadc3`

The website's `uid` is filled by its own Alembic revision `20260725_0006`, which
writes exactly the value the `.ics` feed already emitted as its fallback — so
no subscribed calendar sees a UID change.

## Rollback

One event:

```sh
python dev/set_website_event_uid.py --bot-event-id <id> --unset --apply
```

The whole migration:

```js
db.events.updateMany({}, {$unset: {website_event_uid: ""}})
```

Nothing reads the field yet, so removing it restores the previous state exactly.
