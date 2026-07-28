# Deploy (prod)

**Prod does not auto-deploy. Merging to `main` ships nothing — you must run `make deploy-prod`.**

```text
work on dev → push dev → PR dev→main → merge → make deploy-prod
```

One sweep from a clean `dev` with commits ahead of `main` (does all of the above,
deploy included):

```bash
make release-prod
```

Deploy only, without touching git:

```bash
make deploy-prod            # Coolify prod, branch main
make deploy-prod FORCE=1    # force rebuild (no cache)
make deploy-dev             # manual kick for dev; dev also auto-deploys on push
```

## Why prod does not auto-deploy

Coolify apps (both on `new-c.calmmage.com`, Petr's personal Hetzner box):

| App | Branch | Coolify `source_id` | Auto-deploy |
|---|---|---|---|
| `dev - bot - register-146-meetup-2025-bot` | `dev` | 4 — Club-146 GitHub App | yes |
| `prod - main - register-146-meetup-2025-bot` | `main` | 2 — **calmmage** GitHub App | **no** |

Source 2 is the GitHub App installed for the `calmmage/*` repos. It is not the
installation that receives pushes for `Club-146/club-146-event-registration-bot`,
so GitHub never delivers a webhook to the prod app. Dev's recent deployments are
all `is_webhook=true`; prod's are all manual `force_rebuild=true`.

This is silent. Nothing errors — prod just keeps serving the last image someone
deployed by hand.

**It bit us on 28 Jul 2026.** The reminder-scheduler timezone fix (`4947aed`,
PR #69) merged to `main` on 27 Jul. Prod's last build was 26 Jul (`91c3f0e`), so
prod kept running the old UTC-naive scheduler whose hourly catch-up fired at
00:15 UTC = **03:15 MSK**, messaging every unpaid registrant in the middle of the
night. Prod had not auto-deployed since 16 Jul — 12 days.

Permanent fix (not done yet): repoint the prod app to source 4 in the Coolify UI,
Configuration → Source. Until then `make deploy-prod` is mandatory.

## Verifying a deploy actually landed

`running` in Coolify means the container is up, not that it is up with your code.
Check the commit and the startup log:

```bash
# what prod actually built, most recent first
curl -s -H "Authorization: Bearer $COOLIFY_NEW_TOKEN" \
  "$COOLIFY_NEW_URL/api/v1/deployments/applications/raa8wuc20q0leqf7svr2tj83?take=3" \
  | python3 -c 'import json,sys; [print(d["created_at"], d["status"], d["commit"][:9]) for d in json.load(sys.stdin)["deployments"]]'

# is a given commit actually in what is deployed?
git merge-base --is-ancestor <fix-sha> <deployed-sha> && echo included || echo MISSING
```

Container log after a good deploy contains:

```text
src.reminder_scheduler | payment reminder scheduler started
  (daily 09:00 Europe/Moscow + catch-up :15 hours 9–17 MSK)
```

If that line says anything other than `Europe/Moscow`, prod is running pre-fix
code and will send at 03:15 MSK.

Re-deploying is safe against double-sends: delivery sets per-registration flags
(`payment_reminder_d4_sent` / `_d2_sent`), and those flag names are unchanged
across versions, so already-notified users are skipped on the next tick.

## Credentials

`scripts/coolify-deploy.sh` takes the token from `$COOLIFY_NEW_TOKEN` (exported
from `~/.zshrc`), falling back to `CALMMAGE_COOLIFY_NEW_API_KEY` in `~/.env.enc`.
URL override: `$COOLIFY_NEW_URL` (default `https://new-c.calmmage.com`).

The script preflights the app UUID and refuses unless the repo and branch match —
a wrong instance or token otherwise returns `404 No resources found`, which reads
like the app disappeared.

## Env

Pay links default to `https://146.school`. Coolify **dev** may set
`PAYMENT_SITE_BASE_URL=https://staging.146.school.calmmage.com`.
