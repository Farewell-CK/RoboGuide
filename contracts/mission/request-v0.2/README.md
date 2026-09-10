# Mission Request Contract v0.2

v0.2 preserves the v0.1 user commands and adds inspectable Mission review and repair evidence to
the GET/status projection.

- `messages` remains user clarification dialogue only;
- `review_history` records internal semantic Review results against exact draft revisions/digests;
- `repair_attempts` records the current bounded automatic-repair cycle;
- `Repairing` is transient Mission Intelligence deliberation, not Mission execution state.

A `RequestClarification` review issue returns the request to `NeedsClarification`. The Repairer may
only handle `RepairPlan` issues from already grounded facts. `RejectDraft` and repair-budget
exhaustion produce `Failed`. No rejected draft reaches approval or Controller submission.

Persisted v0.1 projections remain readable and normalize to v0.2 with empty review history and zero
repair attempts.
