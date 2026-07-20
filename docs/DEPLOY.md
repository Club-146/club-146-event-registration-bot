# Deploy (prod)

Coolify watches **main** (`prod - main - register-146-meetup-2025-bot`). Dev Coolify watches **dev**.

```text
work on dev → push dev → PR dev→main → merge
→ Coolify auto-deploys prod
```

One sweep from a clean `dev` with commits ahead of `main`:

```bash
make release-prod
```

Pay links default to `https://146.school`. Coolify **dev** may set `PAYMENT_SITE_BASE_URL=https://staging.146.school.calmmage.com`.
