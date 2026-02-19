# LiteAuction

## What This Is

LiteAuction is a Telegram-first auction platform with a companion FastAPI admin panel for operators and moderators. It manages auction creation, bidding, moderation, appeals, feedback, guarantor flows, and trust/risk controls with PostgreSQL-backed state and Redis-assisted operational signals. The product is built for marketplace communities that need fast in-chat auction workflows with auditable operator controls.

## Core Value

Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## Requirements

### Validated

- ✓ Sellers can create and publish auctions with live post updates and anti-abuse bid protections — existing
- ✓ Moderators can review complaints, fraud signals, and appeals with queue-backed action workflows — existing
- ✓ Admin operators can perform scope-gated moderation actions through the web panel with CSRF-protected flows — existing
- ✓ Role-based access control is enforced consistently across bot and web surfaces — existing
- ✓ Background watchers finalize expired auctions and process outbox-driven integrations reliably — existing
- ✓ Operators can tune queue density, filtering, and column layouts with per-operator persistence across sessions — Phase 1

### Active

- [ ] Improve operator focus-mode defaults for admin dashboard information density
- [ ] Add operator-tunable dashboard density presets for different moderation contexts
- [ ] Refine admin typography and progressive disclosure behavior while preserving operational clarity
- [ ] Strengthen automated web test coverage for progressive disclosure and density-focused UX paths

### Out of Scope

- Native mobile app clients — current strategy is Telegram + web admin first
- Multi-platform auction channels outside Telegram — product fit and operations are optimized for Telegram workflows
- Non-auction social/community feature expansion — not core to trust-first auction operations

## Context

The codebase is a modular monolith on Python 3.12 using aiogram (bot), FastAPI (admin web), SQLAlchemy/Alembic (data + migrations), PostgreSQL, and Redis, with Docker Compose runtime services (`bot`, `admin`, `db`, `redis`). Current operations include moderation/risk queues, private DM topic routing, rewards ledger, and outbox-backed GitHub automation. Active sprint work targets admin UX density and progressive disclosure improvements without disrupting moderation throughput or established RBAC/security guarantees.

## Constraints

- **Tech stack**: Python 3.12 + aiogram + FastAPI + SQLAlchemy + PostgreSQL + Redis — align with established runtime and CI automation
- **Quality gates**: Ruff, unit tests, and integration tests must pass — prevent regressions in critical auction/moderation flows
- **Architecture**: Shared service layer for bot/web with watcher-driven async workflows — keeps behavior consistent across interfaces
- **Security**: Scope-based RBAC, CSRF checks, and moderation audit trails must remain intact — protects operator actions and accountability

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep Telegram-first product direction with web admin as operator control plane | Existing users and workflows center on in-chat auctions plus operator oversight | ✓ Good |
| Prioritize trust/risk moderation and auditability over peripheral feature expansion | Product value depends on safe, reliable auction outcomes more than breadth | ✓ Good |
| Focus current milestone on admin information density and progressive disclosure UX | Active issues indicate operator efficiency gains as the immediate leverage point | — Pending |
| Persist dense-list preferences per authenticated subject and queue with strict payload validation | Cross-session UX had to be reliable while preventing malformed layout state from being stored | ✓ Good |
| Keep quick filter client-local while advanced qualifiers remain server-validated | Needed fast row scanning without weakening trusted backend filter validation and auditability | ✓ Good |
| Use shared `data-col` contract with measured sticky offsets for pinned columns | Reusable queue behavior reduces per-route drift and prevents pin overlap under dynamic layouts | ✓ Good |

---
*Last updated: 2026-02-19 after Phase 1 transition*
