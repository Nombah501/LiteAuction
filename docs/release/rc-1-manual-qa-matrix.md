# RC-1 Manual QA Matrix

Date:
Tester:

## Core Flow Cases

| Case | Result | Notes |
|---|---|---|
| MQ-01 Complaint freeze updates queue and timeline | PASS/FAIL | |
| MQ-02 ban_top denied without `user:ban` scope | PASS/FAIL | |
| MQ-03 Fraud signal ban updates queue and timeline | PASS/FAIL | |
| MQ-04 Repeated callback click is idempotent | PASS/FAIL | |
| TL-01 Complaint timeline consistency | PASS/FAIL | |
| TL-02 Fraud timeline consistency | PASS/FAIL | |

## Visual/Release Cases

| Case | Result | Notes |
|---|---|---|
| Timeline paging and filters usable on desktop | PASS/FAIL | |
| Timeline paging and filters usable on mobile width | PASS/FAIL | |
| Empty-state messaging readability | PASS/FAIL | |
| Error/forbidden/CSRF recovery links clarity | PASS/FAIL | |
| Keyboard focus ring visibility | PASS/FAIL | |
| Table readability on narrow viewport | PASS/FAIL | |

## Evidence

- Queue before/after screenshots:
- Timeline desktop screenshots:
- Timeline mobile screenshots:
- Denied/CSRF/error screenshots:

## Final Verdict

- Release candidate readiness: GO / NO-GO
- Blocking issues (if any):
