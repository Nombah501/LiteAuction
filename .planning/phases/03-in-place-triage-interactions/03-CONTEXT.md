# Phase 3: In-Place Triage Interactions - Context

**Gathered:** 2026-02-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Enable operators to inspect queue rows and act without leaving list context by using inline disclosure with progressive detail loading. Preserve active triage context (position, focus, filters) and keep interactions within a two-level model (list + details).

</domain>

<decisions>
## Implementation Decisions

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

</decisions>

<specifics>
## Specific Ideas

- Strong preference for inline triage flow that avoids context loss.
- Progressive loading should prioritize immediately actionable information.

</specifics>

<deferred>
## Deferred Ideas

None - discussion stayed within phase scope.

</deferred>

---

*Phase: 03-in-place-triage-interactions*
*Context gathered: 2026-02-20*
