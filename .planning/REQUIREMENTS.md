# Requirements: LiteAuction v1.2 Queue Trust Signals

**Defined:** 2026-02-20
**Core Value:** Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## v1.2 Requirements

### Queue SLA Awareness

- [ ] **SLA-01**: Operators can see per-row SLA health state with deterministic countdown windows for pending moderation work.
- [ ] **SLA-02**: Queue views provide aging buckets and SLA filter chips without losing current sort, filter, or focus context.

### Decision Evidence Trail

- [ ] **EVID-01**: Operators can open an inline evidence timeline showing key queue events and policy-relevant transitions for each row.
- [ ] **EVID-02**: Moderation actions can include a concise rationale artifact suitable for later audit review.

### Telemetry Trend Guardrails

- [ ] **TRND-01**: Workflow preset telemetry exposes week-over-week trend deltas for time-to-action, reopen rate, and filter churn.
- [ ] **TRND-02**: Trend insights apply minimum sample guardrails to avoid misleading recommendations from sparse data.

### Guardrails and Safety

- [ ] **SAFE-11**: RBAC and CSRF protections remain enforced for new SLA, evidence, and trend endpoints.
- [ ] **SAFE-12**: Evidence and rationale records remain immutable after write, with explicit audit metadata for actor and timestamp.

### Verification

- [ ] **TEST-21**: Automated tests cover SLA state derivation, aging bucket assignment, and queue-filter continuity behavior.
- [ ] **TEST-22**: Integration tests cover evidence timeline rendering and telemetry trend aggregation from persisted database events.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Autonomous moderation decisions from SLA/telemetry signals | Violates human-in-the-loop trust posture |
| Cross-product analytics warehouse integration | Too broad for v1.2 delivery window |
| Non-Telegram operator channels | Outside current product strategy |

## Traceability (Planned)

| Requirement | Planned Phase | Status |
|-------------|---------------|--------|
| SLA-01 | Phase 1 | Planned |
| SLA-02 | Phase 1 | Planned |
| EVID-01 | Phase 2 | Planned |
| EVID-02 | Phase 2 | Planned |
| TRND-01 | Phase 3 | Planned |
| TRND-02 | Phase 3 | Planned |
| SAFE-11 | Phase 3 | Planned |
| SAFE-12 | Phase 2 | Planned |
| TEST-21 | Phase 3 | Planned |
| TEST-22 | Phase 3 | Planned |

---
*Last updated: 2026-02-20 for v1.2 kickoff*
