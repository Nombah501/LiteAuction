# Milestones

## v1.1 Adaptive Triage Intelligence (Shipped: 2026-02-20)

**Phases completed:** 3 phases, 7 plans
**Timeline:** 2026-02-20 -> 2026-02-20
**Git range:** `28553d5..a1a05c3`

**Key accomplishments:**
- Delivered adaptive detail depth rules with deterministic reason codes, fallback behavior, and per-row operator override controls.
- Added workflow preset telemetry capture and segmented aggregation for time-to-action, reopen rate, and filter churn.
- Hardened workflow telemetry semantics to exclude unauthorized, invalid, and failed business outcomes from sampling.
- Added DB-backed integration coverage for telemetry ingestion and scoped aggregation outputs.
- Kept RBAC/CSRF guardrails intact across adaptive and telemetry endpoints with regression assertions.

**Known gaps:**
- No blocking gaps at milestone close (10/10 requirements verified as met).

---

## v1.0 Operator UX (Shipped: 2026-02-20)

**Phases completed:** 3 phases, 10 plans
**Timeline:** 2026-02-19 -> 2026-02-20
**Git range:** `bc16015..519235b`
**Code delta:** 44 files changed, +5224 / -2552 lines

**Key accomplishments:**
- Added durable dense-list operator preferences (density, filters, column layout) with validated persistence and migration-backed schema.
- Delivered shared queue UX controls for quick filtering, density switching, and column show/hide, reorder, and pin with restored state.
- Implemented workflow presets end-to-end with deterministic default precedence and ownership-safe save/switch/delete flows.
- Added in-place triage interactions with progressive detail hydration, keyboard navigation, and queue-native bulk actions.
- Hardened safety guarantees for destructive actions with explicit confirmation, RBAC/CSRF enforcement, and regression coverage.

**Known gaps:**
- No dedicated GitHub milestone named `v1.0` (delivery tracked under Sprint 51 issues).
- Retroactive Phase 2/3 summaries do not include normalized task-count metadata.

---
