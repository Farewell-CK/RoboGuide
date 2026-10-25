# ADR-0207: Original-profile application generation upgrades

Status: Accepted and implemented; actual rolling, Unknown, SQL-failure/abandonment, patch and cold-recovery acceptance passed.

The immutable Replica profile identifies the original cluster, original policy and original
application generation. A newer infrastructure binary may read an explicitly supported original
generation without granting its newer application write authority. The supported window is finite;
unknown and future generations refuse. Original identities, Root owners and profile hashes do not
change when an infrastructure executable is replaced.

An application generation upgrade is a separate authenticated administrative operation. Its exact
ID, original checkpoint/command head, membership, old/new schema, actual target binary digest,
target policy digest and bounded original receive-time budget are immutable. Every configured
member must independently expose its actual supported interpretation and target binary before any
drain effect. An old member's refusal cannot become an observation of support or an empty voter.

Member-local upgrade intents fence application spawn, ingress, Commit and delivery. Original child
stop and durable actual OS wait precede the member's drain receipt; spawning/unreaped history
remains Unknown. These operational histories never arrive from or disappear through a Raft
Snapshot. Parent storage v10 fences old writers before any new local upgrade authority is saved.
No infrastructure wait receipt establishes Node, Local EAIOS or device stopping.

Only the original epoch/quorum, unchanged complete application predecessor and complete all-member
drain proofs may produce a quorum-ordered activation. Activation changes the permitted application
writer schema/policy, retaining the old checkpoint bytes until normal exact application migration.
Old writers cannot submit old-generation batches afterwards. New writer bytes must be the exact
locally verified candidate; original base binary/policy inputs remain available for verification.
Downgrade and mismatched target binaries refuse. A lost response or cancelled wait remains uncertain;
readonly reconciliation uses the original operation and never renews a budget or repeats a stop.

Committed upgrade authority is included in bounded Raft snapshots and explicit recovery v0.25;
original local drain histories are preserved independently. A missing marker, incomplete original
proof, changed parent/Root ownership or unsupported recovery generation refuses before publication.
Restore never starts an application. Native acceptance must use genuine old/new binaries, actual
TLS peers, original inodes and wait results, injected interruptions, unsafe rollback refusal and
complete cold restore. Tests and local software processes create no hardware continuity evidence.

Same-schema application patches use a distinct original operation and distinct actual executable.
The complete activation history refuses any previously activated binary digest; the exact original
policy witness remains bound to the original startup profile and forbids returning to original code.
Retained executables are separately verified local inputs, bounded to 32, and cannot grant writer
permission without the matching committed receipt. A patch may retain the exact frozen older
checkpoint predecessor when a failed target has not yet migrated it; it never relabels those bytes.

An unexpectedly exited or refused application does not shut down Raft or its authenticated repair
API. The supervisor retains its actual OS wait and a bounded failure observation, and attempts at
most one application incarnation per current epoch/activated generation within that supervisor.
A distinct activation, new epoch or explicit supervisor restart permits a new attempt. Unreaped or
spawning history independently continues to fence replacement. This is infrastructure availability,
not a device restart or physical continuity claim.
