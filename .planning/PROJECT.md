# LiteAuction

## What This Is

LiteAuction is a Telegram-first auction platform with a FastAPI admin panel for operators and moderators. It covers the full trust loop for auctions: lot intake, bidding, moderation and risk triage, appeals, feedback, and audit trails. The admin surface now includes dense queue controls, deterministic workflow presets, adaptive triage detail depth, and preset telemetry insights.

## Core Value

Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## Current State

- Milestone **v1.0 Operator UX** is shipped and archived.
- Milestone **v1.1 Adaptive Triage Intelligence** is shipped and archived.
- Moderation queues support adaptive detail depth with transparent reason/fallback metadata and per-row override controls.
- Workflow preset telemetry now provides segmented quality signals and excludes failed action outcomes from sampling.

## Requirements

### Validated

- ✓ Operators can configure dense moderation queues (density, filters, column layout) with durable persistence.
- ✓ Operators and admins can manage named workflow presets with deterministic default and last-selected behavior.
- ✓ Operators can triage queue rows in place with progressive details, keyboard flow, and safe bulk actions.
- ✓ Adaptive detail depth by risk/priority is deterministic, transparent, and override-capable (`ADPT-01..03`) — v1.1.
- ✓ Preset telemetry captures and segments actionable quality metrics while excluding failed outcomes (`TELE-01..03`) — v1.1.
- ✓ RBAC and CSRF protections remain enforced for adaptive and telemetry surfaces (`SAFE-01..02`) — v1.1.
- ✓ Automated coverage includes adaptive behavior and DB-backed telemetry integration paths (`TEST-11..12`) — v1.1.

### Active

- [x] Define and scope milestone v1.2 requirements.
- [ ] Deliver queue SLA awareness surfaces with deterministic aging buckets (`SLA-01..02`).
- [ ] Deliver inline evidence timeline and moderation rationale trail (`EVID-01..02`).
- [ ] Deliver trend guardrails and safety verification for trust signals (`TRND-01..02`, `SAFE-11..12`, `TEST-21..22`).

### Out of Scope

- Native mobile app clients - current strategy remains Telegram + web admin first.
- Multi-platform auction channels outside Telegram - operations remain optimized for Telegram workflows.
- Autonomous moderation decisioning - conflicts with trust and auditability posture.

## Context

The codebase is a Python 3.12 modular monolith using aiogram (bot), FastAPI (admin web), SQLAlchemy/Alembic (data), PostgreSQL, and Redis with Docker Compose services (`bot`, `admin`, `db`, `redis`). v1.0 and v1.1 are now archived milestones; v1.1 added adaptive triage intelligence and telemetry quality signals while preserving moderation safety boundaries.

## Next Milestone Goals

- Improve operator awareness of queue urgency using clear SLA health signals and aging visibility.
- Increase moderation decision confidence with lightweight evidence timeline and rationale artifacts.
- Keep telemetry advisory by adding trend guardrails and preserving strict safety enforcement.

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
| Gate telemetry sampling on successful business outcomes only | Avoids misleading quality signals from conflict/failure paths | ✓ Good |
| Require DB-backed telemetry integration assertions before milestone closeout | Ensures aggregation confidence beyond monkeypatched route checks | ✓ Good |

---
*Last updated: 2026-02-20 for v1.2 milestone kickoff*
