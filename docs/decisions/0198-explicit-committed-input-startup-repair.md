# ADR-0198: Fail fast and explicitly repair unavailable committed replay inputs

Status: Accepted and implemented; native acceptance completed 2026-10-25.

The actual interrupted historical-stream case leaves a committed follower Log needing qualified
bytes with no original object receipt. OpenRaft 0.9.25 replays committed entries inside `Raft::new`,
before RoboGuide binds its listener. Waiting for missing bytes there creates a startup cycle.
File existence cannot replace the missing receipt; complete backup correctly refuses this state.

## Authority and lifecycle

Before starting Raft, inspect the exact actual committed-but-unapplied Log under the original parent
and Root writers. Deduplicate its declared qualified inputs and verify actual original object
receipts and bytes. Missing/corrupt/unavailable content refuses startup promptly with an explicit
repair requirement. Preserve every original entry, applied/committed pointer, stream Unknown,
producer, original budget and physical backing. Do not report that metadata is applied or a member
is content-ready while those prerequisites are unavailable.

Provide closed local OS-admin inspection and repair commands for the actual supported parent9/
Root8 profile. Opening existing managed files is mandatory; absent parent files or writer identity
must never create replacement authority. Exact profile, receiver, restore binding and original
initialization policy remain required. These commands do not start Raft, Controller, Nodes or devices.

Repair selects an exact original durable committed Log entry and one of its declared qualified
inputs, with the original complete entry digest. Require its real original producer/reference
binding. The source is an explicitly declared actual existing managed Artifact CAS whose real
owner equals the qualified input owner. In the maintained workflow it comes from an independently
verified complete recovery bundle restored to new roots; no caller-provided passed flag or digest
alone confers permission. Payload size/hash, source writer and target leases are independently
checked by the existing immutable-copy port.

Derive one stable repair-copy ID from the original receiver/Log/input, excluding retries and the
current process or epoch. Bind the first source path, entry digest and duration to its authority
digest. The existing Root copy intent/pin precedes reads and publication. Duplicate original IDs
observe only, including Unknown; changed source/context/budget refuses. Read-only observation must
remain possible without reopening missing source payloads. A later exact completed-copy release
uses its actual receipt, separately from original Log pins and original stream retirement.

Only the new repair operation may record its real copy completion. The old interrupted stream
remains Unknown, with its original raw row and clocks unchanged. This creates placement evidence,
never a semantic-use grant, renewed Mission permission, device-stop proof or physical continuity.
Normal Raft replay, current majority/content checks and Controller gates still decide later use.
No persistent, Node or network generation changes are required: all repair effects use existing
version-fenced Root copy/reference journals. A new public authority format would require a separate
explicit decision instead of hiding fields in those journals.

## Required actual acceptance

Use the retained actual interrupted Root/parent, preserving a raw forensic copy and original process
evidence. Require prompt startup refusal, zero implicit files/authority when parent/source is absent,
wrong owner/entry/input rejection, first real copy, source-unavailable same-ID observation, changed
context refusal, and original stream/producer/clock preservation. Rejoin actual mTLS peers and verify
complete current metadata/bytes; restart and complete recovery retain both old Unknown and the new
independent copy evidence. Software processes establish no device or global HA completion claim.

## Implementation and evidence

The maintained implementation is in `core/controller-replication/src/store_replay_repair.rs`,
the pre-Raft gate in `core/controller-replication/src/lib.rs`, and the closed local commands in
`apps/controller-replica/src/content_maintenance.rs`. The native checker
`tools/quality/check_replay_repair_processes.py` retains the original forensic source and checks
refusal, exact owner/input rejection, first copy, same-ID observation after source loss, immutable
context rejection, release ordering and actual three-member rejoin/restart. The separate
`tools/quality/check_replay_repair_cold_processes.py` verifies the pinned post-repair bundle and
rejoins/restarts its restored members.

The native run `replay-repair-native-05/result.json` passed with one actual missing committed input,
one real repair member, one retained startup refusal, one exact completed-copy release, and six
successful infrastructure waits. Its post-repair manifest is
`sha256:3687f3bf5758fb21a671f861dc3e15d0d999b53c12a3efe2fdb6223d7674df4a`. The independent cold
run `replay-repair-cold-native-04/result.json` passed with the original stream rows unchanged,
the repair operation retained on member 307, three restored members rejoined and restarted, and
six further successful waits. These are software-storage/process facts only; no embodied Node,
device, model, simulator, physical stop, or global HA claim follows.
