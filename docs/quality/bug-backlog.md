# Bug Backlog

Updated: 2026-02-13

## Priority Queue (Sprint 27-28 candidates)

| ID | Priority | Area | Title | Repro status | Owner | Target sprint | Status |
|---|---|---|---|---|---|---|---|
| BUG-001 | P1 | timeline/web | Timeline source filter should keep state across all navigation entry points | reproducible | assigned | 27 | done |
| BUG-002 | P1 | timeline/service | Timeline page assembly should not over-fetch rows under high page numbers | reproducible | assigned | 27 | done |
| BUG-003 | P1 | moderation/timeline | Callback retry paths must not create duplicate timeline side effects | reproducible | assigned | 28 | done |
| BUG-004 | P2 | web/rbac | Denied scope pages should preserve return navigation context consistently | reproducible | assigned | 28 | done |
| BUG-005 | P2 | web/ui | Timeline empty-state and filter-label rendering should be consistent for invalid or blank input | reproducible | assigned | 27 | done |
| BUG-006 | P1 | web/security | Reject protocol-relative return paths in `return_to` and denied-page back links | reproducible | assigned | post-sprint | done |

## Completed in Sprint 27

- `BUG-001`: timeline-to-manage-to-timeline navigation now preserves page/limit/source context.
- `BUG-002`: timeline page builder now returns early when page offset is outside total range and caps per-source fetch by total items.
- `BUG-005`: source filter input is normalized/deduplicated; blank filter is treated as `all` consistently.

## Completed in Sprint 28

- `BUG-003`: added idempotency regression coverage for repeated `modrisk:ban` callbacks (no duplicate logs/blacklist/timeline side effects).
- `BUG-004`: denied-scope pages now preserve safe return navigation using `return_to`/`Referer` context.

## Completed Post-sprint

- `BUG-006`: protocol-relative paths (for example `//host/path`) are now rejected in `return_to` and denied-page back links.

## Reproduction Notes

### BUG-001

1. Open `/timeline/auction/<id>?source=moderation,complaint&page=0&limit=50`.
2. Navigate via prev/next and quick source links.
3. Verify source state is always preserved where expected.

Expected: source context remains stable across pagination and quick links.
Actual: edge paths can reset source unexpectedly.

### BUG-002

1. Seed auction with large timeline history.
2. Request higher pages with low `limit`.
3. Compare fetched rows vs shown rows.

Expected: bounded fetch behavior remains predictable and efficient.
Actual: candidate for over-fetch pressure under high page index.

### BUG-003

1. Trigger moderation callback action.
2. Repeat callback quickly or retry manually.
3. Inspect moderation log and timeline entries.

Expected: idempotent state and no duplicate timeline side effects.
Actual: guard exists; keep as focused regression watchlist item.

### BUG-004

1. Login as operator with partial scopes.
2. Attempt forbidden action from management pages.
3. Follow navigation links back.

Expected: consistent return context and clear access messaging.
Actual: candidate edge inconsistency in return path continuity.

### BUG-005

1. Open timeline with empty, mixed-case, or malformed source values.
2. Observe header labels and empty-state rendering.

Expected: normalized filter label and consistent empty-state wording.
Actual: candidate UX inconsistency in boundary inputs.
