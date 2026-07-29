# OpenAI Codex Instructions

Deploy: prod does NOT auto-deploy — merging to `main` ships nothing. Always finish with `make deploy-prod` (or `make release-prod`, which includes it). Cause: prod app is on Coolify `source_id=2` (calmmage GitHub App), not 4 (Club-146), so no push webhook ever arrives; failure is silent, prod just keeps serving the old image. Never tell the user a merge is live — check `deployments/applications/raa8wuc20q0leqf7svr2tj83` and the `Europe/Moscow` line in `src.reminder_scheduler` startup logs. `scripts/coolify-deploy.sh`, `docs/DEPLOY.md`.

Campaign / UTM-like attribution (CRM-ish): Telegram deep links only — `t.me/<bot>?start={source}__{campaign}[__{content}]`. Classic `?utm_*=` on bot URLs does nothing. Parser + store: `App.parse_start_attribution` / `record_start_source` / `user_sources`; /start extract in `router.extract_start_payload`; registration stamps `RegisteredUser.start_source`; admin `/source_stats`. Full note: `docs/start-source-attribution.md`. Outbound mailers (e.g. 146.school event_announce) must put payload in `?start=`, not site UTM.

Imports
- Always use absolute imports - from repo root
- Use uv run to ensure imports work
- Avoid modifying sys.path

Docker compose
- For projects with interlinked components, include Docker file and a docker-compose.yml
- Specificaly, that concerns frontend + backend applications or other multi-component projects

Monorepo selective commits:

```bash
git add -A
git commit <file1> <file2> <file3> -m "message"
```

Step 1 stages everything, Step 2 commits only specified files. Does NOT unstage other files, does NOT require stashing.

Alternatives:
- lazygit: Navigate → Space to stage → `c` to commit
- PyCharm: Commit window (Cmd+K) → select files → commit
- GUI tools: GitHub Desktop, GitKraken, Fork

We are writing instructions for a smart user, that can figure everything out, has strong intuition and can easily guess reasonable things, given a few hints.

Therefore:
1) NO HEADERS
2) BE EXTREMELY CONCISE, JUST MENTION KEY FUNCTIONS / FOLDERS BY NAME, DO NOT GO INTO VERBOSE DETAILS OR YAPPING

Preserve original text and phrasing provided by the user in chats as much as possible.

Makefile - add after prototype, 1-2 essential commands per component max

Minimal prototyping flow
For ~/calmmage/experiments/prototypes folder
- Before implementing a new feature - write a minimal working standalone prototype / demo - and test it.
- After the feature is working - update the existing code with the working code

# Next.js Style (shadcn, v0.dev-inspired)

Build front-ends in Next.js with:
- App Router, Server Components by default; Client Components only when needed.
- UI components: use shadcn patterns for UIs.

uv for python:
- initialize pyproject.toml if missing.
    - use `fix_repo` alias
- uv run for execution
- uv add for dependencies

# Python Libraries
- pathlib: Filesystem paths with `Path`.
- dotenv: Load `~/.env` non-secret config. Secrets are in `~/.env.enc` — use `find_env_key()`.
- httpx: HTTP client (sync/async) with timeouts.
- use loguru with calmlib.logging.setup_logger() settings - avoid prints
- typer for CLI, argparse for simple script flags
- rich: Rich terminal output (tables, colors).
- pydantic / pydantic_settings - avoid dataclasses and unstructured Dicts
- fastapi
- tqdm

- print_exc - use to show full tracebacks
- avoid excessive try/except nesting or blocks, always handle errors on external level, unless explicitly
  requested[6_python_libs.md](6_python_libs.md)

- Type safety - use pydantic, type hints
- Clean patterns - pathlib over os.path, loguru over print
- Simplicity
    - Implement minimal necessary functionality
    - Avoid major refactorings unless explicitly requested
- Modularity with utils
    - Avoid nesting as much as possible - create separate service util functions (private - _func)
    - Avoid code duplication - instead, create reusable utils
    
- CLIs with `typer`;
- Keep `cli.py` colocated under each tool directory.
- Provide short docstrings and a quick usage example per command.

