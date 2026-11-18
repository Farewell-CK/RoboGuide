# ADR-0236: Replay durable cancellation before local execution admission

Status: Accepted.

The field origin task exposed two distinct gaps. An untyped Compute requirement
can select the camera's resource even when a local operation requires another pool.
The origin deployment will opt into existing Node config v0.14 / MissionPlan v0.10
typed Compute units (`spatial.origin-slots/v1`), preserving normal Control selection.
The process gate must include a competing, lexically earlier unrelated Compute pool.

The Node correctly rejected that original Execute before journaling or dispatch.
Its later Cancel was durably recorded in cancellation_intents; modern sessions
correctly omit inferred terminal facts when no execution row exists. Control retains
Unknown. A new explicit maintenance option `--observe-unadmitted-cancellation ID`
may select at most sixteen exact identities. Only persisted selected cancellations
with no execution row and no dispatch authorization produce an observation. Normal
modern sessions retain their receipt-only behavior. The observation uses sequence 1
and the existing cancelled-before-
local-dispatch reason, matching a later original Execute suppressed by that tombstone.
Reconnect replays only the selected original cancellation; no execution/spec row is fabricated.

The journal's existing tombstone and admission transaction remain authoritative.
Active, failed, unknown or dispatch-authorized work never receives this maintenance
pre-admission observation. Wire/schema/storage generations do not change. Cancelled
does not establish physical stopping, no-effects assurance, Memory revocation or
automatic continuation. The original Controller stop purpose and its administrator
reconciliation remain independently required, with original history and clocks.

Verify cold replay, later Execute suppression, exclusion of admitted/dispatched work,
and the real Controller/Node path. Apply to the field only after software checks;
retain the original rejected Mission rather than replaying it as new work.
