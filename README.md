# LiteAuction Bot

MVP Telegram auction bot scaffold on `aiogram` + `PostgreSQL` + `Redis` with Docker Compose.

For agent-driven sessions and planning workflow, start with `AGENTS.md` and `planning/STATUS.md`.

This repository currently contains **Sprint 0 + Sprint 1 + Sprint 2 + Sprint 3 + Sprint 4 + Sprint 5 + Sprint 6 + Sprint 7 + Sprint 8 + Sprint 9 + Sprint 10 + Sprint 11 + Sprint 12 + Sprint 13 + Sprint 14 + Sprint 15 + Sprint 16 + Sprint 17 + Sprint 18 + Sprint 19 + Sprint 20 + Sprint 21 + Sprint 22 + Sprint 23 + Sprint 24 + Sprint 25 + Sprint 26 + Sprint 27 + Sprint 28 + Sprint 29 + Sprint 30**:

- Dockerized runtime (`bot`, `db`, `redis`)
- `Alembic` migrations and initial PostgreSQL schema
- Async SQLAlchemy setup
- Basic bot startup with `/start`
- Startup and container health checks for DB/Redis
- FSM lot creation via private chat (and optional channel DM topics) using `/newauction`
- Inline auction publishing via `auc_<id>` and `chosen_inline_result`
- High-risk publish gate requiring assigned guarantor for risky sellers
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
- Risk/trust indicators on admin user, auction, appeal, and signal views
- Post-trade feedback foundation with bot intake and admin moderation view
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
- Queue message edit and timeline consistency regression coverage for moderation callbacks
- Timeline event sequence guards for callback flows (`create -> moderation action -> resolve`)
- Stable per-entity timeline ordering on equal timestamps (`created_at` + primary-key tie-breakers)
- Web timeline pagination with configurable `page`/`limit` and navigation links
- CI anti-flaky integration re-run and PR quality checklist template
- DB-aware timeline page assembly and source filters (`auction`, `bid`, `complaint`, `fraud`, `moderation`)
- Bug triage foundation: policy, backlog template, and GitHub bug issue form
- Bugfix wave 1 improvements for timeline navigation context and pagination safety
- Bugfix wave 2 improvements for callback retry safety and denied-scope back navigation
- Visual foundation refresh for admin web layout, controls, and responsive readability
- Final visual polish and release-readiness checklist with consolidated QA evidence template
- Section-based moderation topic routing and user feedback/guarantor intake commands (`/bug`, `/suggest`, `/guarant`, `/boostfeedback`, `/boostguarant`, `/boostappeal`)
- Rewards ledger foundation with idempotent points accrual, advanced `/points`, moderator `/modpoints` + `/modpoints_history`, and admin user-page rewards widget
- Outbox-driven automation for approved feedback -> GitHub issue creation with retry/backoff
- Full private-chat topic routing for bot DM (`Лоты`, `Поддержка`, `Баллы`, `Сделки`, `Модерация`) with strict command/topic enforcement and `/topics`
- Channel DM lot intake foundation (Bot API 9.2) via `direct_messages_topic_id` for `/newauction`
- Suggested post moderation pipeline for channel DM topics (approve/decline + persisted review audit)
- Draft-stream progress hints (`sendMessageDraft`) for long-running bot actions like `/modstats` and auction finalize
- Moderation checklists for complaints, guarantor requests, and appeals with audit-logged checklist toggles
- Task-scoped checklist replies in moderation flows with actor/timestamp audit trail
- Seller/chat verification workflow (`verifyUser`/`verifyChat`) with scope-gated operator commands and trust-aware risk integration
- Sprint planning automation via TOML manifests + GitHub issue/draft-PR sync + PR policy gate (`Closes #...` + `sprint:*` label)

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

## Sprint 19 Checklist (Queue + Timeline Consistency)

- [x] Scenario checks for complaint/fraud queue message edit behavior after callback actions
- [x] Timeline consistency checks for complaint/fraud lifecycle after moderation callbacks
- [x] Regression guard for repeated callback clicks (idempotent behavior)
- [x] `BAN_USER` callback logs linked to auction timeline (`auction_id` in moderation log)

## Sprint 20 Checklist (Timeline Sequence Guards)

- [x] Integration helper to assert ordered timeline subsequences in callback scenarios
- [x] Sequence checks for complaint freeze and fraud ban callback paths
- [x] Sequence checks for repeated callback idempotency path
- [x] Manual QA expected order synced with callback event ordering

## Sprint 21 Checklist (Same-Timestamp Ordering)

- [x] Deterministic tie-break rules for timeline events sharing the same timestamp
- [x] Integration regressions for complaint/fraud ordering with same `happened_at`

## Sprint 22 Checklist (Per-Entity Stable Order)

- [x] Added primary-key tie-breakers to timeline source queries (`created_at`, then `id`)
- [x] Removed fragile string-based final tie-breakers from timeline sorting
- [x] Added regressions for multiple complaints/signals with identical timestamps to enforce numeric id ordering

## Sprint 23 Checklist (Timeline Pagination)

- [x] Added `page` and `limit` query parameters to auction timeline admin endpoint
- [x] Added timeline page navigation links and visible page coverage counters
- [x] Added validation for pagination bounds (`page >= 0`, `1 <= limit <= 500`)
- [x] Added regression tests for page boundaries and event ordering preservation

## Sprint 24 Checklist (Quality Gates)

- [x] Added PR template with validation/self-review/risk/manual-QA checklist
- [x] Added CI anti-flaky re-run for integration DB suite on pull requests
- [x] Standardized reviewer focus section in PR metadata

## Sprint 25 Checklist (Timeline Source Filters)

- [x] Added source filter support to timeline endpoint (`source=auction,bid,...`)
- [x] Moved timeline pagination closer to DB layer via per-source bounded fetch (`(page+1)*limit`)
- [x] Added regression tests for source filtering and page boundaries in web/controller and integration layers

## Sprint 26 Checklist (Debug/Triage Foundation)

- [x] Added bug triage policy and bugfix Definition of Done checklist
- [x] Added prioritized bug backlog template for Sprint 27 candidate fixes
- [x] Added GitHub bug report issue template with required reproduction fields

## Sprint 27 Checklist (Bugfix Wave 1)

- [x] Fixed timeline context retention across timeline/manage navigation (`page`, `limit`, `source`)
- [x] Fixed timeline page builder to cap fetch volume and return early for out-of-range pages
- [x] Normalized and deduplicated source filter input; blank source now consistently maps to `all`
- [x] Added regression tests for navigation context and pagination boundary behavior

## Sprint 28 Checklist (Bugfix Wave 2)

- [x] Added idempotency regression for repeated `modrisk:ban` callback actions
- [x] Fixed denied-scope web pages to preserve safe return navigation context
- [x] Added unit and integration regressions for scope-denied back links and callback retry side effects

## Sprint 29 Checklist (Visual Foundation)

- [x] Refreshed global admin web style system with CSS variables and consistent spacing/typography
- [x] Improved readability of tables/cards/forms/buttons without changing backend behavior
- [x] Added responsive layout handling for mobile viewports in core admin pages
- [x] Updated timeline source quick-links to chip-style controls for clearer filtering affordance

## Sprint 30 Checklist (Final Polish + Release Readiness)

- [x] Added focus-visible keyboard affordances for interactive controls in admin web
- [x] Unified visual treatment for empty/error/warning states across key pages
- [x] Added release-readiness checklist at `docs/release/sprint-30-readiness.md`
- [x] Kept backend behavior unchanged while polishing UX and recovery paths

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
docker compose exec -T db psql -U auction -d postgres -c "CREATE DATABASE auction_test OWNER auction;" || true
DB_IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' liteauction-db)
RUN_INTEGRATION_TESTS=1 \
TEST_DATABASE_URL="postgresql+asyncpg://auction:auction@${DB_IP}:5432/auction_test" \
.venv/bin/python -m pytest -q tests/integration
```

Integration tests refuse to run unless `TEST_DATABASE_URL` is set and points to a database name containing `test`.

- Sync sprint plan to GitHub issues/milestone:

```bash
cp planning/sprints/sprint-template.toml planning/sprints/sprint-33.toml
python scripts/sprint_sync.py --manifest planning/sprints/sprint-33.toml
```

- Create draft PR scaffolds for each sprint task:

```bash
python scripts/sprint_sync.py --manifest planning/sprints/sprint-33.toml --create-draft-prs
```

This sync also updates `planning/STATUS.md` so recovery after context loss stays deterministic.

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

- View or adjust user reward balance from bot (requires `role:manage` scope):

```text
/modpoints <tg_user_id>
/modpoints <tg_user_id> <limit>
/modpoints <tg_user_id> <amount> <reason>
/modpoints_history <tg_user_id> [page] [all|feedback|manual|boost|gboost|aboost]
```

- Adjust user reward balance from admin web (requires `role:manage` scope):

```text
/manage/users -> open user -> Rewards / points -> amount (+/-), reason -> Применить
```

The web action uses an idempotent key per form submit (`action_id`) to prevent duplicate ledger writes on repeated submit.

- Open stateful moderation panel:

```text
/modpanel
/botphoto list
/botphoto set <preset>
/botphoto reset
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

- Send user feedback from private chat:

```text
/bug <описание>
/suggest <предложение>
/guarant <запрос на гаранта>
/boostfeedback <feedback_id>
/boostguarant <request_id>
/boostappeal <appeal_id>
/tradefeedback <auction_id> <1..5> [комментарий]
/points
/points <1..20>
/topics
```

High-risk sellers cannot publish drafts until a guarantor request is assigned by moderation.

Trade feedback moderation list is available in admin web: `/trade-feedback` (status/rating/actor filters).

Current points redemption guardrails:

- Global kill-switch for all redemptions.
- Global cooldown between redemptions.
- Global redemption count caps: daily, weekly.
- Global redemption spend caps: daily, weekly, monthly.
- Global account gates: minimum retained balance, minimum account age, minimum earned points.
- Policy visibility in `/points`, `/modstats`, dashboard, and `/manage/user/{id}`.

- Include moderation queue destination in env (recommended):

```text
MODERATION_CHAT_ID=-100xxxxxxxxxx
MODERATION_THREAD_ID=12345
MODERATION_TOPIC_COMPLAINTS_ID=12345
MODERATION_TOPIC_BUGS_ID=12346
MODERATION_TOPIC_SUGGESTIONS_ID=12347
MODERATION_TOPIC_GUARANTORS_ID=12348
MODERATION_TOPIC_APPEALS_ID=12349
MODERATION_TOPIC_AUCTIONS_ACTIVE_ID=12350
MODERATION_TOPIC_AUCTIONS_FROZEN_ID=12351
MODERATION_TOPIC_AUCTIONS_CLOSED_ID=12352
ADMIN_PANEL_TOKEN=change_me
ADMIN_WEB_SESSION_SECRET=change_me_session_secret
ADMIN_WEB_CSRF_TTL_SECONDS=7200
ADMIN_OPERATOR_USER_IDS=324897201,123456789
SOFT_GATE_REQUIRE_PRIVATE_START=true
SOFT_GATE_MODE=grace
SOFT_GATE_HINT_INTERVAL_HOURS=24
FEEDBACK_INTAKE_MIN_LENGTH=10
FEEDBACK_INTAKE_COOLDOWN_SECONDS=90
FEEDBACK_BUG_REWARD_POINTS=30
FEEDBACK_SUGGESTION_REWARD_POINTS=20
FEEDBACK_PRIORITY_BOOST_ENABLED=true
FEEDBACK_PRIORITY_BOOST_COST_POINTS=25
FEEDBACK_PRIORITY_BOOST_DAILY_LIMIT=2
FEEDBACK_PRIORITY_BOOST_COOLDOWN_SECONDS=0
POINTS_REDEMPTION_ENABLED=true
POINTS_REDEMPTION_COOLDOWN_SECONDS=60
POINTS_REDEMPTION_DAILY_LIMIT=0
POINTS_REDEMPTION_WEEKLY_LIMIT=0
POINTS_REDEMPTION_DAILY_SPEND_CAP=0
POINTS_REDEMPTION_WEEKLY_SPEND_CAP=0
POINTS_REDEMPTION_MONTHLY_SPEND_CAP=0
POINTS_REDEMPTION_MIN_BALANCE=0
POINTS_REDEMPTION_MIN_ACCOUNT_AGE_SECONDS=0
POINTS_REDEMPTION_MIN_EARNED_POINTS=0
APPEAL_PRIORITY_BOOST_ENABLED=true
APPEAL_PRIORITY_BOOST_COST_POINTS=20
APPEAL_PRIORITY_BOOST_DAILY_LIMIT=1
APPEAL_PRIORITY_BOOST_COOLDOWN_SECONDS=0
GUARANTOR_INTAKE_MIN_LENGTH=10
GUARANTOR_INTAKE_COOLDOWN_SECONDS=180
GUARANTOR_PRIORITY_BOOST_ENABLED=true
GUARANTOR_PRIORITY_BOOST_COST_POINTS=40
GUARANTOR_PRIORITY_BOOST_DAILY_LIMIT=1
GUARANTOR_PRIORITY_BOOST_COOLDOWN_SECONDS=0
PUBLISH_HIGH_RISK_REQUIRES_GUARANTOR=true
PUBLISH_GUARANTOR_ASSIGNMENT_MAX_AGE_DAYS=30
GITHUB_AUTOMATION_ENABLED=true
GITHUB_TOKEN=ghp_xxx
GITHUB_REPO_OWNER=Nombah501
GITHUB_REPO_NAME=LiteAuction
OUTBOX_WATCHER_INTERVAL_SECONDS=20
OUTBOX_BATCH_SIZE=20
OUTBOX_MAX_ATTEMPTS=5
OUTBOX_RETRY_BASE_SECONDS=30
OUTBOX_RETRY_MAX_SECONDS=1800
FEEDBACK_GITHUB_ACTOR_TG_USER_ID=-998
```

Topic-specific IDs are optional; when unset the bot falls back to `MODERATION_THREAD_ID`.

`SOFT_GATE_MODE` behavior:

- `strict` - block bid/buy/report until user opens bot private chat and presses `/start`
- `grace` - allow actions, but show onboarding prompt to open private chat (`/start`)
- `off` - disable gate logic entirely

`SOFT_GATE_HINT_INTERVAL_HOURS` controls how often the onboarding hint can be shown in `grace` mode (per user).

Private DM topics (Bot API 9.3/9.4):

- `PRIVATE_TOPICS_ENABLED` - enables personal topic routing in bot private chat.
- `PRIVATE_TOPICS_STRICT_ROUTING` - when enabled, commands are processed only in their assigned topic.
- `PRIVATE_TOPICS_AUTOCREATE_ON_START` - bootstrap all personal topics on `/start`.
- `PRIVATE_TOPICS_USER_TOPIC_POLICY` - controls topic mutation mode: `auto` (BotFather policy), `allow`, or `block`.
- `PRIVATE_TOPIC_TITLE_*` - topic names for `auctions/support/points/trades/moderation`.
- When Telegram reports `has_topics_enabled=false` for a user, bot falls back to regular private-chat flow.

Examples:

```text
PRIVATE_TOPICS_ENABLED=true
PRIVATE_TOPICS_STRICT_ROUTING=true
PRIVATE_TOPICS_AUTOCREATE_ON_START=true
PRIVATE_TOPICS_USER_TOPIC_POLICY=auto
PRIVATE_TOPIC_TITLE_AUCTIONS=Лоты
PRIVATE_TOPIC_TITLE_SUPPORT=Поддержка
PRIVATE_TOPIC_TITLE_POINTS=Баллы
PRIVATE_TOPIC_TITLE_TRADES=Сделки
PRIVATE_TOPIC_TITLE_MODERATION=Модерация
BOT_PROFILE_PHOTO_PRESETS=default=AgACAgIAAxk...,campaign=AgACAgIAAxk...
BOT_PROFILE_PHOTO_DEFAULT_PRESET=default
AUCTION_MESSAGE_EFFECTS_ENABLED=false
AUCTION_EFFECT_OUTBID_ID=
AUCTION_EFFECT_BUYOUT_SELLER_ID=
AUCTION_EFFECT_BUYOUT_WINNER_ID=
AUCTION_EFFECT_ENDED_SELLER_ID=
AUCTION_EFFECT_ENDED_WINNER_ID=
```

Bot profile photo presets (`/botphoto` command for operators with `auction:manage`):

- `BOT_PROFILE_PHOTO_PRESETS` - comma-separated `preset=file_id` map used by `/botphoto set <preset>`.
- `BOT_PROFILE_PHOTO_DEFAULT_PRESET` - preset name used by `/botphoto reset`; if missing, reset falls back to `removeMyProfilePhoto`.
- Successful set/reset actions are written to moderation audit log.

Auction message effects for critical auction notifications:

- `AUCTION_MESSAGE_EFFECTS_ENABLED` - global kill-switch for all auction effect usage.
- `AUCTION_EFFECT_*_ID` - per-event effect IDs (`outbid`, `buyout seller/winner`, `ended seller/winner`).
- If effect delivery is rejected by Telegram (unsupported effect/client/chat), bot retries the same text notification without `message_effect_id`.

Channel DM lot intake (Bot API 9.2):

- `CHANNEL_DM_INTAKE_ENABLED` - enables `/newauction` intake in channel DM topics.
- `CHANNEL_DM_INTAKE_CHAT_ID` - optional chat allowlist; `0` allows any direct-messages chat.
- Incoming `suggested_post_info` events from enabled channel DM chats are routed to moderation with approve/decline actions.
- `chat_owner_changed` / `chat_owner_left` service events from monitored channel DM chats are saved to audit and pause auto-processing until operator confirmation (`/confirmowner <chat_id>`).

Examples:

```text
CHANNEL_DM_INTAKE_ENABLED=true
CHANNEL_DM_INTAKE_CHAT_ID=-1001234567890
```

Message drafts (Bot API 9.3):

- `MESSAGE_DRAFTS_ENABLED` - enables draft progress hints in long-running command paths.

Example:

```text
MESSAGE_DRAFTS_ENABLED=true
```

Verification workflow (Bot API verify/remove verification):

- Scope `trust:manage` can run moderation commands:
  - `/verifyuser <tg_user_id> [description]`
  - `/unverifyuser <tg_user_id>`
  - `/verifychat <chat_id> [description]`
  - `/unverifychat <chat_id>`
- User verification state is shown in web `/manage/users` and `/manage/user/<id>` surfaces.
- Risk/trust scoring consumes verification in a conservative way (bonus applies only when there is no active blacklist and no open fraud signals).

- Optional Bot API 9.4 button icons (custom emoji IDs):

```text
UI_EMOJI_CREATE_AUCTION_ID=5368324170671202286
UI_EMOJI_PUBLISH_ID=5368324170671202286
UI_EMOJI_BID_ID=5368324170671202286
UI_EMOJI_BID_X1_ID=5368324170671202286
UI_EMOJI_BID_X3_ID=5368324170671202286
UI_EMOJI_BID_X5_ID=5368324170671202286
UI_EMOJI_BUYOUT_ID=5368324170671202286
UI_EMOJI_REPORT_ID=5368324170671202286
UI_EMOJI_COPY_PUBLISH_ID=5368324170671202286
UI_EMOJI_GALLERY_ID=5368324170671202286
UI_EMOJI_NEW_LOT_ID=5368324170671202286
UI_EMOJI_PHOTOS_DONE_ID=5368324170671202286
UI_EMOJI_MOD_PANEL_ID=5368324170671202286
UI_EMOJI_MOD_COMPLAINTS_ID=5368324170671202286
UI_EMOJI_MOD_SIGNALS_ID=5368324170671202286
UI_EMOJI_MOD_FROZEN_ID=5368324170671202286
UI_EMOJI_MOD_APPEALS_ID=5368324170671202286
UI_EMOJI_MOD_STATS_ID=5368324170671202286
UI_EMOJI_MOD_REFRESH_ID=5368324170671202286
UI_EMOJI_MOD_FREEZE_ID=5368324170671202286
UI_EMOJI_MOD_UNFREEZE_ID=5368324170671202286
UI_EMOJI_MOD_REMOVE_TOP_ID=5368324170671202286
UI_EMOJI_MOD_BAN_ID=5368324170671202286
UI_EMOJI_MOD_IGNORE_ID=5368324170671202286
UI_EMOJI_MOD_TAKE_ID=5368324170671202286
UI_EMOJI_MOD_APPROVE_ID=5368324170671202286
UI_EMOJI_MOD_REJECT_ID=5368324170671202286
UI_EMOJI_MOD_ASSIGN_GUARANTOR_ID=5368324170671202286
UI_EMOJI_MOD_BACK_ID=5368324170671202286
UI_EMOJI_MOD_MENU_ID=5368324170671202286
UI_EMOJI_MOD_PREV_ID=5368324170671202286
UI_EMOJI_MOD_NEXT_ID=5368324170671202286
```

Granular keys are optional: when they are empty, the bot falls back to base keys (`UI_EMOJI_BID_ID`, `UI_EMOJI_PUBLISH_ID`, `UI_EMOJI_CREATE_AUCTION_ID`, `UI_EMOJI_MOD_PANEL_ID`).

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

## Next (Post-Sprint)

- Continue point improvements from bug triage backlog in small scoped PRs
- Run consolidated manual QA using `docs/manual-qa/sprint-19.md` + `docs/release/sprint-30-readiness.md`
- Use `docs/release/rc-1-notes.md` as release-candidate baseline notes
- Fill `docs/release/rc-1-manual-qa-matrix.md` during final interactive QA run
- Prepare release candidate notes and known limitations
