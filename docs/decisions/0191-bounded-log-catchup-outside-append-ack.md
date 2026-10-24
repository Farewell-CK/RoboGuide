# ADR-0191: Bounded retained-Log catch-up outside metadata Append ACK

Status: Accepted; implemented and software-validated with the original failing bundle.

The Root7 cold-restart counterexample restored all bytes/journals correctly, then overlapping
Append handlers each waited for historical content before acknowledging the already-durable Raft
Append. Retried heartbeats exhausted the Root's bounded transport workers, including the live
Controller ingress. The first Artifact HTTP read lost its connection; a lagging member also could
not drain cleanly. The failed run remains evidence and is not a passing recovery case.

A successful metadata Append returns its actual upstream Append result independently of content
catch-up. Only one owned historical catch-up task per original Root is admitted at a time. It
retains the authenticated sender, actual persisted Log prefix and original pre-queue clock; an
already-active task causes no second task, no new byte command or renewed budget. Its bounded
lifetime is independent of the network handler. Each actual blocking job retains the original
admission guard even after async cancellation, so another task cannot replace still-running work.

Current membership/epoch/TLS/Log/source/pin checks and deterministic original stream IDs remain.
Unknown is never re-executed or replaced. Subsequent heartbeats may inspect the next actual durable
prefix after the previous scope has ended. Metadata ACK still grants no complete-byte acceptance:
parent apply, same-voter metadata+bytes quorum, application commit, child/readiness and original
reader gates remain mandatory. No durable, Node or application generation changes are required.

The actual counterexample and fixed run use the same pinned immutable recovery bundle and new
restore roots. Product verification runs again; reusing the bundle does not create new source
Snapshot producers or rewrite historical evidence. This remains software liveness and content
evidence, never a Node/device or physical-continuity claim.

The same manifest `sha256:7ece55f5c52360ef19b2f784bd6d5f3e86b9acb4e476448275dd52cf0cc036b1`
passed actual 337 → 307 → 311 leadership/read/Unknown checks with all original supervisor waits
zero. A real blocking-job cancellation regression retains its original admission/writer until
actual work ends. See [Root7 acceptance](../development/acceptance/2026-10-24-replica-content-gc.md).
