# Sprint 52: Adaptive Detail Depth Policy Contract

## Purpose

Define deterministic, explainable rules for inline detail depth in moderation queues for v1.1.

The contract keeps disclosure bounded to two levels only:
- list row
- inline row details

No nested detail levels are introduced.

## Service Contract

Implemented in `app/services/adaptive_triage_policy_service.py`.

### Inputs

- `queue_key`
- `risk_level`
- `priority_level`
- `operator_override` (optional)

### Output

`AdaptiveDetailDepthDecision`:
- `depth`: `inline_summary` or `inline_full`
- `reason_code`: deterministic reason for selected depth
- `queue_key`: normalized queue key used by policy
- `fallback_applied`: whether fallback normalization path was used
- `fallback_notes`: machine-readable notes (`unknown_queue`, `invalid_*`)

## Reason Codes

- `operator_override`: operator explicitly selected depth.
- `risk_auto_expand`: auto-expanded because risk threshold matched.
- `priority_auto_expand`: auto-expanded because priority threshold matched.
- `risk_and_priority_auto_expand`: both rules matched.
- `default_collapsed`: no rule matched, queue default depth applied.
- `fallback_default`: invalid/unknown input required deterministic fallback.

## Queue Policy Defaults (v1.1 kickoff)

- `complaints`: expand on risk `high|critical` or priority `high|urgent`.
- `signals`: expand on risk `medium|high|critical` or priority `high|urgent`.
- `trade_feedback`: expand on risk `critical` or priority `urgent`.
- `appeals`: expand on risk `high|critical` or priority `urgent`.
- unknown queue: default to `inline_summary` with `fallback_default`.

## Guardrails

- Hard depth bound: only `inline_summary` and `inline_full` are valid outputs.
- Override guardrail: invalid override values are ignored and logged in fallback notes.
- Safety-first fallback: unknown queue or invalid tokens never auto-expand by accident.
- Determinism: same normalized input always yields the same decision and reason code.

## Rollout Notes

- Phase 1 delivers policy contract + tests only.
- Phase 2 consumes this contract in queue UI rendering and row-level overrides.
- Phase 3 validates security and regression coverage for policy-driven behavior.
