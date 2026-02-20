# Requirements: LiteAuction Admin Operator UX

**Defined:** 2026-02-19
**Core Value:** Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## v1 Requirements

### Density Controls

- [x] **DENS-01**: Operator can switch admin list density between compact, standard, and comfortable modes.
- [x] **DENS-02**: Operator density preference persists across browser refresh and new sessions.

### Presets and Views

- [x] **PSET-01**: Operator can save a named preset containing filters, sort order, visible columns, and density.
- [x] **PSET-02**: Operator can load a saved preset and see the list update to the stored view state.
- [x] **PSET-03**: Admin can define workflow-focused default presets for Moderation, Appeals, Risk, and Feedback queues.

### Filtering and Search

- [x] **FILT-01**: Operator can use a quick filter input to narrow list results immediately.
- [x] **FILT-02**: Operator can apply advanced filter qualifiers for queue triage use cases.

### Table Layout

- [x] **LAYT-01**: Operator can show or hide columns in list views.
- [x] **LAYT-02**: Operator can reorder and pin columns for stable scanning.
- [x] **LAYT-03**: Column visibility, order, and pinning preferences persist across sessions.

### Progressive Disclosure

- [x] **DISC-01**: Operator can open row-level details without losing list position or active filters.
- [x] **DISC-02**: Detailed sections load progressively so list interactions remain responsive.
- [x] **DISC-03**: Disclosure depth is limited to two levels (list and details) for operational clarity.

### Keyboard and Batch Operations

- [x] **KEYB-01**: Operator can use keyboard shortcuts to focus search, navigate rows, and open row details.
- [x] **BULK-01**: Operator can select multiple rows and run supported bulk actions with explicit confirmation for destructive actions.

### Quality and Readability

- [x] **TYPO-01**: Compact mode uses a tuned typography scale that preserves readability and scan speed.
- [x] **TEST-01**: Automated web tests verify progressive disclosure state persistence, focus return, and keyboard flow behavior.

## v2 Requirements

### Adaptive Intelligence

- **ADPT-01**: Operator detail depth adapts by risk/priority rules while keeping predictable navigation.
- **TELE-01**: Product team can review preset quality telemetry (time-to-action, reopen rate, filter churn).

## Out of Scope

| Feature | Reason |
|---------|--------|
| Unlimited nested disclosure levels | Slows triage and increases context switching overhead |
| Fully freeform per-user layout engine | High complexity and support cost for low core-value gain |
| Aggressive auto-refresh that reflows active lists | Causes context loss during active moderation review |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DENS-01 | Phase 1 | Complete |
| DENS-02 | Phase 1 | Complete |
| FILT-01 | Phase 1 | Complete |
| FILT-02 | Phase 1 | Complete |
| LAYT-01 | Phase 1 | Complete |
| LAYT-02 | Phase 1 | Complete |
| LAYT-03 | Phase 1 | Complete |
| TYPO-01 | Phase 1 | Complete |
| TEST-01 | Phase 1 | Complete |
| PSET-01 | Phase 2 | Complete |
| PSET-02 | Phase 2 | Complete |
| PSET-03 | Phase 2 | Complete |
| DISC-01 | Phase 3 | Complete |
| DISC-02 | Phase 3 | Complete |
| DISC-03 | Phase 3 | Complete |
| KEYB-01 | Phase 3 | Complete |
| BULK-01 | Phase 3 | Complete |

**Coverage:**
- v1 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-20 after Sprint 51 completion and planning recovery*
