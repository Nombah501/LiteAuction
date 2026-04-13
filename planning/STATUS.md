# Planning Status

Last sync: 2026-04-14 (manual)
Active milestone: Web Panel Refactor (Wave 1 completed)

## Wave 1: Web Panel Refactor (2026-04-14)

| Item | Title | Status |
|---|---|---|
| T1 | Add jinja2 dependency + tune db connection pool | ✅ `aa4646a` |
| T2 | Require explicit ADMIN_WEB_SESSION_SECRET | ✅ `1e1f712` |
| T3 | Extract deps.py (auth, CSRF, DB) | ✅ `e53b693` |
| T4 | Extract filters.py (query param parsers) | ✅ `1c9ecb0` |
| T5 | Extract components.py (UI helpers) | ✅ `7385b7a` |
| T6 | Router registry + thin app factory | ✅ `74f5e02` |
| T7-T19 | 13 domain-scoped router modules | ✅ `52b39a1` |
| T20 | End-to-end route verification | ✅ |
| T21 | Jinja2 base template + CSS | ✅ `319aea5` |
| T22 | Migrate 13 page templates to Jinja2 | ✅ `cb397da` |
| T23 | Final cleanup + validation | ✅ `0874497` |

Validation: 375 passed, 2 skipped, 0 failed. Ruff clean. 43 routes.

Branch: `refactor/web-wave1`

## v1.2 Implementation (2026-04-11)

| Item | Title | Commits | Status |
|---|---|---|---|
| SLA-01/02 | SLA health columns + aging filter chips for complaints and signals | `62bae59`, `2fb3b1a` | ✅ |
| EVID-01/02 | Evidence timeline detail sections for complaints and signals | `0c3e8d7`, `9778570` | ✅ |
| TRND-01/02 | Telemetry panel coverage (already existed) | — | ✅ |
| SAFE-11/12 | RBAC/CSRF reuse + immutable rationale artifacts | — | ✅ |
| TEST-21 | SLA derivation unit tests (11 tests) | `7e7d0fc` | ✅ |
| TEST-22 | Integration tests for timeline + telemetry (5 tests) | `57ea67a` | ✅ |

Validation: 331 passed, 2 skipped, 0 failed. Ruff clean.

## Sprint 56 (completed, 2026-02-23)

| Item | Title | Issue | PR |
|---|---|---|---|
| S56-001 | Harden publish flow for album and post consistency | [#263](https://github.com/Nombah501/LiteAuction/issues/263) (closed) | [#273](https://github.com/Nombah501/LiteAuction/pull/273) (merged) |
| S56-002 | Normalize moderation topic routing taxonomy | [#264](https://github.com/Nombah501/LiteAuction/issues/264) (closed) | [#274](https://github.com/Nombah501/LiteAuction/pull/274) (merged) |
| S56-003 | Redesign /points output into compact and detailed modes | [#265](https://github.com/Nombah501/LiteAuction/issues/265) (closed) | [#275](https://github.com/Nombah501/LiteAuction/pull/275) (merged) |
| S56-004 | Modularize oversized bot handlers into use-case slices | [#266](https://github.com/Nombah501/LiteAuction/issues/266) (closed) | [#276](https://github.com/Nombah501/LiteAuction/pull/276) (merged) |
| S56-005 | Expand bot smoke and resilience test matrix | [#267](https://github.com/Nombah501/LiteAuction/issues/267) (closed) | [#277](https://github.com/Nombah501/LiteAuction/pull/277) (merged) |

## Recovery Checklist

- Keep this file updated at each planning sync.
- Link every implementation PR to an issue with `Closes #<id>`.
- Keep sprint labels (`sprint:*`) on every active PR.
