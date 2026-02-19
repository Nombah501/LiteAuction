# Pitfalls Research

**Domain:** UX refinement for moderation/admin systems (density controls + progressive disclosure)
**Researched:** 2026-02-19
**Confidence:** MEDIUM

## Critical Pitfalls

### Pitfall 1: Security-by-UI (hidden != unauthorized)

**What goes wrong:**
Teams hide advanced moderation actions behind disclosure menus and assume this is sufficient protection, while backend endpoints still accept unauthorized calls.

**Why it happens:**
UX workstreams and authorization workstreams are split; teams treat disclosure as a visual concern and skip permission checks on every request path.

**How to avoid:**
Keep RBAC/ABAC checks server-side for every moderation action endpoint, regardless of visibility in UI. Add automated authorization tests for hidden and non-hidden paths, and deny by default for new actions.

**Warning signs:**
- API accepts a forbidden action when invoked directly (curl/Postman) even though button is hidden in UI.
- New disclosed action ships without backend policy tests.
- Incident review shows unauthorized attempts are not logged as explicit authorization failures.

**Phase to address:**
Phase 1 - Security contract before UI rollout.

---

### Pitfall 2: Hiding decision-critical context

**What goes wrong:**
Density and disclosure reduce clutter, but also hide timeline context (prior warnings, appeals, linked reports), causing incorrect moderation decisions.

**Why it happens:**
Teams optimize visual compactness before mapping which fields are truly required for high-risk actions.

**How to avoid:**
Define a non-collapsible "minimum decision context" for each moderation action (e.g., actor, target, recent actions, appeal status). Allow secondary details to collapse, not primary evidence. Validate with task walkthroughs on real moderation scenarios.

**Warning signs:**
- Moderators frequently open-expand-close several panels before every action.
- Reversal/appeal rate increases after the UI change.
- Operators copy IDs into separate tools to recover context not visible on the primary screen.

**Phase to address:**
Phase 1 - Task analysis and context contract.

---

### Pitfall 3: Over-deep disclosure hierarchies

**What goes wrong:**
Teams add multiple nested "advanced" layers (drawer -> popover -> modal -> section), increasing navigation cost and error rate during time-sensitive moderation.

**Why it happens:**
Progressive disclosure is applied repeatedly without an explicit cap on depth or path length.

**How to avoid:**
Limit disclosure to two levels for core moderation workflows. If a third level seems necessary, restructure the workflow and split by task mode instead of nesting further. Instrument click depth to high-risk actions and set thresholds.

**Warning signs:**
- Operators say they are "hunting" for controls.
- Median clicks/time for frequent actions increase after release.
- Support docs require flowcharts just to find a common action.

**Phase to address:**
Phase 2 - Interaction architecture and IA review.

---

### Pitfall 4: Density modes that break accessibility and keyboard throughput

**What goes wrong:**
Compact mode shrinks hit targets, focus indicators, or tab flow, reducing speed for power users and creating accessibility failures.

**Why it happens:**
Teams tune row height visually but do not re-validate target size, keyboard nav, disclosure semantics, and hover/focus behavior.

**How to avoid:**
Treat each density preset as a separate accessibility surface. Verify target size/spacing, keyboard traversal, disclosure semantics (`aria-expanded`, button roles), and dismissible/hoverable/persistent behavior for revealed content. Add regression tests for keyboard-only moderation flows.

**Warning signs:**
- Misclick rate increases in compact mode.
- Keyboard users lose focus when disclosure toggles open/closed.
- Tooltip/popover content disappears when users move pointer to read details.

**Phase to address:**
Phase 3 - A11y and keyboard hardening.

---

### Pitfall 5: Preference persistence mistakes (none, wrong scope, or unsafe scope)

**What goes wrong:**
Density/disclosure preferences either reset every session (productivity drag), leak across users/devices, or apply globally when they should be role/task specific.

**Why it happens:**
Settings persistence is treated as a front-end afterthought, without explicit scope design (per user, per workspace, per queue, per role).

**How to avoid:**
Define preference scope up front: per-user + per-workspace baseline, with role defaults and explicit reset. Persist server-side when possible for cross-device continuity; if local storage is used, namespace by user/workspace and clear on sign-out/session switch.

**Warning signs:**
- Operators repeatedly reconfigure density each login.
- Shared workstation users inherit previous user's panel state.
- Role leads report defaults being overwritten by unrelated teams.

**Phase to address:**
Phase 2 - State model and settings architecture.

---

### Pitfall 6: Missing audit granularity for disclosed actions

**What goes wrong:**
Audit trails record final moderation outcomes but miss important context: what was visible, what evidence panel was opened, and which settings influenced the decision path.

**Why it happens:**
Audit logging stays action-centric while UX adds stateful pathways that materially affect decisions.

**How to avoid:**
Extend audit schema for high-risk operations to capture decision context snapshots (selected filters, visible columns/panels, action source), actor identity, and timestamps. Keep logs tamper-evident and centrally monitored.

**Warning signs:**
- Post-incident review cannot reconstruct why an action was taken.
- Timeline shows action time but not evidence/context viewed before action.
- Security/compliance teams maintain parallel manual notes to fill log gaps.

**Phase to address:**
Phase 1 - Audit model update before UI behavior changes.

---

### Pitfall 7: No instrumentation for primary vs secondary feature split

**What goes wrong:**
Teams choose what to show/hide based on opinion, then lock in a poor split that hurts both novices and experts.

**Why it happens:**
Progressive disclosure is implemented without feature usage telemetry, task-frequency data, or error-rate segmentation by role.

**How to avoid:**
Instrument interaction events before redesign and during pilot: action frequency, panel expansion frequency, dwell time, reversal rate, and shortcut usage by role. Promote frequently used hidden actions to primary level; demote infrequent clutter.

**Warning signs:**
- "Advanced" panel is opened on most tasks.
- Frequently used actions are buried, while rarely used controls remain primary.
- Throughput variance across teams grows after release.

**Phase to address:**
Phase 0/1 - Baseline telemetry, then continuous tuning.

---

### Pitfall 8: Big-bang rollout without reversible guardrails

**What goes wrong:**
A large UX shift ships to all moderators at once, causing productivity dips and decision-quality regressions with no safe fallback.

**Why it happens:**
UX refinement is treated as "low risk" compared with backend changes, so teams skip progressive rollout and kill switches.

**How to avoid:**
Ship density/disclosure changes behind feature flags with cohort rollout, per-role enablement, and fast rollback. Define guardrail metrics (time-to-action, reversal rate, appeals reopened, error acknowledgments) and auto-halt criteria.

**Warning signs:**
- Immediate spike in handling time or appeals after release.
- Operators create unofficial workarounds (spreadsheets, side chats) to compensate.
- No mechanism to revert only the new disclosure model.

**Phase to address:**
Phase 4 - Controlled rollout and operational readiness.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Store all density/disclosure state only in local storage | Fast implementation | Lost preferences across devices, shared-terminal bleed, no admin reset | MVP-only, with explicit migration ticket |
| One global compact mode for all roles | Low design effort | Poor fit for investigators vs triage operators, hidden context errors | Never for production moderation tools |
| Add nested "advanced" menus instead of redesigning IA | Ships quickly | Navigation depth, discoverability debt, training burden | Temporary for one sprint with depth cap + telemetry |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| RBAC/Policy engine | UI visibility treated as authorization | Enforce policy checks server-side on every action request |
| Audit/timeline service | Log only final action | Log decision context metadata for high-risk moderation actions |
| Frontend data grid | Compact mode shipped without keyboard/focus regression checks | Validate tab flow, focus retention, and disclosure semantics per density mode |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Expanding panels trigger N+1 fetches | Slow panel opens; jitter during triage | Batch/parallel fetch key metadata, prefetch top evidence signals | Usually visible at 10k+ entities with high concurrency |
| Re-rendering whole tables on each density toggle | UI stalls after preference change | Virtualized grid + memoized rows + isolated state updates | Often visible once datasets exceed a few thousand rows |
| Client-side filtering of all records in dense views | Browser memory spikes and lag | Server-side filtering/sorting for moderation queues | Breaks under large queues and long sessions |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Assuming hidden controls are secure | Privilege escalation via direct API use | Deny-by-default + per-request server authorization checks |
| Not logging authorization failures for hidden actions | Silent abuse attempts; weak forensics | Explicit access-denied event logging with actor/context |
| Exposing sensitive moderation context in client-only cached panels | Data exposure on shared machines | Minimize client retention, clear on logout, protect cache scope |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Compact mode below usable target size | Misclicks and slower moderation | Keep minimum target size/spacing; allow role-based density defaults |
| Hover-only disclosure for critical context | Keyboard and low-vision users miss context | Make disclosure keyboard reachable and persistent until dismissed |
| Deeply nested advanced controls | High cognitive load and missed actions | Two-level max disclosure for frequent moderation tasks |

## "Looks Done But Isn't" Checklist

- [ ] **Density controls:** Works with keyboard-only flow and focus retention across panel toggles.
- [ ] **Progressive disclosure:** Primary screen still contains minimum decision context for every high-risk action.
- [ ] **RBAC integrity:** Hidden actions fail server-side when called directly.
- [ ] **Auditability:** Timeline reconstructs both action and decision context.
- [ ] **Preference scope:** Settings persist correctly per user/workspace and reset on sign-out.
- [ ] **Rollout safety:** Feature flag + rollback plan + guardrail metrics are live.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Critical context hidden | MEDIUM | Hotfix primary columns/panels, then rerun task analysis with real cases |
| Unauthorized hidden action path | HIGH | Disable endpoint/action via policy flag, patch server checks, run auth regression suite |
| Accessibility regression in compact mode | MEDIUM | Revert density preset defaults, patch sizes/focus behavior, rerun keyboard and WCAG checks |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Security-by-UI | Phase 1 | Direct API authorization tests fail for unauthorized roles |
| Hiding decision-critical context | Phase 1 | Task tests show high-risk actions completed without extra panel hunting |
| Over-deep disclosure hierarchies | Phase 2 | Median click depth to top actions is at/below target |
| Accessibility regressions in compact mode | Phase 3 | WCAG + keyboard regression suite passes per density preset |
| Preference persistence mistakes | Phase 2 | Cross-session and shared-terminal tests validate scope/reset behavior |
| Missing audit granularity | Phase 1 | Incident replay can reconstruct actor, context, and decision path |
| No telemetry for split decisions | Phase 0/1 | Dashboard tracks usage of primary vs secondary controls by role |
| Big-bang rollout without guardrails | Phase 4 | Cohort rollout dashboard + rollback drill completed |

## Sources

- W3C APG Disclosure Pattern (keyboard + aria-expanded semantics), HIGH confidence: https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/
- W3C WCAG 2.2 SC 2.5.8 Target Size (updated 2025-10-01), HIGH confidence: https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- W3C WCAG 2.2 SC 1.4.13 Content on Hover/Focus (updated 2025-09-17), HIGH confidence: https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html
- MUI X Data Grid Accessibility + Density + Tab Navigation (v8.27.0 docs), HIGH confidence for grid-UX implementation details: https://mui.com/x/react-data-grid/accessibility/
- MUI X Toolbar Settings Menu (local-storage persistence pattern), MEDIUM confidence: https://mui.com/x/react-data-grid/components/toolbar/#settings-menu
- OWASP Authorization Cheat Sheet (server-side auth checks, deny-by-default, per-request validation), HIGH confidence: https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html
- OWASP Logging Cheat Sheet (audit trails, logging failures/authorization failures, tamper concerns), HIGH confidence: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- NN/g Progressive Disclosure (feature split + depth tradeoffs), MEDIUM confidence due age (2006): https://www.nngroup.com/articles/progressive-disclosure/
- NN/g Complex Applications Guidelines (reduce clutter without reducing capability), MEDIUM confidence: https://www.nngroup.com/articles/complex-application-design/

---
*Pitfalls research for: Telegram auction bot + admin moderation panel UX refinement*
*Researched: 2026-02-19*
