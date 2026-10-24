# ADR-0195: Initialize original archived application state without inventing a predecessor

Status: Accepted; implementation and actual initialization/entry/three-leader recovery checks pass.
Final current-source recovery also passes; remote CI is recorded separately in the acceptance report.

The retained native Archive-source capture fails because archived bare-content rows have no first
event/owner binding. It also needs an initialization contract: ordinary application-commit/v2
requires every new event to follow the receiver's actual previous tail. A compacted source has an
original nonzero floor and only its original hot tail. Inserting placeholder events, assigning an
imaginary previous checkpoint or weakening ordinary incremental validation is not acceptable.

## Authority and lifecycle

An explicit complete-content application-initialization/v0.1 envelope carries a context-bound
application-snapshot/v0.1. Its actual receiver predecessor is empty: revision zero, no accepted
checkpoint, events, commands, history anchor or content. Its original source is a complete validated
`CompleteReplicaState`, including exact checkpoint bytes, original authority commands, hot tail,
Archive manifests/operations/cleanup, protected Catalog rows and original history floor. The source
metadata digest is independently recomputable. The floor plus actual hot-tail length must equal the
checkpoint sequence; no prefix events or command outcomes are synthesized.

Generic application-commit validation and legacy ports continue to refuse application-snapshot/v0.1. Only the explicit
complete initialization path validates it beside the full original recovery projection. Source
command revisions and IDs remain exact; source compaction never becomes a made-up receiver command.
All original source bounds remain, including the complete recovery/table, candidate byte and event/
command limits. Exhaustion refuses; the operation does not page away mandatory original evidence.

Initial bare-content origins cover both actual hot rows and the protected archived rows, under the
same declared actual source SQL/CAS owners. Existing origins are never relabelled. Required current
inputs continue to use the existing lifetime reducers; retired history does not require deleted
bytes. Actual current qualified CAS bytes must be independently verified on the same receiving
members before application authority. Models, local file presence and metadata ACKs grant no use.

Only an empty receiver can accept this initialization. Later writes use ordinary incremental v2
under the initialized original floor. A later floor change continues to require coordinated Archive
maintenance. Same original source-operation IDs retain their first intent, budget and Unknown;
readonly accepted-state observations cannot manufacture the original completion receipt.
Accepted metadata retains an initialization anchor derived from that exact original candidate and
source identity. Later candidates carry it unchanged, so Log purge/current checkpoints cannot erase
the original initialization lineage. It is historical metadata, not a pin on retired source bytes
or a replacement for the source member's original operational budget/result journal.

## Explicit storage, product and recovery

Parent storage v9 fences earlier writers before new initialization authority, including at zero
commands. It retains the existing owned-Controller parent contract, original Root8/SQL identity,
member-local transfer/process/producer/source-operation histories and budgets. It is selected
explicitly; original parent7/8 entry points never auto-upgrade. Any existing-parent migration must
drain the actual original writers and retain all original identity and uncertainty. Raft snapshots
never import or erase member-local operational authority. Node contracts and the domain wrapper
remain unchanged.

The runnable supervisor explicitly selects this interpretation. Source initialization is declared
before child startup, with a common pinned complete-source identity and original Artifact owner;
local source paths are deployment inputs. The source is read only under its actual SQL and CAS
writer leases. A source-input declaration is never a Node/capability/device inventory. No empty
replacement Controller may start while initialization is unresolved. Fresh actual accepted metadata
and complete-content quorum may independently establish current readiness while the original
operation outcome remains Unknown; this does not fill or re-execute its old receipt. Already
accepted/restored initialization does not reread or repeat the original source. Normal deployments
without an initialization declaration keep their original semantics and policy.

Actual current voters must support the new interpretation before initialization effects; an old
member cannot receive new application authority through ordinary voter admission. Original epoch,
membership/TLS, complete-content quorum, source intent, original deadlines and owned asynchronous
work remain mandatory through cancellation and response loss.
Later commits may omit retransmitting an input already protected by that peer's exact previous
accepted view only after a fresh actual mTLS metadata/full-byte observation of that same view,
epoch, membership and Root. No cached availability, candidate-only byte claim or old stream budget
is reused. New inputs retain their original streams; final new metadata/application/full-content
quorum checks remain mandatory. Unavailable or different peers use the existing transfer path.
Local staging likewise avoids another source copy only for an exact qualified input in the
immediately preceding accepted target with the same epoch/membership. It still performs the
complete original-clock/current-permission read of every candidate input before returning.
Missing or corrupt local bytes therefore refuse; metadata never substitutes for byte verification.

The original archived source may be standalone. Its exact raw checkpoint remains unchanged at
initialization; it is never silently relabelled as a cluster checkpoint. Only the actual current
owned child, over its existing authenticated PID/Unix/mTLS channel and fresh full-content quorum,
may recognize that exact current initial checkpoint/anchor as its standalone predecessor. Other
cluster profiles, changed bytes and later checkpoints cannot use this exception. Before serving
mutations, one ordinary durable startup command writes the new profile through normal complete
content Commit, retaining the original source/command/anchor history. Generic restore still refuses
profile mismatches. The declared Controller binary must advertise initialization support before
the supervisor can start an application child; wrapper and Node contracts do not change.
Existing source identity authority also retains its original deployment requirement. Optional
typed Controller Node-TLS inputs pin actual certificate, key, CA and identity-profile file bytes in
the common application policy, independently of member-local paths. They populate only the existing
Node transport configuration; they never enroll, synthesize or start any Node. Source history is
not restored through a trusted-loopback fallback. Recovery declares these input files explicitly.
The same typed transport-input contract supports Operator and Artifact TLS with their existing
authorization profiles. Lifecycle Node identity continues to require Artifact certificate
authorization. The supervisor hashes all selected file bytes in its common policy; each listener's
existing application authority interprets its own profile. No environment-path substitution or
restored-state exception can remove these deployed identity requirements.

Explicit recovery v0.23 validates parent9 and both content interpretations, preserving original
source/initialization and all member-local histories beside complete Root8/CAS data. Earlier recovery
profiles refuse parent9. Restoration to new backing starts no service, renews no original budget and
does not convert source-process exit or copied metadata into physical continuity.
Original completed Archive operations also require their explicitly declared source Artifact CAS
and actual Archive destination in the complete bundle. Initializing metadata never manufactures
these independent stores. Repeated original Archive operations across Controller projections may
be validated once only in v0.23 when each projection is independently bound to the same actual
Replica profile and its full original intent/state/receipt/cleanup and source/destination match.
Every projection still requires the declared writer dependencies. Conflicts, another cluster and
standalone duplicates refuse; original records remain unchanged.
Complete archived candidates make synchronous parent-lock/validation waits material to Raft
liveness. Async target construction, captured read admission and supervisor revision/transfer
observations must queue their actual SQL/hash work on owned workers, retaining original Root/work
capacity, writer leases and caller clocks through cancellation. The same authority checks remain;
network/heartbeat execution must not wait synchronously for a parent writer. Original lost epochs
still fence and reap the child; no longer timeout or observation retry restores that authority.
Network-profile Raft log-state, log-range, durable vote and committed-pointer reads use a fresh
readonly transaction on the exact original leased WAL inode. They read only committed values,
including a consistent purge/last-log pair, and retain generation/profile checks. They do not take
the application writer mutex. Consensus writes, durability callbacks and application transactions
retain their original ordering; neither a pending application value nor cached metadata can answer
these reads. Legacy nonnetwork profiles keep their original connection behavior.
An unchanged network-profile vote or committed-pointer save may return only after that exact value
is independently observed durable in the fresh original WAL snapshot. Changed values still take
the original writer/FULL durability path. No callback is invented for a different value, and a
pending application transaction cannot lend its uncommitted metadata to this no-op path.

## Required acceptance

- A real already-compacted Controller/Artifact source first initializes actual authenticated
  replicas, with complete original checkpoint/hot tail/commands/Archive/Catalog and exact CAS.
- Missing/wrong archived origin, source digest, owner, original Archive prefix, command coverage,
  current bytes, old storage and nonempty receiver refuse before authority/effects.
- Normal incremental writes, missed Log and purged Snapshot catch-up, learner/joint admission,
  response loss, native process death/restart, complete recovery and old-writer fences compose.
- Original intent/Unknown/clock evidence survives; no synthetic event, device, model, simulator,
  physical stop/continuity or overall SoftwareComplete fact is created.
