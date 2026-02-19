# Stack Research

**Domain:** Telegram auction operations admin UX (density + progressive disclosure)
**Researched:** 2026-02-19
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| FastAPI | 0.129.x | Server-rendered admin routes, RBAC/CSRF enforcement, partial endpoints | You already run FastAPI; keep the trust boundary and moderation logic server-side. FastAPI docs + current ecosystem support Jinja2 templates and StaticFiles cleanly, so you can ship UX changes without a SPA rewrite. | HIGH |
| Jinja2 (via `fastapi.templating`) | 3.1.6 | Template composition for dense layouts and reusable disclosure components | Replaces monolithic inline HTML strings with reusable templates/partials, reducing regression risk while preserving existing security checks and Python-side rendering control. | HIGH |
| htmx | 2.0.8 | Progressive enhancement for partial updates, sortable/filterable dense tables, drill-down panes | htmx 2.x is now the standard "HTML-over-the-wire" choice for incremental admin UX upgrades: no API-contract explosion, no heavy frontend runtime, graceful degradation with `hx-boost`. | HIGH |
| Alpine.js | 3.15.8 | Small client-side state for UI-only behavior (focus mode, disclosure toggles) | Use Alpine only for local interaction state; this keeps business rules and authorization in FastAPI while giving operators snappy interactions. | HIGH |
| Playwright (Python) + pytest plugin | Playwright 1.58.x, pytest-playwright 0.7.2 | Browser-level regression tests for density presets, disclosure states, and RBAC/CSRF flows | Strongest path for safer UX rollout: role/text locator strategy, auto-waiting assertions, and trace artifacts reduce flaky tests and catch interaction regressions that unit tests miss. | HIGH |

### Supporting Libraries

| Library | Version | Purpose | When to Use | Confidence |
|---------|---------|---------|-------------|------------|
| `idiomorph` (htmx extension) | 0.7.4 | DOM morphing to preserve focus/scroll when updating dense panels | Use for row expansion, KPI blocks, and hot-path panel refreshes where replacing nodes causes focus loss or jank. | HIGH |
| `@alpinejs/persist` | 3.15.8 | Persist operator UI prefs (`density`, `preset`, disclosure defaults) | Use for per-operator presentation preferences only; do not persist security-sensitive state. | HIGH |
| `pytest-xdist` | 3.8.0 | Parallel browser test execution in CI | Use when Playwright suite grows and slows PR checks; pair with deterministic test data fixtures. | MEDIUM |

### Development Tools

| Tool | Purpose | Notes | Confidence |
|------|---------|-------|------------|
| `npm` (asset pinning) | Lock exact frontend asset versions | Prefer vendored/pinned local assets over runtime CDN loads for admin reliability and CSP control. | HIGH |
| `uvicorn` + existing pytest stack | Fast local iteration on UI + test feedback | Keep existing Python workflow; add browser tests as a dedicated marker/stage instead of replacing current test layers. | HIGH |

## Installation

```bash
# Python (core/testing)
pip install "fastapi>=0.129,<1" "jinja2>=3.1.6,<4" "playwright>=1.58,<2" "pytest-playwright>=0.7.2,<0.8" "pytest-xdist>=3.8,<4"
python -m playwright install --with-deps chromium

# Frontend assets (pinned)
npm install --save-exact htmx.org@2.0.8 alpinejs@3.15.8 @alpinejs/persist@3.15.8 idiomorph@0.7.4
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastAPI + Jinja2 + htmx | React/Next.js SPA admin | Use only if you need highly stateful client workflows (complex offline, rich drag/drop, real-time collab). For this milestone, SPA migration adds risk and slows moderation throughput. |
| Alpine.js for micro-state | Custom vanilla JS scattered across pages | Use vanilla only for one-off scripts. For repeated disclosure/preset patterns, Alpine gives consistency with minimal overhead. |
| Playwright + pytest plugin | Selenium/WebDriver stack | Use Selenium only if organization-wide standard mandates it. Playwright has better auto-waiting and locator ergonomics for fast-moving admin UIs. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Full SPA rewrite for this UX tranche | Rebuild risk is high, duplicates backend validation paths, and delays operator-facing wins | Incremental server-rendered enhancement with htmx + Alpine |
| Unpinned CDN scripts in admin production | Adds supply-chain and availability risk, weakens deterministic deploys and CSP posture | Pin and vendor JS assets in your static bundle |
| jQuery-first admin plugins as new foundation | Increases legacy dependency surface and fights current Python-first rendering model | htmx partials + semantic HTML tables + targeted Alpine state |
| CSS/XPath-only end-to-end selectors | High test flake when dense layout shifts | Playwright role/text/test-id locators |

## Stack Patterns by Variant

**If you are improving existing dense pages with minimal refactor:**
- Use FastAPI route returning full HTML on normal requests and partial HTML when `HX-Request: true`.
- Because this preserves deep-link/shareability and progressive enhancement while reducing payload size.

**If you are adding operator preference defaults (focus mode, density presets):**
- Use Alpine store + `@alpinejs/persist` for client preference memory; mirror canonical defaults in server-side runtime settings.
- Because UX preference should feel instant locally, but policy/defaults must remain auditable and centrally controllable.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| `fastapi>=0.129,<1` | `jinja2>=3.1,<4` | Official FastAPI template flow uses Jinja2 via Starlette templating. |
| `htmx.org@2.0.8` | `idiomorph@0.7.4` | Idiomorph extension is explicitly documented for htmx morph swaps. |
| `playwright>=1.58,<2` | `pytest-playwright>=0.7.2,<0.8` | Plugin docs and PyPI metadata align on current pytest-runner workflow. |

## Implementation Approach (Safe Rollout)

1. **Refactor rendering surface first**: move inline dashboard HTML into templates + partials (`base`, `kpi_grid`, `table`, `disclosure_panel`) with no behavior change.
2. **Introduce htmx incrementally**: start with non-destructive GET interactions (preset switch, filter/sort, disclosure content fetch), then POST actions with existing CSRF hidden inputs and header tokens.
3. **Add micro-state only**: keep Alpine limited to UI concerns (open/closed, remembered density), never RBAC decisions or moderation actions.
4. **Protect security posture**: preserve server-side RBAC checks per route/action; keep CSRF verification authoritative on backend; for htmx, include CSRF token through form hidden fields and/or scoped `hx-headers`.
5. **Harden tests before broad rollout**: add Playwright flows for (a) role-based visibility, (b) CSRF failure handling, (c) density preset persistence, (d) keyboard/focus behavior in progressive disclosure.
6. **Release behind runtime flag**: gate new density mode/disclosure defaults with runtime setting, canary to owner/mod group, then expand after metrics look stable.

## Sources

- Context7: `/fastapi/fastapi/0.128.0` — templates/static files, HTML responses, TestClient usage (HIGH)
- Context7: `/bigskysoftware/htmx/v2.0.4` — htmx history/progressive patterns, morphing support references (HIGH)
- Context7: `/websites/playwright_dev_python` — pytest plugin workflow, locator/assertion guidance (HIGH)
- FastAPI templates docs: https://fastapi.tiangolo.com/advanced/templates/ — Jinja2Templates usage and static integration (HIGH)
- htmx docs: https://htmx.org/docs/ — `hx-boost` progressive enhancement, history, security/CSRF notes (HIGH)
- htmx idiomorph extension: https://htmx.org/extensions/idiomorph/ — morph swap behavior and setup (HIGH)
- Alpine docs: https://alpinejs.dev/start-here and https://alpinejs.dev/plugins/persist — lightweight state and persistence model (HIGH)
- Playwright Python docs: https://playwright.dev/python/docs/writing-tests , https://playwright.dev/python/docs/locators , https://playwright.dev/python/docs/test-runners , https://playwright.dev/python/docs/test-assertions (HIGH)
- Package registries for current versions (checked 2026-02-19):
  - https://pypi.org/pypi/fastapi/json
  - https://pypi.org/pypi/jinja2/json
  - https://pypi.org/pypi/playwright/json
  - https://pypi.org/pypi/pytest-playwright/json
  - https://pypi.org/pypi/pytest-xdist/json
  - https://registry.npmjs.org/htmx.org/latest
  - https://registry.npmjs.org/alpinejs/latest
  - https://registry.npmjs.org/@alpinejs/persist/latest
  - https://registry.npmjs.org/idiomorph/latest

---
*Stack research for: Telegram auction admin density UX improvements*
*Researched: 2026-02-19*
