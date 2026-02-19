# Architecture Research

**Domain:** Telegram auction bot + FastAPI server-rendered admin panel (dashboard density + progressive disclosure)
**Researched:** 2026-02-19
**Confidence:** HIGH

## Standard Architecture

### System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                    Interaction Layer (FastAPI Admin)                │
├──────────────────────────────────────────────────────────────────────┤
│  Dashboard Page  List Pages  Detail Pages  Action Endpoints (POST) │
│  (GET /)         (/complaints, /signals, /manage/*, /actions/*)    │
│  + density mode  + filters     + disclosure blocks  + CSRF + RBAC  │
├──────────────────────────────────────────────────────────────────────┤
│                 View Composition + Presentation Policy               │
├──────────────────────────────────────────────────────────────────────┤
│  ViewModel builders  Section registry  Preset policy resolver       │
│  (what to show)      (which blocks)    (incident/routine/rewards)   │
├──────────────────────────────────────────────────────────────────────┤
│                 Shared Domain Services (existing)                    │
├──────────────────────────────────────────────────────────────────────┤
│ moderation_service  moderation_dashboard_service  points_service     │
│ appeal_service      trade_feedback_service       runtime_settings    │
├──────────────────────────────────────────────────────────────────────┤
│                         Persistence + Infra                          │
│             SQLAlchemy AsyncSession + Postgres + Redis               │
└──────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| `Admin route handlers` | HTTP parsing, auth, scope checks, PRG redirects | FastAPI `@app.get/@app.post` with `HTMLResponse`/`RedirectResponse` |
| `Admin view policy` | Decides density preset, default open/closed blocks, priority ordering | Pure Python policy module (no DB writes) |
| `Admin view builders` | Convert service snapshots into renderable section DTOs | Functions that map service outputs to card/table/disclosure models |
| `Domain command services` | Execute moderation actions and write audit log | Existing `moderation_service`, `appeal_service`, `trade_feedback_service` |
| `Domain query services` | Build dashboard and list snapshots | Existing `moderation_dashboard_service`, related query functions |
| `Feature flag/settings` | Controlled rollout and kill switches per feature | Existing runtime settings (`runtime_settings_service`) + allowlisted keys |

## Recommended Project Structure

```text
app/
├── web/
│   ├── main.py                          # app wiring + include routers
│   ├── routers/
│   │   ├── dashboard.py                 # GET pages and disclosure state
│   │   ├── moderation_actions.py        # POST write actions only
│   │   └── listings.py                  # complaints/signals/auctions/list filters
│   ├── viewmodels/
│   │   ├── dashboard_sections.py        # section DTOs for cards/tables/disclosures
│   │   └── density_presets.py           # incident/routine/rewards policy matrix
│   └── ui/
│       ├── components.py                # panel/kpi/table/details render helpers
│       └── page_shell.py                # shared shell/style/header wiring
├── services/
│   ├── moderation_service.py            # authoritative command side (existing)
│   ├── moderation_dashboard_service.py  # authoritative query side (existing)
│   └── runtime_settings_service.py      # rollout flags (existing)
└── db/
    ├── models.py                        # existing domain and runtime settings models
    └── session.py                       # AsyncSession factory
```

### Structure Rationale

- **`web/routers/`**: separates read pages from mutation endpoints so UX changes cannot accidentally alter moderation command semantics.
- **`web/viewmodels/`**: isolates dashboard-density logic from HTTP and domain services, enabling safe iterative redesign with stable data contracts.
- **`web/ui/`**: keeps progressive-disclosure rendering primitives reusable across dashboard and detail screens.
- **`services/` remains source of truth**: moderation actions and audit logging stay where they are; UI only orchestrates.

## Architectural Patterns

### Pattern 1: Command-Query Split at Admin Boundary

**What:** Keep all irreversible moderation actions in command services; keep dashboard and listings as query-only paths.
**When to use:** Always for moderation, bans, bid removals, appeal state transitions.
**Trade-offs:** Slightly more modules, but dramatically lower regression risk.

**Example:**
```python
# route layer (write endpoint)
@router.post("/actions/auction/freeze")
async def freeze_action(...):
    async with SessionFactory() as session:
        async with session.begin():
            result = await freeze_auction(session, actor_user_id=actor_id, auction_id=auction_id, reason=reason)
    return RedirectResponse(return_to, status_code=303)
```

### Pattern 2: Preset Policy Matrix (Density as Configuration)

**What:** Express progressive-disclosure behavior as declarative policy (which sections are visible/expanded per preset).
**When to use:** Dashboard-density, operator-specific workflows, gradual rollout.
**Trade-offs:** Extra mapping layer, but no branching explosion in page assembly.

**Example:**
```python
PRESET_POLICY = {
    "incident": {"show_onboarding": False, "show_rewards_policy": False},
    "routine": {"show_onboarding": True, "show_activity": True},
    "rewards": {"show_rewards_weekly": True, "show_rewards_24h": True, "show_rewards_policy": True},
}
```

### Pattern 3: PRG + CSRF + Explicit Redirect Target

**What:** Every POST action validates CSRF and redirects to a GET page (`303`) with safe `return_to`.
**When to use:** All admin mutations to avoid duplicate writes and refresh-resubmits.
**Trade-offs:** Requires route discipline, but prevents accidental repeated moderation actions.

## Data Flow

### Request Flow (Dashboard Read)

```text
[Operator opens /?preset=routine]
    ↓
[FastAPI dashboard route]
    ↓ auth/scope context
[get_moderation_dashboard_snapshot(session)]
    ↓
[Density preset resolver + section viewmodel builder]
    ↓
[HTML render helpers: cards + <details> blocks]
    ↓
[HTMLResponse]
```

### Request Flow (Moderation Write)

```text
[Operator submits POST /actions/*]
    ↓
[FastAPI action route]
    ↓ CSRF + scope gate + input parsing
[AsyncSession transaction begin]
    ↓
[domain command service: freeze/remove/ban/...]
    ↓ writes + log_moderation_action
[commit]
    ↓
[RedirectResponse 303 -> GET page]
```

### State Management

```text
Persistent state:
- Postgres: domain entities, moderation log, runtime setting overrides

Ephemeral state:
- Query params: preset/filter/page
- sessionStorage: last selected dashboard preset (non-authoritative)

Authoritative rule:
- UI state NEVER substitutes domain truth for moderation decisions
```

### Key Data Flows

1. **Density selection flow:** query param `preset` -> normalization -> policy map -> disclosure open state.
2. **Moderation safety flow:** POST form -> CSRF/scope validation -> command service -> moderation log -> redirect.
3. **Rollout flow:** runtime settings override -> policy gates -> selective section visibility without changing action handlers.

## Build Order and Dependencies

1. **Extract presentation policy first (no behavior change)**
   - Create `density_presets.py` and `dashboard_sections.py` from current inline logic.
   - Keep generated HTML byte-for-byte equivalent where possible.
   - Dependency: none (pure refactor).

2. **Stabilize component boundaries (read routes vs write routes)**
   - Move `/actions/*` into dedicated router module but keep service calls unchanged.
   - Add contract tests around `log_moderation_action` side effects for key actions.
   - Dependency: step 1 complete so UI extraction is isolated from action movement.

3. **Introduce feature-flagged progressive disclosure enhancements**
   - Add runtime setting keys for advanced density behavior (for example, default preset, section ordering).
   - Keep existing presets as fallback defaults.
   - Dependency: step 1 policy layer present.

4. **Incremental UX enrichment on read-only surfaces**
   - Apply disclosure groups and summary compaction to dashboard/list pages first.
   - Do not alter moderation form schemas in this phase.
   - Dependency: step 2 safety boundaries established.

5. **Action-surface hardening and rollout checks**
   - Add smoke tests for top moderation actions in each density mode.
   - Ship behind runtime toggle, then enable for owner/admin cohorts.
   - Dependency: prior steps complete.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 0-1k operators/users | Current modular monolith is sufficient; prioritize clarity and auditability over micro-optimizations. |
| 1k-100k users | Cache heavy dashboard aggregates (short TTL) and precompute expensive counters; keep command paths uncached. |
| 100k+ users | Split admin read-model generation from write model (materialized read model / async refresh), but keep write actions in core service boundary. |

### Scaling Priorities

1. **First bottleneck:** dashboard aggregate query volume; mitigate with cached query snapshots, not by bypassing services.
2. **Second bottleneck:** large table rendering and page weight; mitigate with server pagination and stricter progressive disclosure defaults.

## Anti-Patterns

### Anti-Pattern 1: Mixing density logic into moderation command code

**What people do:** Add UI preset checks inside `freeze_auction`/`ban_user`/`remove_bid` paths.
**Why it's wrong:** Couples UX experiments to safety-critical behavior and risks action regressions.
**Do this instead:** Keep presets in viewmodel/policy layer only; command services remain preset-agnostic.

### Anti-Pattern 2: Client-side-only state as source of truth

**What people do:** Rely on browser storage for action context or effective permissions.
**Why it's wrong:** Non-authoritative state can drift and cause unsafe assumptions.
**Do this instead:** Treat query/sessionStorage as display preferences only; re-check auth/scope/CSRF on every POST.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Telegram Bot API | Side-effect notifications after moderation commands | Keep asynchronous and failure-tolerant; do not block command commit on notification delivery. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `web/routers` -> `services` | Direct async function calls with `AsyncSession` | Keep transaction owned by route boundary (`session.begin`) for clear commit semantics. |
| `web/viewmodels` -> `services` | Read-only snapshot composition | No writes in viewmodel builders. |
| `web/routers/actions` -> `runtime_settings_service` | Feature-flag reads only | Use allowlisted keys; unknown keys fail closed. |

## Sources

- FastAPI docs: Bigger Applications (`APIRouter`, dependency layering) — https://fastapi.tiangolo.com/tutorial/bigger-applications/ (official, current page accessed 2026-02-19, HIGH)
- FastAPI docs: Custom Response (`HTMLResponse`, `RedirectResponse` default 307, explicit redirect behavior) — https://fastapi.tiangolo.com/advanced/custom-response/#redirectresponse (official, accessed 2026-02-19, HIGH)
- SQLAlchemy async docs (`AsyncSession.begin`, transaction patterns, async ORM guidance) — https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html (official, 2.1 beta docs, accessed 2026-02-19, HIGH)
- Starlette docs (`SessionMiddleware`, `request.state`, templates) via Context7 `/encode/starlette` (official upstream snippets, HIGH)
- MDN `<details>` reference (progressive disclosure semantics; `open`, `toggle`, grouped `name`) — https://developer.mozilla.org/en-US/docs/Web/HTML/Element/details (last modified 2025-10-13, MEDIUM)
- W3C WAI-ARIA APG Disclosure Pattern (keyboard and ARIA guidance) — https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/ (official standards guidance, HIGH)
- Repository evidence:
  - `app/web/main.py` (current inline rendering, preset toolbar, disclosure blocks, PRG-style action redirects)
  - `app/services/moderation_service.py` (authoritative moderation commands + audit logging)
  - `app/services/moderation_dashboard_service.py` (dashboard query snapshot)
  - `app/services/runtime_settings_service.py` (allowlisted runtime feature control)
  - `tests/test_web_dashboard_presets.py` (preset behavior locked by tests)

---
*Architecture research for: Telegram auction bot + admin web panel*
*Researched: 2026-02-19*
