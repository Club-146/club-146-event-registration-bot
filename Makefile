.PHONY: check fix fix-unsafe help run run-debug test mock-website-api release-prod deploy-prod deploy-dev

run:
	uv run python run.py

run-debug:
	uv run python run.py --debug

# Local mock of 146.school event-payment internal API (loopback only).
mock-website-api:
	uv run python -m dev.mock_website_event_payments.server

# Run all CI checks locally
check:
	-uv run ruff check src
	-uv run ruff format --check src
	-uv run vulture --min-confidence 80 src
	-uv run pyright src

# Auto-fix what can be fixed
fix:
	-uv run ruff check --fix .
	uv run ruff format .

fix-unsafe:
	-uv run ruff check --fix --unsafe-fixes .
	uv run ruff format .

test:
	uv run pytest tests/ --cov=src --cov-report=term --cov-fail-under=40

# Prod: push dev → PR to main → merge → deploy-prod. See docs/DEPLOY.md
# Merging to main does NOT deploy on its own — the last step is what ships.
release-prod:
	@test "$$(git branch --show-current)" = "dev" || (echo "must be on branch dev"; exit 1)
	@test -z "$$(git status --porcelain)" || (echo "working tree dirty — commit first"; exit 1)
	@git push -u origin dev
	@if [ -z "$$(git log origin/main..dev --oneline)" ]; then echo "dev has nothing new vs main"; exit 1; fi
	@pr=$$(gh pr list --base main --head dev --state open --json number -q '.[0].number'); \
	if [ -z "$$pr" ]; then \
	  title=$$(git log origin/main..dev --pretty=format:%s -1); \
	  body=$$(git log origin/main..dev --oneline); \
	  gh pr create --base main --head dev --title "$$title" --body "$$body"; \
	  pr=$$(gh pr list --base main --head dev --state open --json number -q '.[0].number'); \
	fi; \
	echo "Merging PR #$$pr …"; \
	gh pr merge "$$pr" --merge --delete-branch=false
	@git fetch origin main
	@echo "main updated — now deploying (prod does not auto-deploy)…"
	@$(MAKE) --no-print-directory deploy-prod

# Coolify deploys on new-c.calmmage.com (Petr's personal Hetzner box).
#
# Prod does NOT auto-deploy. The prod app is bound to the `calmmage` GitHub App
# (source_id=2) instead of `Club-146` (source_id=4), so GitHub never delivers a
# push webhook for this repo to it. On 28 Jul 2026 that left prod 12 days behind
# `main`, still running the pre-timezone-fix scheduler and blasting every unpaid
# user at 03:15 MSK. Merging to `main` changes nothing on the server until this
# runs. Dev is on source_id=4 and does auto-deploy on push to `dev`.
#
# Force rebuild: make deploy-prod FORCE=1
deploy-prod:
	@./scripts/coolify-deploy.sh prod

deploy-dev:
	@./scripts/coolify-deploy.sh dev

help:
	@echo "Available targets:"
	@echo "  check             - Run all linters and type checks (continues past failures)"
	@echo "  fix               - Auto-fix lint issues and format code"
	@echo "  fix-unsafe        - Auto-fix with unsafe fixes enabled"
	@echo "  test              - Run tests with coverage"
	@echo "  mock-website-api  - Local mock of website event-payment internal API"
	@echo "  release-prod      - push dev → PR → merge to main → deploy-prod"
	@echo "  deploy-prod       - Coolify prod redeploy (REQUIRED: main does not auto-deploy)"
	@echo "  deploy-dev        - Coolify dev redeploy (dev auto-deploys; this is a manual kick)"
	@echo "  help              - Show this help message"
