# ADR-0197: Compose actual producer retirement with final member-local CAS reclamation

Status: Accepted; implemented with actual producer/GC/restart and three-leader cold acceptance.

The runnable content gate already retires a real original Completed upload across owners while
retained Raft Log/View/Snapshot and stream/reader pins independently protect its qualified bytes.
Individual producer and GC tests cannot establish that their complete normal lifecycle composes.
Deleting operational rows, expiring a budget or asserting process exit is not a release proof.

## Authority and ownership

Use a complete pinned recovery bundle and explicit new physical roots. Preserve original logical
identities, source initialization, operations, budgets, Unknown, restore bindings and original
Controller process history. Start actual configured infrastructure processes over real mTLS;
never invent a Controller wait, embodied Node, capability inventory or device fact.

Actual current accepted inputs must exclude the exact retired upload, while its original historical
Log/View/stream references still prevent GC. The maintained core Snapshot publication and applied-cut
Log purge release only the original replaced publisher/install/Log responsibility. Every current
member must independently complete its own actual parent lifecycle; leader labels cannot release
another member's producer. Existing managed reconciliation retains original publication/Unknown
beside independent complete parent and actual scope/process-end observations.

The first actual long-history gate published its Snapshot and committed the real Log purge, but
the original 15-second maintenance budget expired while per-producer reconciliation repeatedly
validated the complete Root. Keep that failure. The actual parent adapter may collect its complete
original observations while retaining the same parent mutex/writer, then reconcile them under one
Root connection guard. Validate the complete Root before and after the batch, retaining every
original per-producer FULL transaction and original first observer budget issued before Root queueing.
Do not cache source rows, skip individual parent/process/pin checks, create a batch success receipt
or make partial progress atomic by assertion. SQL failure retains actual earlier progress and later
Unknown; current complete validation remains mandatory before GC. Original scan bounds stay.
The independently complete 27 MB source also exceeds the fixture's fixed 15-second whole
Snapshot-plus-purge wait. The maintained core already accepts an explicit bounded duration;
new long-history fixture invocations declare 30000 ms before the first request, while omitted
fields retain the old 15000 ms. Preserve all old timeouts and exact committed partial effects.
This changes neither network stream clocks nor execution safety budgets and proves no SLO.

The longer first budget exposed actual epoch loss: OpenRaft awaits `RaftLogStorage::purge` in
its core loop. Metadata prefix deletion and the durable purged pointer must ACK before separate
Root retirement. A bounded coalesced owned job retains both actual leases and validates the complete
original parent/head/Root roster; it releases only Log sources at or below the real monotonically
durable purged cut. Capture these exact proofs under the parent writer, then release that mutex
before Root writes: this prefix cannot be appended again. Other producer kinds and truncation retain
their existing authority. Full Root validation and each original transaction remain. Errors retain
pins/Unknown and diagnostic failure; the high-level maintenance call waits for actual cleanup under
its original budget. Cancellation never drops the owned blocking job or its leases.

Expose the already-maintained `retire_ended_reader` through the closed local OS-admin content
maintenance command, beside existing copy and stream retirement. It accepts only an original reader
ID and exact original/restored Root declaration. The Root itself observes the original kernel/
process incarnation; caller booleans, age or supervisor labels confer no authority. A live original
reader remains protected. No network, checkpoint, Node, storage or recovery contract changes.

Only after all independent actual references are inactive may existing Root7/8 GC admit its durable
intent and irreversible qualified-key tombstone, then hash/unlink/fsync and record actual outcomes.
Original receipts, clocks, operation rows and Unknown remain unchanged. Same-ID GC observes only;
new readers/copies/streams/producers cannot resurrect a tombstoned key. Required current inputs
must remain intact. A member-local unlink does not erase original Artifact owners, Archive stores
or independently retained backup content.

## Required actual evidence

The maintained zero-Node gate must preserve actual binary/source identities and waits, directly
read all original producer/reference/operation inventories and hash real CAS files. Require blocked
GC before actual retirement, real mTLS Snapshot/purge across members, exact source release,
original-ended reader/stream retirement, retained history, final unlink/tombstones and unchanged
current required bytes. Restart and full pinned backup/verify/cold restore must retain original
GC/producer/Unknown evidence and never revive deleted keys. Failures remain separate evidence.

The probe may accept an explicit restore-binding digest to call the same existing bound Root open;
it cannot infer a restore declaration from files. This infrastructure composition does not claim
global Archive/reference coverage, rolling-upgrade completion, physical stopping or SoftwareComplete.

A second actual case retained an interrupted historical stream with qualified bytes but no original
object receipt. Complete backup correctly refuses that active missing receipt; never relax this
fence or promote file existence into original success. An explicit online repair case may preserve
raw forensic Root/parent files under both actual original inode/writer locks, then operate the same
original declared roots. Forensic copies are not verified bundles or restoration grants. An actual
content-ready majority may publish/purge its own current Snapshot before waiting for every member;
the independent original Snapshot receive supplies new scoped byte proof. Original interrupted
stream rows and Unknown stay exact. Only subsequent complete actual publication permits backup.
The attempted original uncertain restart exposed a separate pre-Raft replay/network startup cycle;
this is retained as an open recovery requirement, not a completed repair result. The complete
producer/GC case uses independently verified fully materialized stores from the actual three-leader
cold gate. Its result cannot close that uncertain-startup counterexample.
