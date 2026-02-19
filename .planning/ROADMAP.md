# Roadmap: LiteAuction

## Overview

This roadmap delivers denser, faster admin moderation workflows without weakening trust boundaries by first establishing persistent list ergonomics, then adding reusable workflow presets, then enabling high-speed triage interactions, and finally hardening readability and regression safety.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Dense List Foundations** - Operators can configure and persist high-signal queue views for daily triage.
- [ ] **Phase 2: Workflow Presets** - Operators and admins can save and apply reusable queue configurations.
- [ ] **Phase 3: In-Place Triage Interactions** - Operators can investigate and act on queues quickly without context loss.
- [ ] **Phase 4: Readability and Regression Hardening** - Compact readability and key interaction paths are validated by automated web tests.

## Phase Details

### Phase 1: Dense List Foundations
**Goal**: Operators can shape queue density, filtering, and table layout for fast scanning, and those choices persist across sessions.
**Depends on**: Nothing (first phase)
**Requirements**: DENS-01, DENS-02, FILT-01, FILT-02, LAYT-01, LAYT-02, LAYT-03
**Success Criteria** (what must be TRUE):
  1. Operator can switch between compact, standard, and comfortable list density while viewing the queue.
  2. Operator can quickly narrow queues using instant search plus advanced qualifiers for triage.
  3. Operator can show/hide, reorder, and pin columns to match their scanning workflow.
  4. Density and table layout preferences remain applied after refresh and new login sessions.
**Plans**: 3 plans
Plans:
- [x] 01-01-PLAN.md - Build server-side preference persistence foundation for dense-list state.
- [ ] 01-02-PLAN.md - Add density controls and two-layer filtering contract to queue routes.
- [ ] 01-03-PLAN.md - Implement column layout controls and persistence wiring across sessions.

### Phase 2: Workflow Presets
**Goal**: Operators can store and reuse named queue configurations, while admins provide default queue presets by moderation context.
**Depends on**: Phase 1
**Requirements**: PSET-01, PSET-02, PSET-03
**Success Criteria** (what must be TRUE):
  1. Operator can save a named preset containing filters, sort order, visible columns, and density.
  2. Operator can load any saved preset and immediately see the queue reflect the stored state.
  3. Moderation, Appeals, Risk, and Feedback queues open with admin-defined default presets.
**Plans**: TBD

### Phase 3: In-Place Triage Interactions
**Goal**: Operators can inspect rows and execute keyboard-first or batch workflows without losing queue context.
**Depends on**: Phase 2
**Requirements**: DISC-01, DISC-02, DISC-03, KEYB-01, BULK-01
**Success Criteria** (what must be TRUE):
  1. Operator can open row details inline and return to the same list position with active filters preserved.
  2. Detail content loads progressively so list interactions stay responsive during review.
  3. Disclosure is limited to two levels (list and details), preventing deep navigation stacks.
  4. Operator can use keyboard shortcuts to focus search, move rows, open details, and perform supported batch actions with destructive confirmations.
**Plans**: TBD

### Phase 4: Readability and Regression Hardening
**Goal**: Compact-density readability is tuned for sustained moderation use, and critical disclosure/focus flows are protected by automated browser tests.
**Depends on**: Phase 3
**Requirements**: TYPO-01, TEST-01
**Success Criteria** (what must be TRUE):
  1. Operators can comfortably scan compact-mode text during long queue sessions without losing legibility.
  2. Automated web tests reliably detect regressions in disclosure state persistence, focus return, and keyboard flow behavior.
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 1.1 -> 1.2 -> 2 -> 2.1 -> 3 -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Dense List Foundations | 1/3 | In Progress | 2026-02-19 |
| 2. Workflow Presets | 0/TBD | Not started | - |
| 3. In-Place Triage Interactions | 0/TBD | Not started | - |
| 4. Readability and Regression Hardening | 0/TBD | Not started | - |
