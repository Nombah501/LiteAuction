---
phase: 01-dense-list-foundations
plan: "01"
subsystem: database
tags: [postgres, sqlalchemy, alembic, preferences, admin-web]
requires: []
provides:
  - "AdminListPreference persistence model and migration with subject+queue uniqueness"
  - "Validated load/save service for density and column layout state"
  - "Unit and DB-backed integration regression tests for persistence and isolation"
affects: [01-02, 01-03, dense-list, admin-queues]
tech-stack:
  added: []
  patterns: ["subject-scoped preference storage", "PostgreSQL upsert with constraint target", "strict columns payload validation"]
key-files:
  created:
    - alembic/versions/0036_admin_list_preferences.py
    - app/services/admin_list_preferences_service.py
    - tests/integration/test_web_dense_list_foundations.py
  modified:
    - app/db/models.py
    - tests/test_admin_list_preferences_service.py
key-decisions:
  - "Use hashed token subjects (tok:<sha256>) for token-auth preference isolation."
  - "Require columns.order to be a full allow-list permutation; pinned must stay within visible."
patterns-established:
  - "Queue preference records are keyed by subject_key + queue_key with one canonical row per scope."
  - "Service rejects malformed payloads early via ValueError instead of storing partial state."
requirements-completed: [DENS-02, LAYT-03]
duration: 5 min
completed: 2026-02-19
---

# Phase 1 Plan 01: Dense List Preference Persistence Summary

**Server-side admin list preferences now persist density and column layout state per authenticated subject and queue across sessions.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-19T18:52:46Z
- **Completed:** 2026-02-19T18:57:29Z
- **Tasks:** 3
- **Files modified:** 5

## Accomplishments
- Added `AdminListPreference` ORM model with queue-scoped uniqueness, density checks, and JSONB layout payload.
- Added Alembic migration `0036_admin_list_preferences` with create/drop safety and lookup indexes.
- Implemented `load_admin_list_preference` and `save_admin_list_preference` with subject derivation, strict validation, and atomic upsert.
- Added unit and DB-backed integration tests proving validation rules, persistence, and cross-subject isolation.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add admin list preference persistence schema** - `bc16015` (feat)
2. **Task 2: Implement validated preference read/write service** - `de55775` (feat)
3. **Task 3: Add persistence-focused regression coverage** - `7cf529b` (test)

**Plan metadata:** pending

## Files Created/Modified
- `app/db/models.py` - adds `AdminListPreference` ORM contract and constraints.
- `alembic/versions/0036_admin_list_preferences.py` - creates/drops preference table and indexes.
- `app/services/admin_list_preferences_service.py` - validates and persists preference state via upsert.
- `tests/test_admin_list_preferences_service.py` - unit coverage for density and columns payload validation.
- `tests/integration/test_web_dense_list_foundations.py` - DB-backed persistence/isolation regression tests.

## Decisions Made
- Use `tg:<id>` and hashed `tok:<sha256>` subject keys so preferences remain isolated across auth modes.
- Enforce a strict columns payload contract (`visible`, `order`, `pinned`) to keep rendering state deterministic.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Alembic CLI unavailable in shell PATH**
- **Found during:** Task 1 (migration verification)
- **Issue:** `alembic` command and `python -m alembic` were unavailable in this environment.
- **Fix:** Switched verification to `.venv/bin/alembic`.
- **Files modified:** none
- **Verification:** migration round-trip completed using `.venv/bin/alembic`
- **Committed in:** `bc16015` (task output unchanged)

**2. [Rule 3 - Blocking] Default database target not reachable for verification**
- **Found during:** Task 1 (migration verification)
- **Issue:** repo default DB host/password were not usable locally for verification.
- **Fix:** Started an isolated temporary Postgres container and ran verification with `DATABASE_URL=postgresql+asyncpg://auction:auction@localhost:55432/auction_test`.
- **Files modified:** none
- **Verification:** migration upgrade/downgrade/upgrade succeeded against temporary DB
- **Committed in:** `bc16015` (task output unchanged)

**3. [Rule 3 - Blocking] Shared integration fixture skipped due unrelated metadata create_all collision**
- **Found during:** Task 3 (integration regression tests)
- **Issue:** existing `integration_engine` fixture skipped tests because unrelated schema index duplication prevented full metadata create.
- **Fix:** Added a focused integration fixture in `tests/integration/test_web_dense_list_foundations.py` that provisions only `AdminListPreference` table for DB-backed persistence checks.
- **Files modified:** tests/integration/test_web_dense_list_foundations.py
- **Verification:** targeted preference integration tests execute and pass on Postgres
- **Committed in:** `7cf529b`

---

**Total deviations:** 3 auto-fixed (3 blocking)
**Impact on plan:** All deviations were environment/test-harness unblockers; plan scope and deliverables remained unchanged.

## Issues Encountered
- None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Dense list persistence foundation is ready for route/UI wiring in `01-02` and `01-03`.
- Service APIs and tests provide a stable contract for upcoming queue density and column layout controls.

---
*Phase: 01-dense-list-foundations*
*Completed: 2026-02-19*

## Self-Check: PASSED

- Verified summary and key deliverable files exist on disk.
- Verified task commits `bc16015`, `de55775`, and `7cf529b` exist in git history.
