.PHONY: check fix fix-unsafe help run run-debug test

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

help:
	@echo "Available targets:"
	@echo "  check        - Run all linters and type checks (continues past failures)"
	@echo "  fix          - Auto-fix lint issues and format code"
	@echo "  fix-unsafe   - Auto-fix with unsafe fixes enabled"
	@echo "  test         - Run tests with coverage"
	@echo "  help         - Show this help message"
