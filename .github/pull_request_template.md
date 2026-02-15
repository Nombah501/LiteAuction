## Summary

- 

## Linked Issue

- Closes #

## Scope

- Type: feature / fix / test / refactor / docs
- Sprint label on PR: `sprint:...`

## Validation

- [ ] `python -m ruff check app tests`
- [ ] `python -m pytest -q tests`
- [ ] `RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@127.0.0.1:5432/auction_test python -m pytest -q tests/integration`
- [ ] Integration suite repeated once (anti-flaky)

## Self Review

- [ ] Permissions/scope checks verified for new paths
- [ ] Idempotency/retry behavior verified for state-changing actions
- [ ] DB transaction boundaries and side effects reviewed
- [ ] Timeline/event ordering impact reviewed (if touched)

## Risk & Rollback

- Risk level: low / medium / high
- Main risk:
- Rollback plan:

## Manual QA

- Status: done / deferred
- If done: attach evidence (screenshots + pass/fail matrix)
- If deferred: when it will be run and by whom

## Reviewer Focus

- 

## Policy Gate

- [ ] PR body includes `Closes #<issue_id>`
- [ ] PR has at least one `sprint:*` label
