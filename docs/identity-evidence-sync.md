# Event registry evidence in alumni verification

The website's `/admin/verification` page can show the registration history and
Telegram metadata already held by this bot. The sync copies both active
`registered_users` and `deleted_users` as display-only evidence.

- Stable key: Mongo registration `_id` as `(source_system, source_record_id)`.
- Payload: event, full name, graduation year/class, participant type,
  `telegram_id`, `@username`, city, registration/payment status and creation
  time.
- The website never converts this evidence into a `person_telegram_link` and
  never auto-merges people. Name/year matches are admin hints only.
- Repeated syncs are idempotent. The first sync runs on bot startup, then every
  900 seconds by default.

Configuration is fail-closed. The website must first have its additive
`identity_evidence` migration and `IDENTITY_EVIDENCE_SYNC_ENABLED=1`. Then set
on this bot:

```dotenv
IDENTITY_EVIDENCE_SYNC_ENABLED=true
IDENTITY_EVIDENCE_SYNC_INTERVAL_SECONDS=900
EVENT_PAYMENTS_WEBSITE_API_BASE_URL=https://146.school
EVENT_PAYMENTS_WEBSITE_API_TOKEN=<same bearer configured on website>
```

This feature does not require `EVENT_PAYMENTS_BRIDGE_ENABLED`; payment and
identity-evidence rollout gates are intentionally independent.
