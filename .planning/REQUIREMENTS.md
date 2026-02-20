# Requirements: LiteAuction v1.1 Adaptive Triage Intelligence

**Defined:** 2026-02-20
**Core Value:** Run trustworthy Telegram auctions end-to-end with fast operator intervention and clear auditability.

## v1.1 Requirements

### Adaptive Detail Depth

- [x] **ADPT-01**: Operator detail depth adapts by risk and priority rules while keeping predictable navigation.
- [x] **ADPT-02**: Adaptive behavior is transparent with a visible reason code and deterministic fallback.
- [x] **ADPT-03**: Operators can override adaptive depth per row without losing queue context.

### Preset Telemetry

- [x] **TELE-01**: Product team can review preset quality telemetry for time-to-action, reopen rate, and filter churn.
- [ ] **TELE-02**: Telemetry sampling excludes unauthorized and failed actions to avoid misleading quality signals.
- [x] **TELE-03**: Telemetry export supports queue and preset segmentation for weekly operations review.

### Guardrails and Safety

- [x] **SAFE-01**: RBAC and CSRF protections remain enforced for all adaptive and telemetry endpoints.
- [x] **SAFE-02**: Adaptive defaults are bounded to prevent high-risk auto-expansion churn in dense queues.

### Verification

- [x] **TEST-11**: Automated tests cover adaptive rule selection, override behavior, and fallback handling.
- [ ] **TEST-12**: Integration tests cover telemetry event ingestion and scoped aggregation outputs.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Autonomous moderation decisioning | Violates trust and auditability posture |
| Cross-product analytics warehouse | Too broad for v1.1 delivery window |
| New non-Telegram operator channels | Outside current product strategy |

## Traceability (Verification)

| Requirement | Planned Phase | Status |
|-------------|---------------|--------|
| ADPT-01 | Phase 1 | Verified (Met) |
| ADPT-02 | Phase 1 | Verified (Met) |
| ADPT-03 | Phase 2 | Verified (Met) |
| TELE-01 | Phase 2 | Verified (Met) |
| TELE-02 | Phase 2 | Verified (Partial) |
| TELE-03 | Phase 3 | Verified (Met) |
| SAFE-01 | Phase 3 | Verified (Met) |
| SAFE-02 | Phase 1 | Verified (Met) |
| TEST-11 | Phase 3 | Verified (Met) |
| TEST-12 | Phase 3 | Verified (Partial) |

---
*Last updated: 2026-02-20 after v1.1 verification pass (8 met, 2 partial)*
