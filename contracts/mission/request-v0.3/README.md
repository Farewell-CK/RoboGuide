# Mission Request Contract v0.3

v0.3 replaces the duplicated `instruction` plus `messages` persistence shape with one ordered,
structured `dialogue` source. Each turn records its identity, speaker, semantic kind, receive time,
and optional reply target.

Dialogue contains only user-facing instruction and clarification exchange. Planner drafts,
Reviewer findings, and Repair attempts remain revision-bound internal evidence in
`review_history`; they are not synthetic dialogue turns.

When a context-aware approval rule matches an admitted draft, `approval_reasons` retains the stable
rule identities that produced the revision-bound approval gate. It is policy evidence, not dialogue.

Persisted v0.1 and v0.2 projections remain readable. They normalize their instruction and user
messages into dialogue when loaded, while all newly written projections use v0.3.
