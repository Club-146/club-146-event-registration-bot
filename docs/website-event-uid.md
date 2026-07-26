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

Calendar-field sync (name, date, venue, url) is **one-way, website → bot**.
Pricing, registration status, and the registrations themselves stay operational
bot data.

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
