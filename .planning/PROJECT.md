# LiteAuction

## What This Is

LiteAuction is a Telegram-first auction platform with a FastAPI admin panel for operators and moderators. It covers the full auction trust loop: lot intake, bidding, moderation and risk triage, appeals, feedback, and audit trails. The current admin UX baseline includes dense queue controls, named workflow presets, and in-place triage interactions for faster queue handling.

## Core Value

Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## Current State

- Milestone **v1.0 Operator UX** is shipped and archived.
- Moderation queues support persisted dense-list ergonomics, deterministic workflow presets, and in-place triage interactions.
- Planning focus has shifted from delivery to defining the next milestone scope.

## Requirements

### Validated

- ✓ Operators can configure dense moderation queues (density, filters, column layout) with durable persistence.
- ✓ Operators and admins can manage named workflow presets with deterministic default and last-selected behavior.
- ✓ Operators can triage queue rows in place with progressive details, keyboard flow, and safe bulk actions.
- ✓ Scope-based RBAC and CSRF protections remain enforced for web moderation actions.
- ✓ Sprint 51 delivery traceability is complete (issues `#205`, `#206`, `#207`, `#208`, `#212`, `#215`, `#218` all closed).

### Active

- [ ] Define and plan milestone v1.1 scope and acceptance criteria.
- [ ] Deliver adaptive detail depth controls with predictable navigation (`ADPT-01`).
- [ ] Add operator preset telemetry for quality analysis (`TELE-01`).

### Out of Scope

- Native mobile app clients - current strategy is Telegram + web admin first.
- Multi-platform auction channels outside Telegram - current operations are optimized for Telegram workflows.
- Non-auction social feature expansion - outside trust-first auction operations scope.

## Context

The codebase is a Python 3.12 modular monolith using aiogram (bot), FastAPI (admin web), SQLAlchemy/Alembic (data), PostgreSQL, and Redis with Docker Compose services (`bot`, `admin`, `db`, `redis`). Milestone v1.0 (Sprint 51 delivery line) completed three phases and delivered operator UX improvements for focus, presets, and in-place triage while preserving moderation and security guarantees.

## Next Milestone Goals

- Define a focused v1.1 milestone that extends v1.0 operator workflows without regressing queue speed.
- Prioritize measurable operator outcomes (time-to-action, reopen risk, filter churn) to guide preset tuning.
- Preserve strict safety posture (RBAC, CSRF, explicit confirmations, deterministic state transitions).

## Constraints

- **Tech stack**: Python 3.12 + aiogram + FastAPI + SQLAlchemy + PostgreSQL + Redis - stay aligned with existing runtime and CI.
- **Quality gates**: Ruff, unit tests, and DB integration tests must pass for touched areas.
- **Security**: RBAC scopes, CSRF checks, and moderation auditability cannot regress.
- **Operations**: Queue workflows must preserve operator context (filters, focus, scroll, row position).

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Keep Telegram-first with web admin as operator control plane | Existing product usage and moderation process center on Telegram workflows | ✓ Good |
| Use deterministic preset precedence (first-entry admin default, then last-selected) | Prevents non-deterministic queue state and operator confusion | ✓ Good |
| Keep triage interaction model to two disclosure levels (list + inline details) | Maintains scan speed and avoids deep navigation context loss | ✓ Good |
| Gate destructive bulk actions with explicit confirmation + server-side validation | Reduces accidental moderation mutations and improves safety posture | ✓ Good |

---
*Last updated: 2026-02-20 after v1.0 milestone completion*
