# Feature Research

**Domain:** Operator-focused moderation/admin console (Telegram auction bot back office)
**Researched:** 2026-02-19
**Confidence:** MEDIUM

## Feature Landscape

### Table Stakes (Users Expect These)

Features operators assume exist. Missing these makes moderation throughput feel broken.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Density controls (compact/standard/comfortable) | Common in modern data-heavy consoles; operators expect to tune information density per task and screen size | LOW | Backed by MUI Data Grid `density` and AG Grid compactness/row-height controls; make compact available globally, not per-widget only |
| Saved views / operator presets (filters + sort + visible columns + grouping) | Teams need quick context switching (triage, fraud review, appeals) without rebuilding queries every time | MEDIUM | Persist per-operator and support team defaults; align with GitHub Projects saved view pattern and grid state export/restore |
| Fast filtering and search (quick filter + advanced query) | Queue-based ops work depends on narrowing large lists quickly | MEDIUM | Must support both simple quick filter and advanced qualifiers; keyboard focus shortcut is expected |
| Column visibility/reorder/pinning with persistence | Moderators need role-specific layouts and stable scanning anchors | MEDIUM | Pin key identity/risk columns; persist column state and restore on refresh/session changes |
| Keyboard-first list operations | High-volume moderation teams optimize for keyboard navigation and low pointer travel | MEDIUM | Include list navigation, focus search, and bulk-action trigger hotkeys; document hotkeys in-product |
| Progressive disclosure at row level (master list -> detail panel/drawer) | Operators need context without losing list position; opening full pages for every item is too slow | MEDIUM | Use expandable detail panel/drawer; lazy-load heavy detail sections to keep list performance |
| Bulk selection and bulk actions with guardrails | Queue work is batch-oriented; single-item handling only does not scale | MEDIUM | Include clear selection state, undo where safe, and explicit confirmation for destructive actions |

### Differentiators (Competitive Advantage)

Features that are not strictly required to be credible, but materially improve operator speed and consistency.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Focus-mode defaults by workflow (Moderation, Appeals, Risk, Feedback) | Reduces setup time and cognitive overhead by loading optimized density + column + panel defaults for current job | MEDIUM | Ship opinionated presets first, then allow personal overrides; this is your strongest near-term differentiator |
| Adaptive progressive disclosure (detail depth changes by risk/priority) | Shows only critical context for low-risk items while expanding richer context for high-risk/ambiguous cases | HIGH | Rule-driven reveal levels; prevents overload while still surfacing deep context when it matters |
| Typography scale tuned for dense ops tables | Increases scan speed and reduces visual fatigue in long queue sessions | LOW | Use an explicit type ramp for compact mode (line-height and numeric alignment), not just smaller font-size |
| Preset quality telemetry (time-to-action, reopen rate, filter churn) | Lets product team improve default presets based on measurable outcomes instead of opinion | MEDIUM | Add lightweight analytics events around view usage and action latency |
| Progressive disclosure regression suite (interaction + accessibility + state persistence) | Prevents UX decay as features evolve; protects list context and keyboard flow | MEDIUM | Automate tests for expand/collapse, focus return, persisted disclosure state, and no unexpected layout shift |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| "Show everything by default" mega-row | Feels like fewer clicks | Destroys scanability, increases error rate, and hurts performance in large queues | Default-collapsed details with one-step expansion and smart field prioritization |
| Unlimited nested disclosure levels | Feels "comprehensive" | Users get lost; navigation depth harms speed and learnability | Cap at 2 levels (list + details) and route rare deep edits to dedicated page/modal |
| Per-user fully freeform layout engine in v1 | Promises maximum flexibility | High implementation and support burden; inconsistent team operations and hard-to-reproduce bugs | Curated workflow presets + limited safe customization (columns/order/filters/density) |
| Aggressive auto-refresh that reflows active lists | Seems real-time and "live" | Causes context loss during review and accidental actions | Soft-refresh with conflict indicators and explicit "new items" affordance |

## Feature Dependencies

```text
Persistent preference model
    ├──requires──> Density controls
    ├──requires──> Saved views / operator presets
    └──requires──> Column visibility/reorder/pinning

Saved views / operator presets
    └──requires──> Fast filtering and search

Progressive disclosure regression suite
    ├──requires──> Progressive disclosure at row level
    └──requires──> Keyboard-first list operations

Focus-mode defaults by workflow
    ├──requires──> Saved views / operator presets
    ├──requires──> Typography scale tuned for dense ops tables
    └──enhances──> Keyboard-first list operations

Adaptive progressive disclosure
    └──requires──> Progressive disclosure at row level
```

### Dependency Notes

- **Saved views / operator presets requires fast filtering/search:** presets are just snapshots unless filters/sorts are expressive enough for real queue segmentation.
- **Focus-mode defaults requires preset infrastructure:** focus mode should be implemented as opinionated preset bundles, not separate parallel config systems.
- **Regression suite requires keyboard semantics and disclosure state model:** otherwise tests cannot reliably catch context-loss regressions.
- **Adaptive disclosure requires baseline master-detail first:** risk-based depth should layer on a stable two-level model, not replace it.

## MVP Definition

### Launch With (v1)

- [x] Density controls with compact default for ops-heavy views
- [x] Operator presets (saved view state: filters, columns, sort, density)
- [x] Row-level progressive disclosure (single expandable detail surface)
- [x] Keyboard shortcuts for search focus, row navigation, and open details
- [x] Progressive disclosure test coverage (state persistence + focus return)

### Add After Validation (v1.x)

- [ ] Workflow-specific focus-mode defaults shipped as curated preset packs
- [ ] Typography scale refinement for compact mode based on operator feedback
- [ ] Bulk-action guardrail improvements (undo + confirmation tuning) based on incident/error data

### Future Consideration (v2+)

- [ ] Adaptive disclosure by risk score/queue type (rule-driven detail depth)
- [ ] Preset recommendation engine from usage telemetry

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Density controls + compact default | HIGH | LOW | P1 |
| Saved views / operator presets | HIGH | MEDIUM | P1 |
| Row progressive disclosure + keyboard flow | HIGH | MEDIUM | P1 |
| Progressive disclosure regression tests | HIGH | MEDIUM | P1 |
| Focus-mode workflow defaults | HIGH | MEDIUM | P2 |
| Typography scale refinement | MEDIUM | LOW | P2 |
| Adaptive disclosure by risk/priority | MEDIUM | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Competitor A | Competitor B | Our Approach |
|---------|--------------|--------------|--------------|
| Saved views and table customization | GitHub Projects supports saved views, table field show/hide, sorting/grouping | Jira supports saved filters and list customization workflows | Treat presets as first-class operator workflow objects (not hidden settings) |
| Keyboard-first list operation | GitHub documents list/project keyboard shortcuts for filtering/navigation/actions | Jira provides keyboard shortcut navigation in issue workflows | Ship a compact, role-relevant shortcut map + test it in CI |
| Progressive disclosure in dense data UI | MUI/AG Grid master-detail patterns are standard for list-to-detail workflows | Atlassian components support inline dialog and compact spacing patterns | Use a strict two-level disclosure model and reserve deep edit for dedicated views |

## Sources

- Context7: MUI X Data Grid docs (`/mui/mui-x`) for density, state persistence, quick filter, and master-detail patterns (HIGH)
- Context7: Atlassian Design docs (`/websites/atlassian_design`) for compact spacing and inline dialog disclosure patterns (MEDIUM)
- MUI X official docs: https://mui.com/x/react-data-grid/accessibility/ (density), https://mui.com/x/react-data-grid/state/ (save/restore state), https://mui.com/x/react-data-grid/master-detail/ (master-detail) (HIGH)
- AG Grid official docs: https://www.ag-grid.com/javascript-data-grid/theming-compactness/ , https://www.ag-grid.com/javascript-data-grid/column-state/ , https://www.ag-grid.com/javascript-data-grid/master-detail-grids/ (HIGH)
- GitHub Docs: https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/filtering-and-searching-issues-and-pull-requests , https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/customizing-the-table-layout , https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/managing-your-views , https://docs.github.com/en/get-started/accessibility/keyboard-shortcuts (HIGH)
- Nielsen Norman Group progressive disclosure article (older but still conceptually relevant): https://www.nngroup.com/articles/progressive-disclosure/ (LOW for recency, MEDIUM for pattern framing)

---
*Feature research for: moderation/admin dashboard density and progressive disclosure*
*Researched: 2026-02-19*
