.PHONY: check fix fix-unsafe help run run-debug test release-prod

run:
	uv run python run.py

run-debug:
	uv run python run.py --debug

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

# Prod: push dev → PR to main → merge (Coolify auto-deploys). See docs/DEPLOY.md
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
	@echo "OK: main updated. Coolify prod should redeploy from main."

help:
	@echo "Available targets:"
	@echo "  check         - Run all linters and type checks (continues past failures)"
	@echo "  fix           - Auto-fix lint issues and format code"
	@echo "  fix-unsafe    - Auto-fix with unsafe fixes enabled"
	@echo "  test          - Run tests with coverage"
	@echo "  release-prod  - push dev → PR → merge to main (Coolify prod)"
	@echo "  help          - Show this help message"
