# Sprint 54 S54-004: Telemetry Trend Deltas and Guardrails

## Goal

Extend workflow preset telemetry with week-over-week deltas while suppressing misleading trend output when either window has too few samples.

## Implementation Notes

- `load_workflow_preset_telemetry_segments` now aggregates two windows per segment:
  - current lookback window (default 7d)
  - previous lookback window of equal size
- Segment output now includes:
  - `time_to_action_delta_ms`
  - `reopen_rate_delta`
  - `filter_churn_delta`
  - guardrail metadata (`trend_low_sample_guardrail`, `trend_min_sample_size`, `trend_previous_events_total`)
- Guardrail policy: deltas are suppressed when either window has fewer than 5 events.

## Web Surface

- Preset telemetry table now shows trend columns for the three tracked metrics.
- When guardrail suppression is active, trend cells display a guardrail note with sample counts.

## Validation Targets

- `tests/test_admin_queue_preset_telemetry_service.py`
  - trend delta math across current vs previous windows
  - low-sample suppression behavior
- `tests/integration/test_web_workflow_presets.py`
  - trend columns render with formatted delta values
  - guardrail note renders for sparse segments
