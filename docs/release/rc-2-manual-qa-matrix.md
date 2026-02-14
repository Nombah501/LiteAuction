# RC-2 Manual QA Matrix

Date: 2026-02-14
Tester: 

How to fill quickly:

- Put `PASS` or `FAIL` in `Result`.
- In `Notes`, add short proof (`ids/action/screenshot`).
- If `FAIL`, include blocker id and expected fix owner.

## Core Flow Cases

| Case | Result | Notes |
|---|---|---|
| RK-01 Manage user page shows risk level/score/reasons | PENDING | user_id=<id>, evidence=<file> |
| RK-02 High-risk seller cannot publish without assigned guarantor | PENDING | seller_tg=<id>, blocked message includes `/guarant`, evidence=<file> |
| RK-03 High-risk seller with recent assigned guarantor can publish | PENDING | seller_tg=<id>, assigned request id=<id>, evidence=<file> |
| AP-01 Appeals page shows SLA status labels correctly | PENDING | open/in_review/resolved samples, evidence=<file> |
| AP-02 Appeals escalation filter (`all/only/none`) works | PENDING | query params + list contents, evidence=<file> |
| TS-01 Trust indicators visible on `/manage/users` | PENDING | risk column with tooltips, evidence=<file> |
| TS-02 Trust indicators visible on `/auctions` | PENDING | seller risk column rendered, evidence=<file> |
| TS-03 Trust indicators visible on `/signals` and `/appeals` | PENDING | user/appellant risk columns rendered, evidence=<file> |
| TF-01 `/tradefeedback` accepts valid seller/winner input | PENDING | auction_id=<uuid>, rating/comment stored, evidence=<file> |
| TF-02 `/tradefeedback` rejects non-participant | PENDING | outsider tg=<id>, denial message, evidence=<file> |
| TF-03 `/trade-feedback` list filters/search and pagination | PENDING | status filters + query checks, evidence=<file> |
| TF-04 Hide/Unhide feedback actions update status with CSRF and scope checks | PENDING | feedback_id=<id>, status transitions, evidence=<file> |
| RP-01 `/manage/user/{id}` reputation summary math is correct | PENDING | received/visible/hidden/avg values, evidence=<file> |
| RP-02 Recent feedback table on profile matches moderation queue | PENDING | profile vs `/trade-feedback`, evidence=<file> |

## Visual/Release Cases

| Case | Result | Notes |
|---|---|---|
| Manage user profile readability on desktop | PENDING | risk + rewards + reputation sections readable, evidence=<file> |
| Manage user profile readability on mobile width | PENDING | tables scrollable and labels visible, evidence=<file> |
| Appeals table readability after SLA/escalation columns | PENDING | narrow viewport check, evidence=<file> |
| Trade feedback moderation table readability | PENDING | filters/actions accessible, evidence=<file> |

## Evidence

- Risk gate screenshots/log snippets:
- Appeals SLA/escalation screenshots:
- Trust surface screenshots (`/manage/users`, `/auctions`, `/signals`, `/appeals`):
- Trade feedback command traces and DB rows:
- Trade feedback moderation screenshots:
- Manage user reputation screenshots:
- Other notes/artifacts:

## Final Verdict

- Release candidate readiness:
- Blocking issues (if any):
- Follow-up tasks (if any):
