# Phase 2: Workflow Presets - Research

**Researched:** 2026-02-20
**Domain:** FastAPI server-rendered queue preset lifecycle (named presets, apply flow, admin defaults)
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
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

### Deferred Ideas (OUT OF SCOPE)
None - discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PSET-01 | Operator can save a named preset containing filters, sort order, visible columns, and density | Add named preset persistence model + save/update/overwrite flows + 1-40 name validation + per-queue filter/sort snapshot contract |
| PSET-02 | Operator can load a saved preset and see the list update to the stored view state | Immediate preset apply route/query wiring + unsaved-change confirm + modified indicator + tolerant partial-apply with notice |
| PSET-03 | Admin can define workflow-focused default presets for Moderation, Appeals, Risk, and Feedback queues | Add admin-default mapping per queue context + first-entry auto-apply + last-selected tracking + reset-to-default action |
</phase_requirements>

## Summary

Phase 2 should extend the existing Phase 1 dense-list architecture, not replace it. The current stack already has queue-scoped preference loading, strict server validation, CSRF-protected JSON writes, and a shared dense-list toolbar/script contract in `app/web/dense_list.py` and `app/web/main.py`. The fastest safe path is to add a named-preset domain on top of this contract and keep server authority for validation, ownership checks, and persistence.

The key implementation shift is moving from a single per-user-per-queue preference row (`admin_list_preferences`) to a lifecycle with multiple named presets, active-preset tracking, and admin defaults by queue context. Current behavior auto-saves every layout tweak; Phase 2 requires explicit save/update choices, unsaved-change prompts on switch, and modified-state detection against an active preset baseline.

The largest planning risk is state precedence. You must define deterministic resolution for first entry, admin default, last selected preset, deleted active preset, and invalid legacy preset payloads. If this precedence is ambiguous, regressions will appear as "random" queue layouts and operators will lose trust.

**Primary recommendation:** implement presets as a dedicated server-side domain (new tables + service + explicit apply/save/delete endpoints) while reusing existing dense-list render/query contracts and CSRF/RBAC patterns.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | `>=0.116,<1` | Queue HTML routes + preset action endpoints | Existing admin web runtime; auth/CSRF patterns already in place |
| SQLAlchemy async + PostgreSQL | `>=2.0.38,<3` | Preset persistence, upserts, ownership/default queries | Existing ORM/session pattern and proven ON CONFLICT support |
| Alembic | `>=1.14.1,<2` | Backward-safe schema migrations for preset tables | Required for rollout safety and rollback symmetry |
| Vanilla JS in server-rendered HTML | browser baseline | Immediate apply, modified indicator, confirmation prompts | Matches current dense-list script model; no new frontend runtime |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest + pytest-asyncio | repo standard | Unit/integration coverage for service + routes | For ownership, overwrite, default precedence, invalid payload fallback tests |
| Existing RBAC + CSRF helpers | repo standard | Authorization and request integrity | For owner/admin-only default management and all JSON write actions |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Dedicated preset tables | Expand `admin_list_preferences` JSON only | Faster initially, but difficult ownership/default/index semantics and harder audits |
| Server-authoritative persistence | Browser storage for presets/defaults | Fails multi-session consistency and shared-terminal safety |

**Installation:**
```bash
# No new runtime dependency is required for Phase 2.
# Expected additions are app code + Alembic migration(s) + tests.
```

## Architecture Patterns

### Recommended Project Structure
```text
app/
|- db/
|  `- models.py                                  # add preset entities + constraints
|- services/
|  `- (new) admin_queue_presets_service.py       # lifecycle, apply sanitization, default resolution
|- web/
|  |- dense_list.py                              # extend toolbar/script for preset UX state
|  `- main.py                                    # queue routes + preset action endpoints
alembic/
`- versions/
   `- 0037_workflow_presets.py                   # schema for named presets and context defaults
tests/
|- test_admin_queue_presets_service.py
|- test_web_dense_list_contract.py
`- integration/
   `- test_web_workflow_presets.py
```

### Pattern 1: Explicit Preset Domain + Deterministic State Precedence
**What:** Keep named presets, active selection, and admin defaults as explicit persisted concepts (not implicit JSON fields).
**When to use:** Always, because Phase 2 introduces ownership, overwrite flow, and first-entry behavior.
**Example (precedence contract):**
```text
if first_entry_for_user_and_context and admin_default_exists:
  active = admin_default
elif user_last_selected_preset_exists:
  active = user_last_selected_preset
else:
  active = fallback_baseline
```

### Pattern 2: Tolerant Apply, Strict Save
**What:** Save endpoints remain strict/validated; apply path sanitizes and drops invalid fields with a notice.
**When to use:** On loading old presets after schema or queue-column/filter evolution.
**Example:**
```text
save: reject unknown fields -> 400
apply: keep known fields, drop unknown fields, emit notice="Some preset settings were skipped"
```

### Pattern 3: Split "working state" from "saved preset"
**What:** Track active preset identity and compare current UI/query state to preset snapshot for modified marker.
**When to use:** Required by locked decision for unsaved-change confirmation and modified indicator.
**Example:**
```text
is_modified = normalize(current_view_state) != normalize(active_preset_state)
```

### Anti-Patterns to Avoid
- **Single-row overwrite model:** cannot satisfy named presets, overwrite prompts, or delete-active behavior.
- **Client-only ownership checks:** owner/admin permissions must be enforced server-side.
- **Hard failure on any stale field during apply:** violates locked "apply valid parts" behavior.
- **Implicit default behavior without persisted first-entry marker/last-selected tracking:** leads to nondeterministic queue opening state.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Duplicate-name race handling | pre-check then insert in Python | DB unique constraints + transactional upsert/branching | Avoids concurrent overwrite/new-save races |
| Upsert semantics | manual select-then-update loops | PostgreSQL `insert().on_conflict_do_update()` | Atomic and already used in Phase 1 service |
| Access control | UI-hidden buttons only | existing auth role/scopes + server ownership checks | Prevents unauthorized preset edits/deletes/default changes |
| CSRF for JSON writes | ad-hoc tokens | existing `_build_csrf_token` / `_validate_csrf_token` flow | Aligns with current web action safety |

**Key insight:** this phase is mostly state-model engineering; correctness depends on explicit persistence and precedence rules, not UI cosmetics.

## Common Pitfalls

### Pitfall 1: Context Drift Between Product Terms and Queue Keys
**What goes wrong:** "Moderation/Appeals/Risk/Feedback" defaults map inconsistently to actual route keys.
**Why it happens:** existing code keys are `complaints`, `signals`, `trade_feedback`, `appeals` (plus other queues).
**How to avoid:** define one canonical context-to-queue mapping table in service/constants and reuse everywhere.
**Warning signs:** default applies in one screen but not another that operators consider the same context.

### Pitfall 2: Unsaved-Change Logic Never Triggers (or Always Triggers)
**What goes wrong:** switch confirmation is noisy or absent.
**Why it happens:** comparing raw/unsanitized state or not normalizing query + columns + density before diffing.
**How to avoid:** one normalization function shared by save/apply/compare.
**Warning signs:** "modified" badge flips incorrectly after no-op interactions.

### Pitfall 3: Delete Active Preset Creates Silent State Loss
**What goes wrong:** current view disappears or resets without user choice.
**Why it happens:** delete endpoint does not return enough context for client decision branch.
**How to avoid:** return `was_active` + fallback/default metadata; client prompts "keep current vs revert default" before finalizing state.
**Warning signs:** operators report queue "jumping" after delete.

### Pitfall 4: First-Entry Default Reapplies Forever
**What goes wrong:** operator never stays on chosen preset because admin default keeps overriding.
**Why it happens:** no persisted "has_entered_context"/last-selected marker.
**How to avoid:** persist last-selected preset per user+context and treat absence as first-entry only.
**Warning signs:** opening queue repeatedly reverts to admin default despite prior selection.

### Pitfall 5: Stale Preset Fails Hard on Schema Evolution
**What goes wrong:** presets become unusable after filter/column changes.
**Why it happens:** strict validation in apply path (current Phase 1 behavior).
**How to avoid:** tolerant apply with per-field sanitization and user notice.
**Warning signs:** 400/500 errors when selecting older presets.

## Code Examples

Verified patterns from official docs and current codebase:

### SQLAlchemy PostgreSQL upsert (existing repo pattern)
```python
# Source: SQLAlchemy docs + app/services/admin_list_preferences_service.py
from sqlalchemy.dialects.postgresql import insert

stmt = insert(MyTable).values(...)
stmt = stmt.on_conflict_do_update(
    constraint="uq_name",
    set_={"updated_at": func.timezone("utc", func.now()), ...},
)
await session.execute(stmt)
```

### FastAPI JSON payload parse and HTTPException handling
```python
# Source: FastAPI docs + app/web/main.py action_save_dense_list_preferences
try:
    payload = await request.json()
except Exception as exc:
    raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

if not isinstance(payload, dict):
    raise HTTPException(status_code=400, detail="Payload must be an object")
```

### Existing dense-list route integration contract
```python
# Source: app/web/main.py
dense_config = await _load_dense_list_config(
    session,
    request=request,
    auth=auth,
    queue_key="appeals",
    requested_density=density,
    table_id="appeals-table",
    quick_filter_placeholder="id / ref / source / appellant / note",
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| One persisted queue preference (`subject_key + queue_key`) | Multiple named presets + active-selection/default policies | Phase 2 target | Supports reusable workflows and admin-onboarding defaults |
| Implicit always-save behavior | Explicit save/update + overwrite/confirm lifecycle | Phase 2 target | Matches locked user-control decisions |
| Strict load validation | Tolerant apply with notices for stale parameters | Phase 2 target | Prevents preset breakage after queue schema changes |

**Deprecated/outdated for this phase:**
- Treating `admin_list_preferences` as the only persistence model for queue view behavior.
- Assuming density/layout-only state is enough for workflow reuse (filters/sort must be represented).

## Implementation Plan Inputs (Task-Ready)

### Required planning decisions (must be explicit in PLAN)
1. Define canonical queue contexts for Phase 2 defaults:
   - `moderation -> complaints`
   - `appeals -> appeals`
   - `risk -> signals`
   - `feedback -> trade_feedback`
2. Define preset payload schema per context:
   - `density`, `columns.visible/order/pinned`, and context-specific filter/sort keys.
3. Define state precedence and transitions:
   - first entry + admin default, last selected reuse, delete-active branch, reset-to-default behavior.
4. Define ownership/permission checks:
   - owner/admin can edit/delete all; operator can edit/delete own only.

### Suggested build slices
1. **Schema foundation:** add named preset table(s), name constraints (1-40), ownership indexes, admin-default-per-context mapping.
2. **Preset service layer:** CRUD, duplicate-name overwrite flow, active/last-selected tracking, tolerant apply sanitizer with notice payload.
3. **Queue route wiring:** load/apply preset state on GET for four contexts; expose preset selector/save/update/delete/reset actions.
4. **Client contract updates:** active preset badge + modified indicator + unsaved-switch confirm + delete-active choice prompt.
5. **Role policy + audit behavior:** enforce owner/admin controls for non-owned presets and admin defaults; add/extend logging if required.
6. **Regression coverage:** unit + integration tests for precedence, ownership, duplicate handling, partial-apply notices, and first-entry default logic.

### Concrete file targets
- `app/db/models.py`: add preset entities and constraints.
- `alembic/versions/0037_workflow_presets.py`: create/drop new preset/default tables and indexes.
- `app/services/admin_queue_presets_service.py` (new): central lifecycle and precedence logic.
- `app/web/dense_list.py`: extend toolbar/script data attributes and client interaction hooks.
- `app/web/main.py`: four queue routes + preset action endpoints + reset flow wiring.
- `tests/test_admin_queue_presets_service.py` (new): schema/validation/precedence unit tests.
- `tests/integration/test_web_workflow_presets.py` (new): end-to-end route/action behavior.

### Validation strategy
- **Unit:** preset name validation (1-40), duplicate-name branch behavior, ownership checks, payload normalization and diff detection.
- **Integration (DB-backed):**
  - first queue entry auto-applies admin default,
  - subsequent entry uses last selected preset,
  - delete-active path supports both keep-current and revert-default,
  - stale preset parameters are partially applied with notice.
- **Security regression:** unauthorized edit/delete/default-set operations fail with 403/401.
- **Manual smoke:** operator first login, switch with modified state, reset-to-default discoverability.

## Open Questions

1. **Moderation context exact queue mapping**
   - What we know: product context names are Moderation/Appeals/Risk/Feedback; existing queue keys are `complaints`, `appeals`, `signals`, `trade_feedback`.
   - What's unclear: whether "Moderation" should include only `complaints` or also another queue.
   - Recommendation: lock mapping in plan up front and encode it as constants.

2. **Sort-order representation for contexts without user sort controls**
   - What we know: current routes use fixed SQL ordering; no user sort UI today.
   - What's unclear: whether Phase 2 introduces sort controls or stores fixed route sort as preset metadata.
   - Recommendation: include sort fields in payload contract now (even if fixed defaults initially) to satisfy PSET-01 explicitly.

## Sources

### Primary (HIGH confidence)
- Repository code: `app/web/main.py`, `app/web/dense_list.py`, `app/services/admin_list_preferences_service.py`, `app/db/models.py`, `tests/test_web_dense_list_contract.py`, `tests/integration/test_web_dense_list_foundations.py`, `tests/test_admin_list_preferences_service.py`.
- Context7 `/websites/sqlalchemy_en_21` - PostgreSQL `insert().on_conflict_do_update()` and async session patterns.
- Context7 `/websites/fastapi_tiangolo` - `Request.json()` behavior and `HTTPException` usage.

### Secondary (MEDIUM confidence)
- `.planning/research/SUMMARY.md` - phase-level state-model and preference-scope risk flags.
- `.planning/research/PITFALLS.md` - persistence/audit pitfall mapping for Phase 2.

### Tertiary (LOW confidence)
- None used for critical claims.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - directly validated via `pyproject.toml` and current code.
- Architecture: HIGH - based on existing Phase 1 implementation points and locked phase decisions.
- Pitfalls: MEDIUM - behavior risks are clear, but some UX edge-cases need implementation-time validation.

**Research date:** 2026-02-20
**Valid until:** 2026-03-22
