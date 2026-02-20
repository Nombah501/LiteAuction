---
phase: 01-dense-list-foundations
verified: 2026-02-19T19:58:39Z
status: passed
score: 9/9 must-haves verified
human_verification:
  - test: "Exercise density chips on each queue page in a browser"
    expected: "Compact/standard/comfortable modes change row padding immediately and remain selected after refresh/login"
    why_human: "Visual density/scan-speed ergonomics are UI behavior and cannot be fully proven by static code checks"
  - test: "Use quick filter and advanced qualifiers together on queue pages"
    expected: "Typing quick filter narrows visible rows instantly; server qualifier chips/pagination retain full qualifier context"
    why_human: "End-user interaction timing and mixed-flow usability require real browser interaction"
  - test: "Toggle column visibility, reorder, and pin multiple columns"
    expected: "Columns reflow correctly, pinned columns stay left without overlap, and layout restores in a new session"
    why_human: "Runtime DOM layout/offset behavior and cross-session UX confirmation need live rendering"
---

# Phase 1: Dense List Foundations Verification Report

**Phase Goal:** Operators can shape queue density, filtering, and table layout for fast scanning, and those choices persist across sessions.
**Verified:** 2026-02-19T19:58:39Z
**Status:** passed
**Re-verification:** Yes - human checklist approved by operator

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Preferences for density and column layout persist per admin subject and queue. | ✓ VERIFIED | `app/services/admin_list_preferences_service.py:182` upserts by `subject_key`+`queue_key`; `app/db/models.py:452` unique constraint. |
| 2 | Same operator gets saved preferences after refresh/new authenticated session. | ✓ VERIFIED | `app/web/main.py:900` loads saved prefs on route render; integration assertion in `tests/integration/test_web_dense_list_foundations.py:232`. |
| 3 | One operator's preferences do not leak to another. | ✓ VERIFIED | Subject-scoped load/save in `app/services/admin_list_preferences_service.py:143`; isolation test in `tests/integration/test_web_dense_list_foundations.py:180`. |
| 4 | Operator can switch density among compact/standard/comfortable on queue pages. | ✓ VERIFIED | Density chips rendered in `app/web/dense_list.py:92`; consumed across queue routes in `app/web/main.py:1520`. |
| 5 | Quick filter narrows rendered rows immediately without submit. | ✓ VERIFIED | `input` event client filter in `app/web/dense_list.py:284`; row markers rendered via `data-row` in `app/web/main.py:3454`. |
| 6 | Advanced qualifiers stay server-validated and preserved in filter/pagination links. | ✓ VERIFIED | Server validation path includes 400 behavior (e.g., `app/web/main.py:872` and route parsers); qualifier retention asserted in `tests/integration/test_web_dense_list_foundations.py:349`. |
| 7 | Operators can show/hide columns without breaking table rendering. | ✓ VERIFIED | Visibility logic updates `hidden`/`aria-hidden` in `app/web/dense_list.py:208`; queue tables expose `data-col` keys in `app/web/main.py:3531`. |
| 8 | Operators can reorder and pin columns for stable left-side scanning. | ✓ VERIFIED | Reorder and pin logic in `app/web/dense_list.py:185` and `app/web/dense_list.py:216`; sticky offsets recomputed with measured widths. |
| 9 | Density and layout choices remain applied after refresh/new session. | ✓ VERIFIED | Saved state loaded into `DenseListConfig` in `app/web/main.py:909`; persistence endpoint writes validated payload in `app/web/main.py:3540`. |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| --- | --- | --- | --- |
| `app/db/models.py` | `AdminListPreference` model + uniqueness + density/layout fields | ✓ VERIFIED | Exists and substantive (`class AdminListPreference` at `app/db/models.py:449`), used by service import at `app/services/admin_list_preferences_service.py:11`. |
| `alembic/versions/0036_admin_list_preferences.py` | Migration creating preference table/indexes | ✓ VERIFIED | Exists with full upgrade/downgrade and required columns; schema matches ORM contract (`subject_key`, `queue_key`, `density`, `columns_json`). |
| `app/services/admin_list_preferences_service.py` | Validated load/upsert API for preferences | ✓ VERIFIED | Exists with validation + `on_conflict_do_update` upsert, imported/used by web routes at `app/web/main.py:47` and `app/web/main.py:3571`. |
| `app/web/dense_list.py` | Dense toolbar/script contract for density/filter/layout interactions | ✓ VERIFIED | Exists and substantive JS/HTML contract; imported and rendered from queue routes (`app/web/main.py:104`). |
| `app/web/main.py` | Queue routes wired to dense controls + persistence endpoint | ✓ VERIFIED | Routes call `_load_dense_list_config` for all 7 queues and expose `/actions/dense-list/preferences` at `app/web/main.py:3540`. |
| `tests/test_web_dense_list_contract.py` | Contract assertions for toolbar/script/layout markers | ✓ VERIFIED | Exists, 8 test cases for queue keys, density chips, controls markup, and pin/order script markers. |
| `tests/integration/test_web_dense_list_foundations.py` | Integration checks for persistence/filter/layout contracts | ✓ VERIFIED | Exists with route-level and endpoint validations; persistence/isolation + malformed payload coverage present. |

### Key Link Verification

| From | To | Via | Status | Details |
| --- | --- | --- | --- | --- |
| `app/services/admin_list_preferences_service.py` | `app/db/models.py` | SQLAlchemy select/upsert by subject+queue | WIRED | `select(AdminListPreference)` and `on_conflict_do_update` at `app/services/admin_list_preferences_service.py:148` and `app/services/admin_list_preferences_service.py:188`. |
| `alembic/versions/0036_admin_list_preferences.py` | `app/db/models.py` | Matching table columns/constraints | WIRED | Migration defines `subject_key`, `queue_key`, `density`, `columns_json` and unique constraint matching model. |
| `app/web/main.py` | `app/web/dense_list.py` | Route render flow imports and uses helpers | WIRED | Import at `app/web/main.py:104`; toolbar/script calls across queue routes (e.g. `app/web/main.py:3485`). |
| `app/web/main.py` | `tests/integration/test_web_dense_list_foundations.py` | `data-quick-filter` + density markers asserted | WIRED | HTML emits markers in route rendering; assertions at `tests/integration/test_web_dense_list_foundations.py:342`. |
| `app/web/main.py` | `app/services/admin_list_preferences_service.py` | GET loads prefs, POST persists prefs | WIRED | Load in `_load_dense_list_config` (`app/web/main.py:900`), save in action endpoint (`app/web/main.py:3571`). |
| `app/web/dense_list.py` | `app/web/main.py` | Shared `data-col` contract between script and table markup | WIRED | Script queries `th[data-col]` (`app/web/dense_list.py:151`); queue tables emit `data-col` headers/cells (`app/web/main.py:3531`). |
| `app/web/dense_list.py` | `tests/test_web_dense_list_contract.py` | Pin/order script contract tests | WIRED | Script contains `is-pinned` and pin offset logic; test asserts in `tests/test_web_dense_list_contract.py:91`. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| --- | --- | --- | --- | --- |
| DENS-01 | `01-02-PLAN.md` | Switch density between compact/standard/comfortable | ✓ SATISFIED | Density chips + active state in `app/web/dense_list.py:92`; route rendering/tests in `tests/integration/test_web_dense_list_foundations.py:321`. |
| DENS-02 | `01-01-PLAN.md`, `01-03-PLAN.md` | Density preference persists across refresh/sessions | ✓ SATISFIED | Persistence model/service + route load wiring (`app/services/admin_list_preferences_service.py:135`, `app/web/main.py:900`). |
| FILT-01 | `01-02-PLAN.md` | Quick filter narrows results immediately | ✓ SATISFIED | Client `input` filtering in `app/web/dense_list.py:284`; route markup includes `data-row` and filter input. |
| FILT-02 | `01-02-PLAN.md` | Advanced qualifiers for triage remain usable/validated | ✓ SATISFIED | Server-side filter validation in route parsers + qualifier-preserving link assertions (`tests/integration/test_web_dense_list_foundations.py:349`). |
| LAYT-01 | `01-03-PLAN.md` | Show/hide columns in list views | ✓ SATISFIED | Column visibility controls and `hidden` toggling in `app/web/dense_list.py:239` and `app/web/dense_list.py:208`. |
| LAYT-02 | `01-03-PLAN.md` | Reorder and pin columns for scanning | ✓ SATISFIED | Reorder controls and sticky pinned offset logic in `app/web/dense_list.py:185` and `app/web/dense_list.py:216`. |
| LAYT-03 | `01-01-PLAN.md`, `01-03-PLAN.md` | Column visibility/order/pinning persist across sessions | ✓ SATISFIED | `columns_json` persisted in model/service and rehydrated by route loader (`app/web/main.py:907`). |

Phase-1 orphaned requirements check against `.planning/REQUIREMENTS.md`: **none** (all Phase 1 IDs are declared in plan frontmatter).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| --- | --- | --- | --- | --- |
| `tests/integration/test_web_dense_list_foundations.py` | 93 | `return []` in `_EmptyRows` test stub | ℹ️ Info | Intentional fixture/stub behavior for isolated route tests; not production code. |
| `tests/integration/test_web_dense_list_foundations.py` | 351 | `return {}` in monkeypatched risk map | ℹ️ Info | Intentional mock return for focused qualifier-link test path. |

### Human Verification Required

### 1. Density ergonomics and persistence

**Test:** Open each queue page and switch density chips repeatedly, then refresh and re-login.
**Expected:** Visual density changes immediately; selected density remains active after refresh/new session.
**Why human:** Readability/scan-speed and rendered CSS behavior are visual UX outcomes.

### 2. Quick filter + advanced qualifier interaction

**Test:** Apply advanced qualifiers (status/source/etc.), then type in quick filter while paging.
**Expected:** Rows narrow instantly client-side and qualifier links/pagination keep context.
**Why human:** Real interaction speed and cross-control usability cannot be fully validated statically.

### 3. Column show/hide, reorder, and pin stability

**Test:** Hide columns, reorder several times, pin multiple columns, then resize browser.
**Expected:** No overlap, pinned columns remain left with recalculated offsets, layout persists after reload.
**Why human:** Runtime DOM measurement/offset behavior needs browser execution.

### Human Verification Outcome

Operator response: `approved`.
All three interactive checks were accepted in manual browser testing.

### Gaps Summary

No implementation gaps were found in code wiring or artifact completeness for declared must-haves. Automated evidence indicates Phase 1 deliverables are implemented; remaining checks are human UX confirmations for interactive behavior and visual scan quality.

---

_Verified: 2026-02-19T19:58:39Z_
_Verifier: Claude (gsd-verifier)_
