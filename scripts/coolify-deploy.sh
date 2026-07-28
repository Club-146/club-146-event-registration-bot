#!/usr/bin/env bash
# Queue a Coolify deploy for this bot. Called by `make deploy-prod` / `make deploy-dev`.
#
# Why this exists: prod does NOT auto-deploy. See docs/DEPLOY.md.
#
# Usage: scripts/coolify-deploy.sh prod|dev [--force]
#
# Token:  $COOLIFY_NEW_TOKEN (exported from ~/.zshrc), else
#         CALMMAGE_COOLIFY_NEW_API_KEY from ~/.env.enc via calmlib find_env_key.
# URL:    $COOLIFY_NEW_URL, default https://new-c.calmmage.com
set -euo pipefail

REPO="Club-146/club-146-event-registration-bot"
PROJECT_UUID="tsctsivmf3235t2r3sp6rq03"
ENV_UUID="v4nuwv7a15i48ixche5hbzgv"
PROD_APP="raa8wuc20q0leqf7svr2tj83"
DEV_APP="ch0xb09nbcgyhu9qf1hy03i5"

target="${1:-}"
case "$target" in
  prod) app="$PROD_APP"; branch="main" ;;
  dev)  app="$DEV_APP";  branch="dev"  ;;
  *) echo "usage: $0 prod|dev [--force]" >&2; exit 2 ;;
esac

force="false"
[[ "${2:-}" == "--force" || "${FORCE:-0}" == "1" || "${FORCE:-0}" == "true" ]] && force="true"

url="${COOLIFY_NEW_URL:-https://new-c.calmmage.com}"
token="${COOLIFY_NEW_TOKEN:-}"
if [[ -z "$token" ]]; then
  token=$(cd "$HOME/work/calmmage" && uv run python -c \
    'from calmlib.utils import find_env_key; print(find_env_key("CALMMAGE_COOLIFY_NEW_API_KEY") or "")' \
    2>/dev/null | tail -1)
fi
if [[ -z "$token" ]]; then
  echo "no Coolify token — export COOLIFY_NEW_TOKEN, or set CALMMAGE_COOLIFY_NEW_API_KEY in ~/.env.enc" >&2
  exit 1
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

api() {  # api <path> -> prints http code, body lands in $tmp
  curl -sS -o "$tmp" -w "%{http_code}" \
    -H "Authorization: Bearer $token" -H "Accept: application/json" "$url$1"
}

# Preflight. A wrong instance/token answers 404 "No resources found", which reads
# like the app vanished — fail loudly instead, and confirm we hit the right app.
code=$(api "/api/v1/applications/$app")
if [[ "$code" != "200" ]]; then
  echo "cannot read app $app on $url (HTTP $code) — wrong Coolify instance or token?" >&2
  cat "$tmp" >&2; echo >&2
  exit 1
fi
name=$(python3 - "$tmp" "$REPO" "$branch" <<'PY'
import json, sys
app = json.load(open(sys.argv[1]))
_, _, want_repo, want_branch = sys.argv
if app.get("git_repository") != want_repo:
    sys.exit(f"refusing: app tracks {app.get('git_repository')!r}, expected {want_repo!r}")
if app.get("git_branch") != want_branch:
    sys.exit(f"refusing: app tracks branch {app.get('git_branch')!r}, expected {want_branch!r}")
print(app.get("name", ""))
PY
)

echo "Queueing Coolify deploy: $name  (branch $branch, force=$force)"
code=$(api "/api/v1/deploy?uuid=$app&force=$force")
cat "$tmp"; echo
if [[ "$code" != "200" ]]; then
  echo "deploy failed HTTP $code" >&2
  exit 1
fi

echo "OK — watch: $url/project/$PROJECT_UUID/environment/$ENV_UUID/application/$app"
echo "Logs should show: payment reminder scheduler started (daily 09:00 Europe/Moscow + catch-up :15 hours 9-17 MSK)"
