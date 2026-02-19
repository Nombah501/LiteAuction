# Project Research Summary

**Project:** LiteAuction
**Domain:** Telegram auction operations platform with FastAPI moderation/admin console UX refinement
**Researched:** 2026-02-19
**Confidence:** HIGH

## Executive Summary

LiteAuction is a trust-sensitive moderation product where operator speed must improve without weakening RBAC, CSRF, auditability, or decision quality. Across stack, feature, architecture, and pitfalls research, the strongest consensus is to evolve the existing server-rendered FastAPI admin surface incrementally instead of introducing a frontend rewrite. Experts build this class of system by preserving command safety boundaries, progressively enhancing read-heavy pages, and validating dense interactions with browser-level regression tests.

The recommended implementation is FastAPI + Jinja2 templates, htmx for partial updates, and Alpine.js only for local UI state such as density and disclosure preferences. Architecture should enforce a clean command-query split: read routes compose dense viewmodels and presets; write routes remain authoritative moderation commands with PRG + CSRF + RBAC checks on every request. Feature rollout should prioritize P1 capabilities (density controls, saved presets, row-level disclosure, keyboard flow, and regression tests), then layer focus-mode defaults and typography tuning, with adaptive risk-based disclosure deferred until stable telemetry exists.

Key risk is accidental coupling of UX changes to safety-critical behavior: hidden controls being treated as authorization, critical context being collapsed, or compact modes degrading keyboard/a11y throughput. Mitigation is explicit: define minimum decision context per action, keep two-level disclosure max, instrument primary-vs-secondary action usage, extend audit context for high-risk operations, and ship with feature flags plus cohort rollout and rollback guardrails.

## Key Findings

### Recommended Stack

Research supports a low-risk, high-leverage path that keeps the current Python/FastAPI operational model and introduces targeted frontend enhancements for dense operator workflows. This avoids duplicated validation logic, preserves backend trust boundaries, and enables iterative delivery behind runtime flags.

**Core technologies:**
- `FastAPI` (`0.129.x`): server-rendered admin routes and trusted mutation endpoints - keeps moderation policy enforcement server-side.
- `Jinja2` (`3.1.6`): template/partial composition - replaces brittle inline HTML and reduces regression risk.
- `htmx` (`2.0.8`): HTML-over-the-wire partial updates - improves responsiveness without SPA complexity.
- `Alpine.js` (`3.15.8`): micro-state for disclosure/density UI - keeps business logic out of the client.
- `Playwright` + `pytest-playwright` (`1.58.x` / `0.7.2`): interaction-level regression testing - catches focus, disclosure, and RBAC/CSRF UX regressions.

Critical version constraints: pin assets/deps, use idiomorph with htmx for morph swaps, and keep CSP-safe vendored assets over runtime CDN scripts.

### Expected Features

Launch credibility depends on operator throughput features that are standard in dense moderation tooling, with one clear differentiator: curated workflow presets that reduce setup friction per moderation context.

**Must have (table stakes):**
- Density controls (`compact/standard/comfortable`) with compact default for ops-heavy queues.
- Saved operator presets (filters, sort, visible columns, grouping, density).
- Fast filtering/search and persistent column visibility/reorder/pinning.
- Row-level progressive disclosure (master-detail) that preserves list position.
- Keyboard-first list operations and bulk-action guardrails.
- Regression coverage for disclosure state persistence and focus return.

**Should have (competitive):**
- Workflow-specific focus-mode preset packs (Moderation/Appeals/Risk/Feedback).
- Compact-mode typography refinement optimized for long queue scanning.
- Preset quality telemetry (time-to-action, reopen/reversal indicators, filter churn).

**Defer (v2+):**
- Adaptive disclosure depth by risk/priority.
- Preset recommendation engine driven by usage telemetry.

### Architecture Approach

Architecture should remain a modular monolith with stronger internal boundaries: route handlers for HTTP/auth/PRG, a presentation policy layer for presets/disclosure defaults, viewmodel builders for dense UI sections, and unchanged domain services as command/query authorities. The key pattern is command-query separation at the admin boundary, with runtime settings used for controlled rollout and kill switches. Keep UI state non-authoritative, and preserve transaction ownership and audit logging in server-side action paths.

**Major components:**
1. `web/routers` - split read pages (`dashboard/listings`) from write actions (`moderation_actions`) to reduce mutation regression risk.
2. `web/viewmodels` + preset policy - map service snapshots into disclosure-safe section DTOs and density behavior.
3. `services/*` - retain authoritative moderation commands, query snapshots, and runtime feature-flag controls.

### Critical Pitfalls

1. **Security-by-UI assumptions** - prevent by enforcing per-request server authorization and logging access-denied attempts.
2. **Hiding decision-critical context** - prevent by defining non-collapsible minimum context for high-risk actions.
3. **Over-deep disclosure stacks** - prevent by capping at two levels and routing rare deep edits to dedicated views.
4. **Compact mode accessibility/keyboard regressions** - prevent by validating each density preset as its own a11y + keyboard surface.
5. **Big-bang rollout without guardrails** - prevent by feature-flag cohorts, rollback levers, and metric-based halt criteria.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 0: Baseline Telemetry and UX Guardrails
**Rationale:** Instrument before redesign so disclosure and density decisions are evidence-based, not opinion-based.
**Delivers:** Baseline metrics (time-to-action, expansion frequency, reversal/reopen trends, shortcut usage), success thresholds, rollback triggers.
**Addresses:** Preset telemetry foundation and anti-feature prevention from FEATURES.
**Avoids:** Pitfall 7 (no instrumentation), Pitfall 8 (unguarded rollout).

### Phase 1: Safety Contracts and Refactor Foundations
**Rationale:** Trust boundaries and context requirements must be locked before UI behavior shifts.
**Delivers:** Extracted preset/viewmodel layer, strict read/write router boundary, CSRF/RBAC regression checks, minimum decision-context contract, richer audit context for high-risk actions.
**Addresses:** P1 prerequisites for disclosure and density on safe foundations.
**Implements:** Architecture patterns: command-query split, PRG + CSRF discipline, server-authoritative policy checks.
**Avoids:** Pitfall 1, Pitfall 2, Pitfall 6.

### Phase 2: Core Operator Throughput UX (MVP)
**Rationale:** Deliver highest-value operator speed improvements once safety scaffolding is in place.
**Delivers:** Density controls, saved presets, persistent column state, fast filtering/search, row-level progressive disclosure (two-level max), keyboard shortcuts.
**Uses:** `FastAPI + Jinja2 + htmx + Alpine` with pinned assets and policy-driven disclosure defaults.
**Implements:** View composition and preset policy matrix across dashboard/listing read paths.
**Avoids:** Pitfall 3, Pitfall 5.

### Phase 3: Hardening and Operator Experience Quality
**Rationale:** Validate that compact and disclosure flows are fast, accessible, and stable under real workflows.
**Delivers:** Playwright regression suite (focus retention, keyboard-only flows, disclosure persistence, RBAC/CSRF UX), compact typography tuning, bulk-action guardrail tuning.
**Addresses:** P1 completion quality and P2 readiness.
**Avoids:** Pitfall 4 and latent regression drift.

### Phase 4: Controlled Rollout and Optimization
**Rationale:** UX changes in moderation systems need progressive exposure and reversible controls.
**Delivers:** Cohort-based feature-flag rollout, per-role enablement, operational dashboard for guardrail metrics, staged expansion to all operators.
**Addresses:** P2 focus-mode defaults and validation for future P3 adaptive disclosure.
**Avoids:** Pitfall 8 and productivity cliffs.

### Phase Ordering Rationale

- Dependencies require policy/state model before advanced presets: persistence and preset infra unlocks focus-mode defaults and quality telemetry.
- Architecture recommends isolating read-path UX iteration from write-path moderation commands before introducing richer interactions.
- Pitfall mapping is front-loaded on security/context/audit in early phases, then interaction depth/persistence/a11y, then rollout safety.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1:** Audit schema extension strategy and evidence-context retention policy for compliance replay.
- **Phase 2:** Preference scope resolution (per-user/per-workspace/per-role precedence) and migration path from local to server persistence.
- **Phase 4:** Guardrail metric thresholds and auto-halt criteria calibration by moderation queue type.

Phases with standard patterns (skip research-phase):
- **Phase 0:** Baseline event instrumentation patterns are well-established.
- **Phase 3:** Playwright keyboard/focus/a11y regression implementation follows mature, documented workflows.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Strong alignment across official FastAPI/htmx/Playwright docs and clear compatibility guidance. |
| Features | MEDIUM | Feature set is well-supported by industry patterns, but some competitor-derived assumptions need local validation. |
| Architecture | HIGH | Directly grounded in current repository structure and established backend patterns. |
| Pitfalls | MEDIUM | Security/a11y guidance is strong; some UX tradeoff guidance is heuristic and should be validated with operator telemetry. |

**Overall confidence:** HIGH

### Gaps to Address

- Preference ownership model is not fully settled (user vs role vs workspace defaults): define explicit precedence and reset semantics before implementation.
- Audit context granularity for decision-path replay needs schema and retention decisions with compliance stakeholders.
- Adaptive disclosure criteria (risk thresholds and reveal depth rules) are still speculative: defer until MVP telemetry stabilizes.
- Performance envelope for dense queues under high concurrency needs load validation after MVP (panel expansion and filter query behavior).

## Sources

### Primary (HIGH confidence)
- Context7 `/fastapi/fastapi/0.128.0` and FastAPI docs (`templates`, routing, responses) - server-rendered architecture and redirect patterns.
- Context7 `/bigskysoftware/htmx/v2.0.4` and htmx docs/extensions - progressive enhancement and morphing patterns.
- Context7 `/websites/playwright_dev_python` and Playwright Python docs - regression testing strategy.
- SQLAlchemy async docs - transaction/session patterns for command safety.
- W3C APG disclosure and WCAG 2.2 guidance - accessibility and interaction constraints.
- OWASP Authorization and Logging cheat sheets - security and audit controls.

### Secondary (MEDIUM confidence)
- MUI X and AG Grid docs - dense table feature conventions and persistence patterns.
- Atlassian design guidance - compact spacing and disclosure ergonomics.
- GitHub/Jira workflow patterns - saved-view and keyboard-operation expectations.

### Tertiary (LOW confidence)
- Nielsen Norman Group progressive disclosure references - useful framing, but older/non-product-specific evidence.

---
*Research completed: 2026-02-19*
*Ready for roadmap: yes*
