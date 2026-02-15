# AGENTS Guide

This file is the session bootstrap for coding agents working in this repository.
If chat context is lost, treat this document plus `planning/STATUS.md` as source of truth.

## Project Snapshot

- Product: Telegram auction bot + admin web panel.
- Stack: Python 3.12, aiogram, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Redis.
- Runtime: Docker Compose services `bot`, `admin`, `db`, `redis`.
- Core domains: auctions, moderation/risk, appeals, feedback, guarantor flow, points ledger, private DM topics.
- Quality gates: Ruff + unit tests + DB integration tests in GitHub Actions.

## Non-Negotiable Workflow

1. Plan first, then code.
2. Represent sprint scope in `planning/sprints/*.toml` manifest.
3. Sync plan to GitHub before implementation:
   - `python scripts/sprint_sync.py --manifest planning/sprints/<sprint>.toml`
   - optional draft PR scaffolds: add `--create-draft-prs`
4. Implement one issue at a time (small PRs, clear acceptance criteria).
5. Every PR must include both:
   - linked issue in body: `Closes #<issue_number>`
   - at least one label: `sprint:*`
6. Do not merge with failing CI.

## Session Start Checklist

1. Read `AGENTS.md`.
2. Read `planning/STATUS.md`.
3. Check active work:
   - `gh pr list --state open`
   - `gh issue list --state open --limit 50`
4. If new scope appears, add it to sprint manifest and sync before coding.

## Session End Checklist

1. Update docs affected by behavior/config changes.
2. Re-run validation commands for touched areas.
3. Update `planning/STATUS.md` via sprint sync (or manually when needed).
4. Ensure PR body has `Closes #...` and PR has `sprint:*` label.
5. Add concise rollout or rollback notes for risky changes.

## Required Validation Commands

- Lint: `python -m ruff check app tests`
- Unit tests: `python -m pytest -q tests`
- Integration tests:
  - `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://.../auction_test python -m pytest -q tests/integration`

Integration tests must run against a dedicated test DB only (`*_test` database).

## Planning and Traceability Rules

- Keep issue titles stable with sprint/task id markers (example: `[Sprint 33][S33-001] ...`).
- Keep branch naming aligned with task id (example: `sprint-33/s33-001-short-name`).
- Track process debt under label `tech-debt` and include it in sprint manifests.
- Prefer updating existing issue/PR threads instead of opening duplicates.

## Runtime Safety Rules

- Never run destructive git commands without explicit user instruction.
- Never use production credentials in tests.
- For schema changes: add Alembic migration + backward-safe rollout note.
- For bot behavior changes: include a minimal Telegram smoke checklist in PR.
