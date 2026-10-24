# ADR-0193: Preserve normal Raft heartbeats during Snapshot content preparation

Status: Implemented; actual blocked-read, Snapshot, learner and current software conformance passed.
See [acceptance](../development/acceptance/2026-10-24-content-voter-admission.md).

The actual Snapshot regression exposed an intermittent election during its byte prerequisite:
the source remained leader in term 2, while the returning voter advanced to Candidate in term 6.
Its original prerequisite stayed Unknown. Content preparation occurs before the first upstream
Snapshot chunk, so that chunk cannot keep the returning voter's election timer live.

Only the explicit network-content Snapshot generation sends normal authenticated Raft Append
heartbeats before and during its bounded content preparation. These carry the original upstream
leader vote, exact committed Snapshot cut as previous-log and commit positions, and zero entries.
OpenRaft alone accepts or rejects the vote and resets its election timer. A prefix conflict is
not a matching-log, content or quorum receipt. An actual higher vote is returned through the
upstream Snapshot response so that OpenRaft can step down; no local role override is permitted.

The first heartbeat, negotiation, original receive intent and byte work share one original
preparation clock. Existing mTLS/current-source/Root/receiver clocks and immutable Unknown remain.
The independent Root7 process case also exposed OpenRaft's default 200ms metadata-chunk timeout:
the receiver had actually installed the Snapshot, but the source never received acceptance and
still reported no matched position for the learner. Only the explicit network-content profile
uses a bounded 10-second metadata chunk wait. One original 15-second source clock spans first
heartbeat, preparation and all metadata delivery, so neither chunk retries nor the longer wait
renew the operation. Normal Append heartbeats continue through metadata delivery as well. Actual
blocking storage workers retain their original lifetime after a source timeout/cancellation.
Caller cancellation still retains actual blocking worker ownership, and no retry renews an old
intent. Metadata-only and non-Snapshot-content profiles retain their original transport behavior.
No storage, application, Node or wire generation changes are necessary because the heartbeat is
the existing Raft Append command. These are infrastructure liveness observations, never device or
physical-continuity evidence.
