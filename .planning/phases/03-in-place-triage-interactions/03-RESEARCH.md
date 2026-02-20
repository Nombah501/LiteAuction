# Phase 3: In-Place Triage Interactions - Research

**Researched:** 2026-02-20
**Domain:** FastAPI server-rendered queue triage interactions (inline disclosure, progressive details, keyboard and batch flows)
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
### Detail reveal pattern
- Row details open inline under the selected row.
- Multiple row details can remain expanded at the same time.
- Closing details must preserve scroll position, keyboard focus, and active filters.
- While details are open, non-active rows are visually dimmed to keep attention on active review context.

### Progressive detail states
- Show skeleton placeholders plus key fields immediately on open.
- Load detail sections progressively from highest triage priority to lower-priority sections.
- On partial load failure, keep available sections visible and provide retry on failed sections.
- Show errors inline in the affected section.

### Claude's Discretion
- Keyboard shortcut mapping and focus choreography for search/row navigation/detail open.
- Batch-selection interaction model and destructive-confirmation phrasing.
- Exact visual treatment for skeletons, dimming intensity, and section hierarchy.

### Deferred Ideas (OUT OF SCOPE)
None.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DISC-01 | Open row details without losing list position or active filters | Keep details inline in table context and persist open-row intent in URL/query state so reload/back keeps list position/filter state |
| DISC-02 | Load detailed sections progressively while list stays responsive | Use small initial detail payload + section-by-section fetch endpoint with per-section error/retry contract |
| DISC-03 | Keep disclosure to two levels | Enforce only list row + inline detail panel, avoid nested detail navigation routes |
| KEYB-01 | Keyboard shortcuts for search, row nav, and open details | Extend dense-list client script with roving focus index + deterministic shortcuts bound to table shell |
| BULK-01 | Multi-select + supported bulk actions with destructive confirmation | Add explicit row selection state and server-validated bulk actions endpoint with confirmation text for destructive paths |
</phase_requirements>

## Summary

The existing Phase 1 and Phase 2 direction already centralizes queue interaction logic in `app/web/dense_list.py` and queue page rendering in `app/web/main.py`. Phase 3 should extend this same contract instead of adding a new frontend runtime. The safest path is: render lightweight inline detail containers in each queue row, hydrate them via JSON endpoints, and keep row/filter context in existing queue query parameters.

The largest risk is context loss when expanding/closing details or running keyboard/bulk actions. To avoid this, all interactions should be shell-scoped to the dense-list table and should never navigate away from the queue route except explicit row action links. For progressive loading, partial failure must degrade by section, not by panel.

**Primary recommendation:** implement triage interactions as dense-list contract extensions (data attributes + script behavior) with queue-specific server JSON detail builders and integration tests for context persistence, progressive loading, keyboard flow, and batch confirmations.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | `>=0.116,<1` | Queue HTML routes + detail/bulk action JSON endpoints | Existing admin web runtime and auth/CSRF flow |
| SQLAlchemy async + PostgreSQL | `>=2.0.38,<3` | Fetch detail payload data and apply batch mutations transactionally | Existing ORM/session model and integration tests |
| Vanilla JS in server-rendered HTML | browser baseline | Inline disclosure UI, section retries, keyboard and batch selection state | Matches existing dense-list interaction architecture |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest + pytest-asyncio | repo standard | Unit/integration coverage for disclosure, keyboard, and bulk contracts | Required for DISC/KEYB/BULK regression protection |
| Existing CSRF and RBAC helpers | repo standard | Guard all write actions and queue moderation operations | Required for bulk action safety |

## Architecture Patterns

### Pattern 1: Inline Detail Shell + Progressive Section Hydration
**What:** Render an inline details row immediately, then fetch prioritized sections asynchronously.
**Why:** Satisfies immediate visual feedback while keeping list responsive.

### Pattern 2: URL-Stable Triage Context
**What:** Preserve filter/density/page and open-row ids in query-compatible state so refresh/back returns to same triage surface.
**Why:** Prevents operator disorientation and supports DISC-01.

### Pattern 3: Shell-Scoped Keyboard and Selection Model
**What:** Keep keyboard handlers and row-selection state scoped to one dense-list shell instance.
**Why:** Avoids global hotkey collisions and makes behavior deterministic across queue pages.

### Anti-Patterns to Avoid
- Full-page navigation for row details (breaks in-place triage).
- Nested disclosure stacks inside details (violates DISC-03).
- Bulk actions without server-side CSRF/RBAC validation.
- Single-shot detail load that fails all content on one section error.

## Common Pitfalls

### Pitfall 1: Expanded Rows Collapse on Any Interaction
Persist expanded ids and focus target in script state and reapply after sort/filter/pager changes.

### Pitfall 2: Keyboard Flow Hijacks Form Inputs
Ignore shortcuts while focus is inside editable controls and require table-shell focus for navigation keys.

### Pitfall 3: Partial Detail Failure Appears as Empty Panel
Return per-section status (`ready`, `loading`, `error`) and render retry controls inline for failed sections.

### Pitfall 4: Batch Actions Apply to Invisible or Stale Rows
Server must validate selected ids against current queue scope and return rejected ids/causes.

## Concrete File Targets

- `app/web/dense_list.py`: disclosure, progressive section, keyboard, and bulk-selection client contract.
- `app/web/main.py`: queue markup hooks and JSON endpoints for details and batch actions.
- `tests/test_web_dense_list_contract.py`: contract coverage for data attributes and script behaviors.
- `tests/integration/test_web_triage_interactions.py`: DB-backed regression tests for DISC/KEYB/BULK paths.

## Validation Strategy

- Unit/contract tests for script output containing required data hooks and confirmation prompts.
- Integration tests for inline open/close context retention, progressive section retries, keyboard shortcuts, and bulk confirmation branches.
- Security checks ensuring unauthorized bulk/detail actions fail with 401/403.

## Metadata

**Research date:** 2026-02-20
**Valid until:** 2026-03-22
