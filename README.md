# LiteAuction Bot

MVP Telegram auction bot scaffold on `aiogram` + `PostgreSQL` + `Redis` with Docker Compose.

This repository currently contains **Sprint 0 + Sprint 1 + Sprint 2 + Sprint 3 + Sprint 4 + Sprint 5 + Sprint 6 + Sprint 7 + Sprint 8 + Sprint 9 + Sprint 10 + Sprint 11 + Sprint 12 + Sprint 13 + Sprint 14 + Sprint 15 + Sprint 16 + Sprint 17 + Sprint 18**:

- Dockerized runtime (`bot`, `db`, `redis`)
- `Alembic` migrations and initial PostgreSQL schema
- Async SQLAlchemy setup
- Basic bot startup with `/start`
- Startup and container health checks for DB/Redis
- FSM lot creation in private chat (`/newauction`)
- Inline auction publishing via `auc_<id>` and `chosen_inline_result`
- Live post updates after bids (`top-3`, current price, ending time)
- Buyout, anti-sniper (`2m -> +3m`, max `3`) and anti-mistake protections
- Background watcher for expired auctions
- Telegram moderation commands with audit log and blacklist
- In-post complaint flow to moderator queue with action buttons
- Auto fraud-signal scoring with moderator action queue
- Stateful moderator panel (`/modpanel`) and extended fraud heuristics
- Moderator dashboard stats (`/modstats`) and baseline spike anomaly scoring
- Historical cross-auction baseline heuristic and web admin panel
- Telegram Login auth for web admin and dispute timeline pages
- Web moderation actions with role-based permissions
- Fine-grained web RBAC scopes (`auction`, `bid`, `user-ban`, `role-manage`)
- Telegram moderation RBAC synced with scope model + `/role` commands
- Web CSRF protection and confirm-step for dangerous moderation actions
- Configurable soft-gate for bids/reports (`strict`/`grace`/`off`) with private `/start`
- Soft-gate conversion and onboarding funnel KPIs in web dashboard
- Regression tests and GitHub Actions CI for RBAC/CSRF/confirm-flow
- Integration regression tests for callback scope mapping and web post-refresh actions
- Role-management workflow tests (web + bot) and permission-downgrade edge-case coverage
- DB-backed RBAC integration tests and dedicated CI Postgres job
- End-to-end callback integration tests for complaint/risk moderation flows

## Sprint 0 Checklist

- [x] Project scaffold and dependency setup
- [x] PostgreSQL schema baseline (`users`, `auctions`, `bids`, moderation tables)
- [x] Redis connectivity foundation
- [x] Docker Compose for local/prod-like environment
- [x] Basic bot polling runtime and `/start`
- [x] Healthcheck command for bot container

## Sprint 1 Checklist (Core)

- [x] Create auction wizard: photo, description, start price, buyout/skip, min step, duration, anti-sniper
- [x] Publish draft via inline flow (`switch_inline_query` -> inline card)
- [x] Activate auction on publish and store `inline_message_id`
- [x] Bid buttons (`x1`, `x3`, `x5`) and buyout button
- [x] Live post refresh after each accepted action
- [x] Anti-mistake: no self-overbid, cooldown, duplicate guard, confirm for `x3/x5` and buyout
- [x] Auction auto-finish watcher and winner/seller notifications

## Sprint 2 Checklist (Moderation)

- [x] Moderator command set in Telegram private chat
- [x] Auction controls: freeze / unfreeze / force end
- [x] Fraud controls: remove bid by bid UUID
- [x] User controls: ban / unban by `tg_user_id`
- [x] Read tools: list recent bids and audit log feed
- [x] Blacklist enforcement for new auctions and bidding

## Sprint 3 Checklist (Report Workflow)

- [x] Complaint button in auction post (`Пожаловаться`)
- [x] Complaint cooldown and double-confirmation to reduce spam
- [x] Complaint persistence in database (`complaints` table)
- [x] Moderator queue notifications with action buttons
- [x] Callback moderation workflow: freeze / remove top bid / ban+remove / dismiss
- [x] Open complaints counter in auction post

## Sprint 4 Checklist (Anti-Fraud Core)

- [x] Fraud signal persistence in database (`fraud_signals` table)
- [x] Automatic risk scoring on accepted bids
- [x] Moderator queue notifications for fraud signals
- [x] Callback workflow for fraud signals: freeze / ban / ignore
- [x] Moderator command `/risk [auction_uuid]` for open signals

## Sprint 5 Checklist (Moderator UX + Heuristics)

- [x] Stateful moderator panel with inline navigation (`/modpanel`)
- [x] Browse open complaints/signals with paging and detail view
- [x] Return-to-panel flow after moderation callback actions
- [x] Duopoly (pair-collusion) heuristic in fraud scoring
- [x] Alternating-bid chain heuristic in fraud scoring

## Sprint 6 Checklist (Dashboard + Baseline Anomaly)

- [x] Moderator dashboard snapshot service
- [x] `/modstats` command and panel stats section
- [x] Baseline-spike heuristic in fraud scoring
- [x] Configurable anomaly tuning via env

## Sprint 7 Checklist (Historical Baseline + Web Admin)

- [x] Historical baseline heuristic across completed auctions
- [x] Admin panel backend (`FastAPI`) with dashboard and list pages
- [x] Docker Compose `admin` service on port `8080`
- [x] Token-protected admin access (`ADMIN_PANEL_TOKEN`)

## Sprint 8 Checklist (Telegram Auth + Timeline)

- [x] Telegram Login auth callback with signature verification
- [x] Session-cookie auth for admin panel (`la_admin_session`)
- [x] Auction dispute timeline page with bids/complaints/signals/mod-actions
- [x] Timeline links from complaints/signals/auctions tables

## Sprint 9 Checklist (Web Actions + RBAC)

- [x] Web actions: freeze/unfreeze/end auction
- [x] Web actions: remove bid, ban/unban user
- [x] Role-aware access in web admin (viewer/operator/owner)
- [x] Operator allowlist config (`ADMIN_OPERATOR_USER_IDS`)

## Sprint 10 Checklist (RBAC Scope Split)

- [x] Scope-based permission checks per web action
- [x] Operator role limited to auction/bid actions (no ban/role management)
- [x] User-management UI now hides forbidden actions by scope

## Sprint 11 Checklist (Telegram RBAC Sync)

- [x] Shared RBAC scope service used by web and Telegram
- [x] Scope checks for Telegram moderation commands and callbacks
- [x] Owner-scope bot command `/role` to grant/revoke/list moderator roles

## Sprint 12 Checklist (Web Security Hardening)

- [x] CSRF token validation for all web moderation POST actions
- [x] Confirm-step page for dangerous actions (end auction, remove bid, ban/unban)
- [x] Basic reason normalization/validation on web action endpoints

## Sprint 13 Checklist (Onboarding Analytics)

- [x] Soft-gate conversion metrics added to moderation dashboard service
- [x] Funnel KPIs exposed in web dashboard (users/private-start/hints/conversion)
- [x] `/modstats` extended with onboarding and conversion indicators

## Sprint 14 Checklist (Regression + CI)

- [x] Regression tests for allowlist RBAC scope matrix
- [x] Regression tests for CSRF protection and confirm-step behavior
- [x] GitHub Actions CI workflow to run test suite on push/PR

## Sprint 15 Checklist (Integration Regression)

- [x] Callback action -> required-scope mapping extracted to testable helpers
- [x] Regression tests for complaint/fraud callback scope mapping
- [x] Regression tests for web moderation actions refreshing auction posts

## Sprint 16 Checklist (Role Flow Regression)

- [x] Bot `/role` workflow tests (list/grant/validation branches)
- [x] Web role-management action tests (grant/revoke success/failure paths)
- [x] Permission-downgrade edge-case test for cookie auth after allowlist change

## Sprint 17 Checklist (DB Integration)

- [x] DB-backed integration tests for dynamic `user_roles` scope resolution
- [x] Grant/revoke propagation tests with real Postgres session
- [x] Dedicated GitHub Actions Postgres job for integration tests

## Sprint 18 Checklist (Callback E2E Integration)

- [x] Shared integration test fixtures moved to `tests/integration/conftest.py`
- [x] E2E tests for `modrep` callback flow (`freeze` + scope-denied path)
- [x] E2E tests for `modrisk` callback flow (`ban` -> DB updates + refresh + notify)

## Quick Start

1. Copy env template:

```bash
cp .env.example .env
```

2. Set real `BOT_TOKEN` in `.env`.

2.1 Set `BOT_USERNAME` (without @) to enable Telegram Login in web admin.

3. Ensure timezone is set (default: `Asia/Tashkent`).

4. Run services:

```bash
docker compose up -d --build
```

Admin panel will be available at `http://localhost:8080`.

5. Check logs:

```bash
docker compose logs -f bot
```

## Useful Commands

- Run migrations manually:

```bash
docker compose run --rm bot alembic upgrade head
```

- Create a new migration:

```bash
docker compose run --rm bot alembic revision --autogenerate -m "message"
```

- Run tests locally (same suite as CI):

```bash
python -m venv .venv
.venv/bin/pip install ".[dev]"
.venv/bin/python -m pytest -q tests
```

- Run lint locally (same command as CI):

```bash
.venv/bin/python -m ruff check app tests
```

- Run DB integration tests (use a dedicated test database):

```bash
RUN_INTEGRATION_TESTS=1 \
TEST_DATABASE_URL=postgresql+asyncpg://auction:auction@127.0.0.1:5432/auction_test \
.venv/bin/python -m pytest -q tests/integration
```

- Open moderation command list in bot private chat:

```text
/mod
```

- Manage moderator role from bot (requires `role:manage` scope):

```text
/role list <tg_user_id>
/role grant <tg_user_id> moderator
/role revoke <tg_user_id> moderator
```

- Open stateful moderation panel:

```text
/modpanel
```

- Extract custom emoji IDs for Bot API 9.4 button icons:

```text
/emojiid
```

Use it as a reply to a message that contains premium/custom emoji.

- Quick moderation stats:

```text
/modstats
```

- Include moderation queue destination in env (recommended):

```text
MODERATION_CHAT_ID=-100xxxxxxxxxx
MODERATION_THREAD_ID=12345
ADMIN_PANEL_TOKEN=change_me
ADMIN_WEB_SESSION_SECRET=change_me_session_secret
ADMIN_WEB_CSRF_TTL_SECONDS=7200
ADMIN_OPERATOR_USER_IDS=324897201,123456789
SOFT_GATE_REQUIRE_PRIVATE_START=true
SOFT_GATE_MODE=grace
SOFT_GATE_HINT_INTERVAL_HOURS=24
```

`SOFT_GATE_MODE` behavior:

- `strict` - block bid/buy/report until user opens bot private chat and presses `/start`
- `grace` - allow actions, but show onboarding prompt to open private chat (`/start`)
- `off` - disable gate logic entirely

`SOFT_GATE_HINT_INTERVAL_HOURS` controls how often the onboarding hint can be shown in `grace` mode (per user).

- Optional Bot API 9.4 button icons (custom emoji IDs):

```text
UI_EMOJI_CREATE_AUCTION_ID=5368324170671202286
UI_EMOJI_PUBLISH_ID=5368324170671202286
UI_EMOJI_BID_ID=5368324170671202286
UI_EMOJI_BUYOUT_ID=5368324170671202286
UI_EMOJI_REPORT_ID=5368324170671202286
UI_EMOJI_MOD_PANEL_ID=5368324170671202286
```

Custom emoji on buttons require Bot API 9.4 support in Telegram client and the bot owner's Premium-enabled custom emoji access.

- Fraud tuning (optional):

```text
FRAUD_DUOPOLY_WINDOW_SECONDS=300
FRAUD_DUOPOLY_MIN_TOTAL_BIDS=10
FRAUD_DUOPOLY_PAIR_RATIO=0.85
FRAUD_ALTERNATING_RECENT_BIDS=8
FRAUD_ALTERNATING_MIN_SWITCHES=6
FRAUD_BASELINE_WINDOW_SECONDS=3600
FRAUD_BASELINE_MIN_BIDS=6
FRAUD_BASELINE_SPIKE_FACTOR=4.0
FRAUD_BASELINE_MIN_INCREMENT=50
FRAUD_BASELINE_SPIKE_SCORE=25
FRAUD_HISTORICAL_COMPLETED_AUCTIONS=30
FRAUD_HISTORICAL_MIN_POINTS=25
FRAUD_HISTORICAL_SPIKE_FACTOR=3.0
FRAUD_HISTORICAL_MIN_INCREMENT=40
FRAUD_HISTORICAL_SPIKE_SCORE=20
FRAUD_HISTORICAL_START_RATIO_LOW=0.5
FRAUD_HISTORICAL_START_RATIO_HIGH=2.0
```

## Next (Sprint 19)

- Add scenario tests for complaint/risk queue message edits and moderation timeline consistency
- Run manual QA using `docs/manual-qa/sprint-19.md` and attach evidence in PR
