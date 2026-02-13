# Market Benchmark Matrix Template

Use one row per product. Score each dimension 1-5.

| Product | Segment | Trust Signals | Pre-trade Guardrails | Dispute/SLA | Reputation Loop | Incentive Utility | Mod Tooling | Notable Pattern | Reuse Feasibility (LiteAuction) |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| Example A | C2C local | 4 | 3 | 5 | 4 | 2 | 4 | SLA timer visible to both sides | High |
| Example B | Telegram-native | 2 | 2 | 3 | 2 | 4 | 2 | Fast bot-driven moderation | Medium |

## Candidate Practice Scoring

Use this table after extracting practices.

| Practice | Source Products | KPI Target | Impact (1-5) | Effort (1-5) | Risk (1-5) | Time-to-Value (1-5) | Priority Score | Decision |
|---|---|---|---:|---:|---:|---:|---:|---|
| Risk-based guarantor requirement | A, C, F | -20% dispute rate in high-risk lots | 5 | 3 | 2 | 4 | 4.0 | GO |
| Post-trade dual feedback | A, D, E | +8% repeat trade rate | 4 | 4 | 2 | 3 | 2.0 | HOLD |

Priority formula:

`priority = (Impact * Time-to-Value) / (Effort + Risk)`
