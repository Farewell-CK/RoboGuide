# ADR-0192: Complete-content voter admission before joint consensus

Status: Implemented; single-host actual software conformance passed. See
[acceptance](../development/acceptance/2026-10-24-content-voter-admission.md).

Actual mTLS/Snapshot learner testing found that metadata catch-up alone can admit a new joint
configuration while that learner's qualified bytes are corrupt. The operation may time out with a
pending joint configuration and no complete content majority. A returned error does not undo that
configuration; tests must inspect original voters before considering any follow-up operation.

For the explicit network-content profile, `change_membership` performs a bounded readonly
complete-content preflight after original learner enrollment/catch-up and before asking OpenRaft
to enter joint consensus. It freezes the current accepted target and original voting set, requires
the current complete metadata+byte quorum, independently observes each new voter's exact original
member/Root/current metadata/qualified bytes over actual mTLS, and requires a complete majority of
the proposed voter set. Actual original membership/epoch/trust are checked again before admission.
No first application/content state means there are no application bytes to prove; that explicit
empty bootstrap retains the existing metadata enrollment rules.

One process-local owned transition gate serializes actual complete-content commits and voter
changes from admission through their actual Raft result. A cancelled or expired caller does not
cancel an already queued Raft operation or release its gate; the owned worker retains it until
that original operation returns. Queueing, observations and pre-effect checks share the original
bounded clock. No new effect starts after expiry. A pending Raft result remains an uncertainty
fence rather than permitting another overlapping transition. This prevents a concurrent content
commit from invalidating the exact candidate between learner proof and joint submission. Epoch,
exact committed application/membership and trust are rechecked before submission; the gate grants
no distributed authority and never replaces Raft quorum or content verification.

Authenticated administration carries its original caller epoch into actual learner and membership
admission. Direct core calls freeze their epoch before queueing. The owned transition rechecks that
same epoch after acquiring its gate; it never adopts a later epoch from a new source observation.
Stale administration refuses before enrollment or joint effects, rather than performing the effect
and only then reporting the epoch mismatch. Existing Raft uncertainty after submission remains.
The actual member-writer guard and captured original TLS connection also follow the owned work.
Cancellation of its network waiter cannot release that writer while the transition is pending.
Original member/chain/leaf validity is rechecked against current committed trust before effects;
another connection or rotated leaf cannot lend authority to an expired original request.

These observations create no reservations, byte copies, application commits or device facts. They
are admission evidence under their original bounded clock, not a guarantee against later peer/disk
failure. OpenRaft retains joint-consensus and timeout/uncertainty authority; metadata-only profiles
retain their original semantics. Existing complete content/readiness and child/process fences stay.
No Root, Raft storage, Node or application generation changes are required.

Actual conformance covers rejected corrupt learner bytes with unchanged voters, repaired original
bytes followed by normal admission, exact old/new joint groups, complete new-voter content quorum,
and independent actual process/SQL/CAS evidence with zero embodied Nodes/devices/models/simulators.
