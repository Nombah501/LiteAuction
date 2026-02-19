# Codebase Concerns

**Analysis Date:** 2026-02-19

## Tech Debt

**Tracked build artifact source tree (`build/lib`)**
- Severity: High
- Issue: A second Python source tree is committed under `build/lib/app`, creating two competing copies of application code.
- Files: `build/lib/app/web/main.py`, `build/lib/app/services/auction_service.py`, `build/lib/app/bot/handlers/moderation.py`, `.gitignore`
- Evidence: Repository tracks `build/lib/app/**/*.py` and local parity check shows many mismatches/missing files versus `app/**/*.py`.
- Impact: Reviewers and automation can read stale code, packaging may ship outdated modules, and refactors require duplicate edits.
- Fix approach: Treat `build/` as generated output, remove tracked build sources from git, and add `build/` to `.gitignore`.

**Oversized monolith modules**
- Severity: High
- Issue: Core behavior is concentrated in very large files.
- Files: `app/web/main.py`, `app/bot/handlers/moderation.py`, `app/bot/handlers/start.py`
- Evidence: File sizes are ~4043, ~3253, and ~1474 lines respectively.
- Impact: High merge-conflict rate, slower onboarding, and elevated regression risk for unrelated changes.
- Fix approach: Split by bounded context (auth, pages, action handlers, rendering helpers; complaint/risk/appeal callback handlers), keep route registration thin.

**Forward-only enum migrations without downgrade path**
- Severity: Medium
- Issue: Multiple Alembic migrations intentionally use `pass` in `downgrade()` for enum changes.
- Files: `alembic/versions/0009_add_escalate_appeal_action.py`, `alembic/versions/0021_points_appeal_boost_evt.py`, `alembic/versions/0030_bot_profile_photo_actions.py`
- Evidence: Downgrade methods explicitly state enum value removal unsupported and no compensating strategy is encoded.
- Impact: Rollbacks depend on manual DB intervention and increase incident recovery complexity.
- Fix approach: Keep forward-only policy explicit in runbooks and add rollback playbooks per migration family (or shadow enums + cast strategy when rollback is mandatory).

## Known Bugs

**Race condition when creating actor user from web auth context**
- Severity: Medium
- Symptoms: Concurrent first-time actions for the same `tg_user_id` can fail with integrity errors.
- Files: `app/web/main.py`
- Trigger: `_resolve_actor_user_id()` checks for existing user, then inserts and commits without conflict handling.
- Evidence: Code path at `app/web/main.py:920`-`app/web/main.py:937` has no `IntegrityError` retry branch.
- Workaround: Retry failed request.
- Fix approach: Use upsert semantics (`ON CONFLICT DO NOTHING` + re-read) or catch `IntegrityError` and re-query.

**Health endpoint can report healthy while dependencies are down**
- Severity: Medium
- Symptoms: `/health` returns `{status: ok}` even if DB or Redis is unavailable.
- Files: `app/web/main.py`, `app/db/session.py`, `app/infra/redis_client.py`
- Trigger: Web health route is static and does not probe dependencies.
- Evidence: `app/web/main.py:940`-`app/web/main.py:942` returns constant response.
- Workaround: Use container healthcheck command (`python -m app.healthcheck`) instead of web `/health` for readiness.
- Fix approach: Add readiness endpoint that checks `ping_database()` and `ping_redis()` with timeout.

## Security Considerations

**Full-owner access via token in query string/header**
- Severity: High
- Risk: Possession of admin token grants owner scope set immediately, and query-string transport leaks through logs/referrers.
- Files: `app/web/auth.py`, `app/services/rbac_service.py`, `app/web/main.py`
- Current mitigation: HMAC compare is used for token comparison.
- Evidence: `_token_from_request()` accepts query/header in `app/web/auth.py:32`-`app/web/auth.py:37`; `resolve_allowlist_role(... via_token=True)` returns owner in `app/services/rbac_service.py:39`-`app/services/rbac_service.py:42`; `_path_with_auth()` propagates `?token=` across links in `app/web/main.py:389`-`app/web/main.py:395`.
- Recommendations: Remove query-token auth, restrict to short-lived session or signed one-time login links, and keep token out of generated URLs.

**Session/CSRF secret fallback to bot token**
- Severity: High
- Risk: If `ADMIN_WEB_SESSION_SECRET` is unset, web auth secrets derive from `BOT_TOKEN`, coupling bot compromise to admin web compromise.
- Files: `app/web/auth.py`, `app/web/main.py`, `app/config.py`
- Current mitigation: Optional dedicated secret exists (`admin_web_session_secret`).
- Evidence: Fallback in `_session_secret()` (`app/web/auth.py:40`-`app/web/auth.py:45`) and `_csrf_secret()` (`app/web/main.py:397`-`app/web/main.py:399`); default setting is empty in `app/config.py:86`.
- Recommendations: Make `ADMIN_WEB_SESSION_SECRET` mandatory at startup for web mode and fail fast when missing.

**Insecure-by-default cookie transport flag**
- Severity: Medium
- Risk: Session cookie can be sent without `Secure` if environment variable is not set.
- Files: `app/config.py`, `app/web/main.py`, `docker-compose.yml`
- Current mitigation: Configurable secure flag exists.
- Evidence: Default `admin_web_cookie_secure=False` in `app/config.py:88`; cookie uses setting in `app/web/main.py:1009`-`app/web/main.py:1016`.
- Recommendations: Default to `True`, enforce HTTPS in deployment, and explicitly document local-dev override only.

## Performance Bottlenecks

**Sequential finalize loop with per-auction DB transactions**
- Severity: High
- Problem: Finalization handles expired auctions one-by-one with separate sessions and follow-up network operations.
- Files: `app/services/auction_service.py`, `app/services/auction_watcher.py`
- Cause: `finalize_expired_auctions()` selects IDs, loops each ID, opens a new transaction, refreshes post, then sends notifications sequentially.
- Evidence: `app/services/auction_service.py:798`-`app/services/auction_service.py:854`.
- Improvement path: Batch lock/update in chunks, decouple post refresh/notifications into queue jobs, and parallelize I/O with bounded concurrency.

**Redis keyspace scans for metrics snapshots**
- Severity: Medium
- Problem: Snapshot building performs repeated `SCAN`/`MGET` over metric key patterns.
- Files: `app/services/notification_metrics_service.py`, `app/bot/handlers/moderation.py`
- Cause: All-time and window views walk Redis keyspace in nested loops.
- Evidence: `app/services/notification_metrics_service.py:423`-`app/services/notification_metrics_service.py:533`.
- Improvement path: Maintain pre-aggregated counters by window and dimensions, and use bounded sorted-set/hashes for top-k queries.

**`ILIKE %...%` user search on non-indexed username**
- Severity: Medium
- Problem: User search can degrade as `users` grows.
- Files: `app/web/main.py`, `app/db/models.py`
- Cause: Query uses wildcard `ILIKE` (`%term%`) against `User.username` without dedicated trigram/lower index.
- Evidence: Filter at `app/web/main.py:2649`-`app/web/main.py:2658`; model field at `app/db/models.py:45` has no index.
- Improvement path: Add normalized search column or pg_trgm index and switch to indexed predicate.

## Fragile Areas

**Private topic routing relies on global mutable process caches**
- Severity: High
- Files: `app/services/private_topics_service.py`
- Why fragile: `_TOPICS_CAPABILITY_CACHE` and `_TOPIC_MUTATION_POLICY_CACHE` are unbounded in-memory dicts with no TTL/invalidation and are process-local.
- Safe modification: Isolate cache behind interface with explicit expiry and size limits; keep DB source of truth for critical routing decisions.
- Test coverage: Unit coverage exists for helper behavior (`tests/test_private_topics_service.py`), but no stress tests for long-running cache growth or multi-process consistency.

**Broad exception swallowing in interaction-critical paths**
- Severity: Medium
- Files: `app/bot/handlers/create_auction.py`, `app/bot/handlers/start.py`, `app/bot/handlers/moderation.py`, `app/services/private_topics_service.py`
- Why fragile: Several paths catch `Exception`/Telegram errors and silently `pass`, making delivery/routing failures hard to detect and diagnose.
- Safe modification: Replace `pass` with structured warning logs (context IDs), keep user-safe fallback response, and emit counters for suppressed failures.
- Test coverage: Functional flows are tested, but failure-observability assertions are limited.

## Scaling Limits

**State and worker model are single-process oriented**
- Severity: High
- Current capacity: One bot process with in-memory FSM state and co-located background watchers.
- Limit: Horizontal scaling causes FSM inconsistency and duplicate watcher work unless external coordination is added.
- Files: `app/main.py`, `app/services/auction_watcher.py`, `app/services/appeal_escalation_watcher.py`, `app/services/outbox_watcher.py`
- Evidence: `Dispatcher(storage=MemoryStorage())` at `app/main.py:56`; watchers started in same process at `app/main.py:61`-`app/main.py:63`.
- Scaling path: Move FSM to Redis-backed storage, separate watcher processes, and use leader election or DB locks for singleton jobs.

**Single-worker admin runtime**
- Severity: Medium
- Current capacity: Uvicorn started with defaults (single worker process).
- Limit: CPU-bound rendering or slow DB requests reduce responsiveness for all admin users.
- Files: `app/web/main.py`
- Evidence: `uvicorn.run(..., host='0.0.0.0', port=8080)` in `app/web/main.py:4037`-`app/web/main.py:4039`.
- Scaling path: Run under process manager with multiple workers and external reverse proxy; move heavy page assembly to async service layer.

## Dependencies at Risk

**Unpinned transitive dependency resolution (no lockfile)**
- Severity: Medium
- Risk: Builds can change across environments due to floating ranges.
- Impact: Non-deterministic CI/local behavior and surprise regressions.
- Files: `pyproject.toml`
- Evidence: Version ranges (`>=,<`) are used and no lockfile is present in repository.
- Migration plan: Adopt lockfile-based workflow (`pip-tools` or equivalent), commit lock artifact, and pin deploy installs to lock.

## Missing Critical Features

**Operational surface for failed integration outbox events**
- Severity: High
- Problem: Failed outbox records accumulate without dedicated operator UI/alerts.
- Blocks: Reliable recovery for GitHub automation failures and timely manual replay.
- Files: `app/services/outbox_service.py`, `app/services/outbox_watcher.py`, `app/web/main.py`
- Evidence: Events transition to `FAILED` in `app/services/outbox_service.py:104`-`app/services/outbox_service.py:107`; no admin route references outbox entities.
- Fix approach: Add admin outbox page (failed/pending filters), replay endpoint with audit logging, and alert threshold on failure backlog.

## Test Coverage Gaps

**Background watcher loops are untested**
- What's not tested: Cancellation, retry/backoff behavior, and repeated-failure handling for watcher loops.
- Files: `app/services/auction_watcher.py`, `app/services/appeal_escalation_watcher.py`, `app/services/outbox_watcher.py`
- Risk: Infinite-loop behavior can regress silently and impact production stability.
- Priority: High
- Evidence: No tests reference `run_auction_watcher`, `run_appeal_escalation_watcher`, or `run_outbox_watcher` under `tests/`.

**Healthcheck behavior is untested**
- What's not tested: Exit-code semantics for DB/Redis failure combinations and exception diagnostics.
- Files: `app/healthcheck.py`
- Risk: Readiness reporting regressions may go unnoticed during deploys.
- Priority: Medium
- Evidence: No tests reference `app.healthcheck` or healthcheck module behavior.

**Real GitHub HTTP client behavior lacks direct tests**
- What's not tested: HTTP error parsing, timeout behavior, and malformed payload handling in concrete client implementation.
- Files: `app/services/github_automation_service.py`
- Risk: Integration outages may surface only in production outbox processing.
- Priority: Medium
- Evidence: Existing outbox integration tests use fake issue clients in `tests/integration/test_feedback_outbox_automation.py` and do not exercise `GitHubApiIssueClient` network code.

---

*Concerns audit: 2026-02-19*
