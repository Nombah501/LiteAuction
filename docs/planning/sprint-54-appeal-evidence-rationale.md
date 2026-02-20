# Sprint 54 S54-003: Appeal Evidence Timeline and Rationale Artifact

## Goal

Provide inline evidence context for appeal queue rows and persist concise rationale artifacts for moderation decisions with audit metadata.

## Implementation Notes

- Appeals triage detail endpoint (`/actions/triage/detail-section`) now renders queue-local evidence for `queue_key=appeals`:
  - `primary`: event timeline (created, in-review, escalation, finalization, moderation actions).
  - `secondary`: source evidence snapshot (complaint/risk context) and rationale artifact list.
  - `audit`: immutable-style audit metadata summary with append-only moderation log references.
- Resolve/reject appeal actions now store `payload.rationale_artifact` in moderation logs with:
  - `summary`
  - `actor_user_id`
  - `actor_tg_user_id`
  - `source`
  - `recorded_at`
  - `immutable=true`

## Safety and Traceability

- Existing RBAC checks remain unchanged for triage detail and action endpoints.
- Existing CSRF checks remain unchanged for mutation endpoints.
- Rationale records are stored in append-only moderation log entries (no update path exposed).

## Validation Targets

- `tests/integration/test_web_appeals.py`
  - verify rationale artifact is present in moderation log payload after resolve action.
  - verify appeals triage detail endpoint renders evidence timeline, rationale artifact section, and immutable audit marker.
