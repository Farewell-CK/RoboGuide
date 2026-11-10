# ADR-0244: State history lifecycle and lossless telemetry storage

Status: implementation in progress; no field deployment.

Storage v3 extends the explicit original v2 Catalog checkpoint. Current State, TTL, source
times, session epochs and sequence admission keep their existing semantics. Historical
StateRecordObserved values are stored once per original source key, payload schema and
exact value bytes. Original event envelopes retain byte-exact prefix/suffix and a reference.
The public events view reconstructs and verifies original JSON bytes; no Node wire change
or synthetic freshness is introduced. The storage marker fences all prior writers.

The same transaction writes an immutable value body, its event reference, Catalog coverage
and the original event counter. Migration streams old records under exclusive ownership;
failed migration rolls back the entire generation. Native read-only consumers must install
the standard verified-body SQLite function before querying reconstructed event content.
Recovery tools verify bodies, references, Catalog coverage and original authority together.

Typed journal classification distinguishes externally replayable commands from internal
Node-fact and timer transactions. Historical entries without a typed classification remain
protected; never infer deletion permission from an ID prefix.

Original State-policy decisions retain exact input identity to original event sequence in a
separate transactional reference index. Its coverage, protected edges and unresolved gaps are
validated before treating a reference as absent. The index stores digests and source sequences,
not another full copy of each observation. An unindexed archived prefix is explicitly unresolved.
This index supplies decision references only; it cannot invent task/Unknown/backup/replica proof.

Normal startup checks the derived GC index's closed schema, checksummed head and original sequence
coverage; it does not scan ordinary source-index rows or use them to grant execution authority.
Actual decisions still resolve selected records against their original verified events. Complete
reference counts/edges/source auditing runs in a separate readonly snapshot outside the FIFO
writer before any source-retirement proof, in explicit audit3, and during complete backup/restore.
The proof preserves original candidate pins and protected-head identity; a changed protection set
withholds index GC. No permission to delete follows from a merely valid startup index head.

Retention policy defaults to evaluation only: hot 24 hours; ordinary telemetry archives 30
days after their original durable publication. Explicit activation is required. Maintenance
uses one bounded worker (8192 rows / 4 MiB input), original archive publication receipts,
source-byte verification and short final transactions. Unknown and active task references,
decision inputs, external receipts, open backups/replica transfers and original Catalog
dependencies pin their evidence. No complete receipt means no deletion. Clock anomalies
suspend expiry. Expired telemetry is reported explicitly, not as nonexistent history.

Lossless hot relocation uses a separate explicit status from permanent expiry coverage. Its
default evaluation mode creates no original archive intent. An enabled policy freezes an aged
homogeneous bounded sequence prefix under the FIFO writer, verifies publication outside that
writer, then rechecks the original policy revision and current complete checkpoint before final
commit. A policy-only readonly WAL observer checks the original database inode and committed
revision during copying; an unrelated uncommitted Node transaction cannot fabricate revocation.
Routine State means Node-produced Reported/Observed Node/World values, not RoboGuide authority
State. Catalog, Task and checkpoint evidence remain protected even in an otherwise ordinary segment.

The final archive transaction retires only frozen-prefix bodies with no remaining hot references.
With an actual Controller clock sampled after writer acquisition, it may retire expired source
index rows without decision edges; unresolved gaps withhold this retirement. The original full
event bytes remain in verified immutable archives. Cumulative byte/hit counters and hot gauges
share the original mutation transaction; metrics declare their original accounting start sequence
and never invent totals for already archived pre-v3 history. Missing or corrupt counter authority
is refused. Prometheus counters and SQLite reusable-page gauges are observational only.

Permanent 30-day expiry is not authorized by hot relocation. Old archives without reliable
Controller completion time or complete backup/decision/replica reference coverage remain retained;
whole archive directories include protected checkpoints/Catalog and cannot be deleted as telemetry.

This work is delivered in the authorized A→B→C→D sequence, with existing-MI goal verification
and then replicated format negotiation as separate acceptance gates. No standalone pass
grants replicated recovery support or physical-effect verification. Deployment follows an
actual 24-hour stability run; simulated clocks are used only for long retention windows.

The existing MI process owns optional durable reported-posture verification work. A new
operator-authorization/v0.3 `verifier` grant requires actual mTLS and explicit tenant delegation;
its allowlist reads only State/inventory and original Mission admission/tenant/attempt evidence,
and publishes generic verifier evidence. It cannot admit, cancel or retry device actions.
Earlier authorization codecs/wrappers reject this role. The original 10-second window requires
two newer valid observations from the same source/epoch; unavailable or ambiguous evidence stays
unknown. This is software-reported posture verification, never independent physical-stop proof.
