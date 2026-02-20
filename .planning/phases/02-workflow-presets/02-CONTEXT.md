# Phase 2: Workflow Presets - Context

**Gathered:** 2026-02-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 delivers reusable queue presets: operators/admins can save and apply named queue configurations (filters, sort, visible columns, density), and each moderation context (Moderation, Appeals, Risk, Feedback) opens with an admin-defined default preset. This phase clarifies behavior within that capability only.

</domain>

<decisions>
## Implementation Decisions

### Preset lifecycle and ownership
- Personal presets can be created by both operators and admins.
- Personal presets can be edited/deleted by the preset owner and by admins.
- No separate shared preset catalog in this phase (only personal presets + admin defaults by queue context).
- If an active preset is deleted, show a user choice: keep current on-screen state or revert to queue default.

### Save and naming flow
- Saving uses a suggested name template, but users can edit it before saving.
- If a user already has a preset with the same name, prompt to overwrite existing or save as new.
- Support both explicit actions: "Update current preset" and "Save as new preset".
- Preset name validation: 1-40 characters.

### Preset apply behavior
- Applying a selected preset is immediate (no extra apply button).
- If unsaved view changes exist, ask for confirmation before switching presets.
- Show active preset plus an "modified" indicator when current state diverges from saved preset.
- If stored preset includes now-invalid/removed parameters, apply valid parts and show a short notice.

### Admin default preset policy
- One admin default preset per queue context.
- Admin default auto-applies on operator's first entry to that queue context.
- After first entry, opening the queue should use the operator's last selected preset.
- Provide a clear operator action to reset back to admin default.

### Claude's Discretion
- No explicit discretionary areas were requested; decisions above are considered locked for planning.

</decisions>

<specifics>
## Specific Ideas

No external product references were provided. Preference is explicit user control where actions can discard state (confirm on switch, prompt on duplicate name, reset-to-default action visible).

</specifics>

<deferred>
## Deferred Ideas

None - discussion stayed within phase scope.

</deferred>

---

*Phase: 02-workflow-presets*
*Context gathered: 2026-02-20*
