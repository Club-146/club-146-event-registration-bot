# Start-source attribution (UTM-like deep links)

Campaign tracking for `@register_146_meetup_2025_bot` (prod) / test bot.
Part of the broader CRM / acquisition picture: which channel brought someone into the registration bot, first touch vs later clicks, and which invite converted.

This is **not** website `utm_*` query params. Telegram start payloads cannot contain `=` / `&` and are max 64 chars. Labels are encoded in `?start=` and parsed into UTM-like fields.

## Link format

```
https://t.me/register_146_meetup_2025_bot?start={payload}
```

Payload alphabet: `A–Z a–z 0–9 _ -` only, length 1–64.

Encoding (double underscore `__` separates fields; single `_` is OK inside a label):

| payload | utm_source | utm_campaign | utm_content |
| --- | --- | --- | --- |
| `email__event_1_aug_26_invite_1` | `email` | `event_1_aug_26_invite_1` | — |
| `email__invite__v2` | `email` | `invite` | `v2` |
| `group_chat` | `group_chat` | — | — (legacy single token) |
| `tg__partner_ivan__story_a` | `tg` | `partner_ivan` | `story_a` |

Suggested sources: `email`, `group_chat`, `channel`, `tg`, `site`, `partner`, …
Campaign slugs: stable ids like `event_1_aug_26_invite_1`, `event_announce`, not free-form sentences.

**Wrong for this bot:**  
`t.me/bot?utm_source=email&…` — Telegram ignores classic UTM on bot URLs; the bot never sees them.

## Runtime path

1. User opens deep link → Telegram delivers `/start <payload>` (or `/start@bot payload`).
2. `router.extract_start_payload` → `App.normalize_start_payload`.
3. `App.record_start_source` → Mongo `user_sources` + `event_logs` (`event_type=start_source` when counted as click).
4. FSM keeps `start_source` for this session.
5. On successful registration, `RegisteredUser.start_source` is stamped on `registered_users` (raw payload).

Bare `/start` (no payload): synthetic source `direct`, **not** counted as a campaign click (no history spam). Users who existed before tracking: first source `before_tracking` (migration), later campaign clicks only update `last_*`.

## Persistence

Collection `user_sources` (per `user_id`):

- first touch (immutable): `first_source`, `first_source_at`, `first_utm_source|campaign|content`
- last touch: `last_source`, `last_source_at`, `last_utm_*`
- `history[]` (last 100 clicks), `click_count`

`registered_users.start_source` — payload active at registration time (conversion stamp).

Admin: `/source_stats` (also in admin menu → «Кампании»). Aggregates: clicks by source / campaign / pair, first-touch mix, recent clicks. Code: `App.get_source_attribution_stats`, `routers/stats.show_source_stats`.

## Code map

| piece | where |
| --- | --- |
| normalize / parse / record / stats | `src/app.py` — `normalize_start_payload`, `parse_start_attribution`, `record_start_source`, `get_source_attribution_stats` |
| extract + /start handler | `src/router.py` — `extract_start_payload`, `start_handler` |
| registration stamp | `src/router.py` (build `RegisteredUser`) + `App.save_registered_user` |
| admin UI | `src/routers/stats.py` `/source_stats`; menu in `src/routers/admin.py` |
| backfill | `src/migrations.py` — `before_tracking`, utm field heal |
| tests | `tests/test_start_source.py` |

## How other systems must link

Any **outbound** invite that should attribute to email / chat / partner must use `?start=` encoding above.

| surface | expected CTA |
| --- | --- |
| site event announce email (`146.school` newsite `event_announce`) | e.g. `https://t.me/register_146_meetup_2025_bot?start=email__event_1_aug_26_invite_1` |
| TG group/channel posts | `?start=group_chat__…` or `channel__…` |
| partner / personal invites | `?start=tg__partner_<slug>` |
| site pages that deep-link to the bot | `?start=site__…` |

Site email `add_utm()` (medium/source/campaign query on **146.school** URLs) is a **different** system: Metrika/GA on the website. Do not confuse with bot start payloads. Site logo/home links can keep website UTM; the **bot button** must use `?start=`.

Site event-announce mailer (`146.school` `event_announce.run_campaign`) attaches  
`?start=email__event_announce[_e{event_id}][_c{campaign_id}]` at send time  
(unless the form URL already has `start=`). Website `utm_*` is only for 146.school links.

**Planned (not this feature):** registration outside Telegram — website first, then Max/VK — needs DB unify; see  
`~/work/projects/146.school/docs/research-multi-channel-event-registration.md`.

## CRM angle

This is acquisition telemetry for event CRM:

- **Clicks** — who opened the bot from which campaign (`user_sources` / `event_logs`).
- **Conversions** — who registered after which payload (`registered_users.start_source`).
- **First vs last** — organic/old users (`before_tracking` / `direct`) later hit by email still show campaign on last touch without overwriting first channel.

Not yet a full multi-channel CRM warehouse; data lives in the bot Mongo and admin `/source_stats`. When website people/events CRM grows, join key is Telegram `user_id` (+ optional email from registration if collected).
