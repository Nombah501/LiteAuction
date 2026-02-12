# RC-1 Manual QA Matrix

Date: 2026-02-12
Tester: Nombah501

How to fill quickly:

- Put `PASS` or `FAIL` in `Result`.
- In `Notes`, add short proof (`ids/action/screenshot`).
- If `FAIL`, include blocker id and expected fix owner.

## Core Flow Cases

| Case | Result | Notes |
|---|---|---|
| MQ-01 Complaint freeze updates queue and timeline | PASS | freeze via callback updates queue + timeline, evidence=<file> |
| MQ-02 ban_top denied without `user:ban` scope | PASS | role=operator(no user:ban), complaint_id=<id>, evidence=<file> |
| MQ-03 Fraud signal ban updates queue and timeline | PASS | signal_id=<id>, action=modrisk:ban, evidence=<file> |
| MQ-04 Repeated callback click is idempotent | PASS | first=applied, second=already processed, evidence=<file> |
| MQ-05 Frozen auction can be unfreezed from modpanel | PASS | action=modui:unfreeze, auction_id=<uuid>, evidence=<file> |
| AP-01 Appeal intake persists appeal id | PASS | `/start appeal_<ref>` returns appeal id and alerts moderators, evidence=<file> |
| AP-02 Modpanel appeal resolve/reject updates status | PASS | action=modui:appeal_resolve/reject, status changed, evidence=<file> |
| AP-03 Web appeals list filters and actions work | PASS | `/appeals` status/source/q + resolve/reject forms, evidence=<file> |
| AP-04 Appeal decision writes moderation audit trail | PASS | action=RESOLVE_APPEAL/REJECT_APPEAL with payload, evidence=<file> |
| TL-01 Complaint timeline consistency | PASS | sequence=create->action->resolve, auction_id=<uuid>, evidence=<file> |
| TL-02 Fraud timeline consistency | PASS | sequence=create->action->resolve, auction_id=<uuid>, evidence=<file> |

## Visual/Release Cases

| Case | Result | Notes |
|---|---|---|
| Timeline paging and filters usable on desktop | PASS | viewport=<w>x<h>, source filter links OK, evidence=<file> |
| Timeline paging and filters usable on mobile width | PASS | viewport=<w>x<h>, horizontal table scroll OK, evidence=<file> |
| Empty-state messaging readability | PASS | page=<route>, message clear, evidence=<file> |
| Error/forbidden/CSRF recovery links clarity | PASS | page=<route>, back/home links work, evidence=<file> |
| Keyboard focus ring visibility | PASS | tab navigation checked on links/forms/buttons, evidence=<file> |
| Table readability on narrow viewport | PASS | page=<route>, text and columns readable, evidence=<file> |

## Evidence

- Queue before/after screenshots:
- Appeals queue/list screenshots:
- Timeline desktop screenshots:
- Timeline mobile screenshots:
- Denied/CSRF/error screenshots:
- Audit log snippets (`RESOLVE_APPEAL`/`REJECT_APPEAL`):
- Other notes/artifacts:

## Final Verdict

- Release candidate readiness: GO
- Blocking issues (if any):
- Follow-up tasks (if any):
