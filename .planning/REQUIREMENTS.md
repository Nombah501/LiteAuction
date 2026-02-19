# Requirements: LiteAuction Admin UX Density and Disclosure

**Defined:** 2026-02-19
**Core Value:** Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## v1 Requirements

Requirements for this milestone. These map to roadmap phases.

### Density Controls

- [ ] **DENS-01**: Operator can switch admin list density between compact, standard, and comfortable modes
- [ ] **DENS-02**: Operator density preference persists across browser refresh and new sessions

### Presets and Views

- [ ] **PSET-01**: Operator can save a named preset containing filters, sort order, visible columns, and density
- [ ] **PSET-02**: Operator can load a saved preset and see the list update to the stored view state
- [ ] **PSET-03**: Admin can define workflow-focused default presets for Moderation, Appeals, Risk, and Feedback queues

### Filtering and Search

- [ ] **FILT-01**: Operator can use a quick filter input to narrow list results immediately
- [ ] **FILT-02**: Operator can apply advanced filter qualifiers for queue triage use cases

### Table Layout

- [ ] **LAYT-01**: Operator can show or hide columns in list views
- [ ] **LAYT-02**: Operator can reorder and pin columns for stable scanning
- [ ] **LAYT-03**: Column visibility, order, and pinning preferences persist across sessions

### Progressive Disclosure

- [ ] **DISC-01**: Operator can open row-level details without losing list position or active filters
- [ ] **DISC-02**: Detailed sections load progressively so list interactions remain responsive
- [ ] **DISC-03**: Disclosure depth is limited to two levels (list and details) for operational clarity

### Keyboard and Batch Operations

- [ ] **KEYB-01**: Operator can use keyboard shortcuts to focus search, navigate rows, and open row details
- [ ] **BULK-01**: Operator can select multiple rows and run supported bulk actions with explicit confirmation for destructive actions

### Quality and Accessibility

- [ ] **TYPO-01**: Compact mode uses a tuned typography scale that preserves readability and scan speed
- [ ] **TEST-01**: Automated web tests verify progressive disclosure state persistence, focus return, and keyboard flow behavior

## v2 Requirements

Deferred for future releases.

### Adaptive Intelligence

- **ADPT-01**: Operator detail depth adapts by risk/priority rules while keeping predictable navigation
- **TELE-01**: Product team can review preset quality telemetry (time-to-action, reopen rate, filter churn)

## Out of Scope

Explicitly excluded for this milestone.

| Feature | Reason |
|---------|--------|
| Mega-row "show everything by default" layout | Reduces scanability and increases operator error risk in dense queues |
| Unlimited nested disclosure levels | Adds navigation depth and slows moderation decision speed |
| Fully freeform per-user layout engine | High implementation/support cost with inconsistent team operations |
| Aggressive auto-refresh that reflows active lists | Causes context loss during active moderation review |

## Traceability

Phase mapping finalized in roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DENS-01 | Phase 1 | Pending |
| DENS-02 | Phase 1 | Pending |
| PSET-01 | Phase 2 | Pending |
| PSET-02 | Phase 2 | Pending |
| PSET-03 | Phase 2 | Pending |
| FILT-01 | Phase 1 | Pending |
| FILT-02 | Phase 1 | Pending |
| LAYT-01 | Phase 1 | Pending |
| LAYT-02 | Phase 1 | Pending |
| LAYT-03 | Phase 1 | Pending |
| DISC-01 | Phase 3 | Pending |
| DISC-02 | Phase 3 | Pending |
| DISC-03 | Phase 3 | Pending |
| KEYB-01 | Phase 3 | Pending |
| BULK-01 | Phase 3 | Pending |
| TYPO-01 | Phase 4 | Pending |
| TEST-01 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 17 total
- Mapped to phases: 17
- Unmapped: 0

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-19 after roadmap creation*
