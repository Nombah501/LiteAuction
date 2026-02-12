# Sprint 19 Manual QA Checklist

## Goal

Validate moderation callback UX and data consistency after callback actions:

- queue message edit behavior for complaint and fraud flows,
- timeline coherence for complaint/risk lifecycle,
- no side effects on permission denied and repeated callback clicks.

## Preconditions

- Branch: `sprint-19-*`.
- Local env is up (`bot`, `db`, `redis`, `admin`).
- Migrations applied: `alembic upgrade head`.
- Test accounts are ready:
  - owner moderator (full scopes),
  - operator moderator (without `user:ban`),
  - seller, reporter, suspect bidder.
- Moderation queue chat/thread is configured via env:
  - `MODERATION_CHAT_ID`,
  - optional `MODERATION_THREAD_ID`.

## Preflight (required before manual run)

Run and confirm all commands pass:

```bash
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest -q tests
RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@127.0.0.1:5432/auction_test .venv/bin/python -m pytest -q tests/integration
```

## Test Cases

### MQ-01 Complaint freeze updates queue and timeline

Steps:

1. Create an active auction and place at least one bid.
2. Send complaint from a different user to create a moderation queue message.
3. In moderation queue, click `Freeze` (`modrep:freeze`).
4. Open timeline page for this auction in admin web.

Expected:

- Callback returns success.
- Complaint becomes non-open (`RESOLVED`).
- Auction status becomes `FROZEN` and auction post is refreshed.
- Original queue message is edited (same message id), action buttons are replaced by panel navigation.
- Timeline contains complaint creation + complaint resolution + moderation action entries in chronological order.

### MQ-02 ban_top denied for operator without user ban scope

Steps:

1. Create complaint with a valid target user (`target_user_id` is present).
2. Log in as operator that has no `user:ban` scope.
3. Click complaint action `ban_top`.

Expected:

- Callback shows permission denied alert.
- Complaint stays `OPEN`.
- No blacklist entry is created for target user.
- Queue message is not switched to resolved state.
- Timeline does not receive new moderation/resolve events for this action attempt.

### MQ-03 Fraud signal ban updates queue and timeline

Steps:

1. Generate or seed an open fraud signal for an active auction.
2. In moderation queue, click `Ban` (`modrisk:ban`) as owner/full-scope moderator.
3. Open timeline page for the same auction.

Expected:

- Signal becomes non-open (`CONFIRMED`) with resolution note.
- Target user gets active blacklist entry.
- Auction post is refreshed.
- Target user receives Telegram notification about ban.
- Queue message is edited to resolved state and action buttons are replaced.
- Timeline contains signal creation + signal resolution + moderation action in correct order.

### MQ-04 Idempotency on repeated callback click

Steps:

1. Pick an open complaint or fraud signal queue message.
2. Double click same action quickly (or click once and immediately retry).

Expected:

- First click applies state transition.
- Second click returns already-processed warning.
- No duplicate moderation side effects:
  - no duplicate blacklist entries,
  - no duplicate status transitions,
  - no duplicate timeline events for the same resolution.

### TL-01 Timeline consistency after complaint path

Steps:

1. Run one complaint action (`freeze`, `rm_top`, or `dismiss`).
2. Open `/timeline/auction/<auction_uuid>`.

Expected:

- Event sequence is coherent: create complaint -> resolve complaint (if action resolves) -> related moderation action.
- If bid was removed, timeline includes bid removal details and reason.
- No missing resolver fields for resolved complaint.

### TL-02 Timeline consistency after fraud path

Steps:

1. Run one fraud action (`ignore`, `freeze`, or `ban`).
2. Open `/timeline/auction/<auction_uuid>`.

Expected:

- Event sequence is coherent: signal created -> signal resolved -> related moderation action.
- Resolver and resolution note are present on resolved signal.
- No duplicate events created by a single callback action.

## Evidence to attach in PR

- 2-3 screenshots from moderation queue before/after callback edits.
- 2 screenshots from timeline pages (complaint path + fraud path).
- One short pass/fail matrix for cases `MQ-01..MQ-04`, `TL-01..TL-02`.
- If any case fails: include auction id, complaint/signal id, actor role, and callback action used.

## Result Template

Copy this block into PR description:

```text
Manual QA (Sprint 19)

MQ-01: PASS/FAIL - notes
MQ-02: PASS/FAIL - notes
MQ-03: PASS/FAIL - notes
MQ-04: PASS/FAIL - notes
TL-01: PASS/FAIL - notes
TL-02: PASS/FAIL - notes

Evidence:
- queue before/after screenshots: <links or filenames>
- timeline screenshots: <links or filenames>
```
