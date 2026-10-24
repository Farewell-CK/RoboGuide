# Replica readiness and producer retirement projection acceptance

ADR-0189 adds a readonly `ReplicaReadiness` response to the actual Replica process. It is
validated through real software Raft/mTLS processes and the existing content quorum path. The
response distinguishes:

- actual OpenRaft role, term, leader, log/applied positions and current/joint voters;
- metadata/quorum observation;
- same-voter complete content majority;
- a bounded refusal reason when a follower/learner cannot authoritatively report content readiness.

The native Snapshot/Log process gate now checks that a post-Commit leader reports metadata and
content readiness, while the learner test checks that a learner remains independently observable and
does not claim content-ready. Producer retirement remains owned by the existing original intent,
parent reference and Unknown reconciliation path; the readiness DTO cannot create or retire a pin.

The software evidence creates no embodied Node, device, model, simulator, hardware identity,
physical safe-state or physical-continuity claim. Joint membership, full producer GC and cross-host
HA remain later A2/A3/N3 work.
