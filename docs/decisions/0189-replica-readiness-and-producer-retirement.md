# ADR-0189: Replica readiness and producer-retirement projection

Status: Accepted; implemented and software-validated. This closes a readiness observability slice only; it
does not claim complete N1/A2, HA, device readiness or SoftwareComplete.

## Decision

Expose one bounded readonly `ReplicaReadiness` projection from the actual running Replica process.
It records the actual member, role, term/leader, log and applied positions, current/joint membership,
metadata quorum observation, content readiness and complete content members. A leader may report
content readiness only after the current accepted content target passes the existing same-voter
metadata+bytes quorum path. A follower or learner can report metadata liveness while content
readiness remains false or unavailable; process liveness alone never becomes READY.

The projection reads current OpenRaft metrics and the original member-local producer heads under
the existing authority checks. It is readonly: it creates no producer, renews no budget, changes no
membership and never retires a pin. Existing producer reconciliation remains the only retirement
authority and keeps Unknown until original source/process and current parent references are proven
gone. The projection may report a pending/blocked reason without changing that history.

The maintained native network probe includes this projection in its status response and tests a
real learner/leader transition. The evidence is software infrastructure state only. It does not
establish a Node, robot, hardware, safe physical state, realtime guarantee or physical continuity.

## Compatibility

No Raft, Node, application, content SQL or recovery schema changes are required. The DTO is a
versioned observation response; older peers continue their existing protocol and cannot claim the
new readiness semantics.
