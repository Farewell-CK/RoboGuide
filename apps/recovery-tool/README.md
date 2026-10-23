# RoboGuide recovery tool

[Profile v0.17](../../contracts/recovery/bundle-v0.17/README.md) adds the explicit actual Controller
storage generation and its SQL-inode writer lease, preserving the full v0.16 contract. Each Controller
declares `roboguide.controller-storage/legacy` or `roboguide.controller-storage/v1`. The latter retains
raw original events behind an exact read-only legacy view and fences old startup even at zero events.
It is selected explicitly with `ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA=1`; legacy is not a durable
downgrade fence. Restore keeps original identities/budgets and starts no service.

An actual hot rollback journal requires explicit OS-admin maintenance, outside all active original
writers. Pin the actual source SQL/journal digests and select a new evidence directory outside every
declared source root:

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery recover-artifact-journal \
  --profile /absolute/complete-v0.17-profile.json --artifact source-artifact \
  --sql-digest sha256:ACTUAL_SQL_DIGEST --journal-digest sha256:ACTUAL_JOURNAL_DIGEST \
  --evidence-directory /absolute/new-recovery-evidence --reason 'Original uncommitted SQL rollback'
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery observe-artifact-journal-recovery \
  --evidence-directory /absolute/new-recovery-evidence
```

Complete actual writers and known consumers, pinned inputs, original intent and forensic copies
precede writable SQLite. Only native `READONLY_ROLLBACK` is admitted; missing committed history is
never rebuilt. Existing-directory retries only observe, preserving old Unknown and original budgets.
Forensic evidence grants no application/content/GC authority; declare it as a `files` component with
an Artifact writer dependency when it must be included in a complete cold bundle. See ADR-0162 and
the maintained `check_artifact_sql_recovery_processes.py` actual fault matrix.

`retire-provider-snapshot --profile <current> --original-profile <original> --provider <component-id>
--operation-id <original-operation> --original-intent-digest <sha256>` closes an original unpublished
provider Snapshot after relocation/profile changes. Current profile v0.16 and the unchanged original
profile must expose every actual writer. Both provider instances, complete original requests and
generation-1 pins must match; released/published copies require normal publication verification.
It invokes the existing original v0.1 abort transaction only in the current provider, never copies,
deletes payloads or changes a separate historical source. All previous SQL/writer/Node schemas stay.
See [ADR-0158](../../docs/decisions/0158-relocated-original-provider-snapshot-abort.md) and the
[actual process acceptance](../../docs/development/acceptance/2026-10-23-relocated-provider-snapshot.md).

[Profile v0.16](../../contracts/recovery/bundle-v0.16/README.md) preserves all preceding complete
contracts and explicitly declares each ordinary Artifact component's actual storage v3 or v5.
Storage v5 adds the original same-owner GC journal and mandatory command/tombstone heads. The
offline `gc-artifact --profile <profile> --artifact <id> --content-digest <sha256> --byte-size <size>
--command-id <id> --reason <reason>` takes all original writer leases, checks every known Controller
and independent content reference, and commits original intent/tombstone before unlink. Unknown
commands only observe on retry. Active Catalog/lineage, uploads, unresolved use/import/handoff,
MI policy/goal, verifier, Snapshot and Archive claims refuse deletion. Actual Replica/online
authority is not supported by this offline entry and is refused rather than treated as quiescent.
`retire-artifact-snapshot` additionally requires the original profile/intent and all original/current
writers for an unpublished old-profile Snapshot; `reconcile-artifact-snapshot-retirement` only
publishes the exact retained pending receipt. Neither operation copies or deletes payloads.
Cold recovery retains all original Unknown/history and permits absent already released bytes.
See the [actual acceptance](../../docs/development/acceptance/2026-10-23-source-artifact-reclamation.md).
Hot rollback journals continue to refuse readonly startup/backup. The separate v0.17 explicit native
maintenance path preserves original history; Raft/online and the complete P1-P7 matrix remain required.

[Profile v0.13](../../contracts/recovery/bundle-v0.13/README.md) preserves original wrapper v49
copy intents while validating v50 / operations v0.2 unpublished-Archive cleanup. Actual source/
Archive writer fences, original cleanup budgets, durable tombstones and exact SQL receipts remain
independent of copy and receiving-reference history. Unknown retains its source pin; expired cleanup
never renews a copy. Missing mandatory cleanup history/tombstone refuses recovery. It preserves the
v0.12 complete directory/consumer/CAS contracts. Receiving Archive retirement/GC and online/full
P1-P7 acceptance remain separate required work.

[Profile v0.12](../../contracts/recovery/bundle-v0.12/README.md) requires the v3 Artifact reference
authority inventory, wrapper v49 Archive journal and all declared original consumers. It verifies
pending source pins and independently retained receiving Archive bytes alongside complete Catalog
recovery. Bundle v0.4 preserves mandatory nested empty directories; missing directories, references,
owners or content refuse publication. Restore receipt v0.12 retains original history and budgets.
Actual zero-Node TLS/SQL/CLI and file Mission gates remain separate from online majority, Archive
abort/GC and the remaining mandatory P1-P7 work.

[Profile v0.11](../../contracts/recovery/bundle-v0.11/README.md) explicitly selects Artifact storage
v2 with the original owner and mandatory maintenance journal. The actual CLI writes original
Snapshot intent/pins before copying; interruption keeps them active and the same command cannot
copy again. Complete original bundle verification permits exact release; `reconcile-snapshots`
copies zero files and `abort-snapshots` holds every original writer before fencing an unpublished
copy. Bundle v0.3 and restore receipt v0.11 preserve the original active intent, exact bytes,
kernel/UID and budgets. They retain the v0.10 Catalog, Memory v4 and Replica v3 contracts. Archive,
online majority/content replication, GC and complete P1-P7 remain separate required work.

[Profile v0.10](../../contracts/recovery/bundle-v0.10/README.md) keeps the v0.9 storage contracts
and verifies original Controller wrapper v48 / event v29 Catalog/lineage ownership against actual
CAS owners in the same complete bundle. Missing reference/publication history, absent original
owner or missing bytes refuses before publication. Parent retirement does not retire a child's
independent lineage. Released history needs no deleted content; restore starts no service and
never renews original time, permission, generation or budget. This is declared-store recovery,
not global reference coverage, an online replica ACK or physical continuity.

[Profile v0.9](../../contracts/recovery/bundle-v0.9/README.md) requires the actual Artifact retained
layout v1, including `.retention-owner.json`, the `blobs` regular-file old-writer fence, `objects-v1`
CAS and staging. Original metadata and all bytes are verified together; missing/conflicting identity
refuses without replacement. It keeps the v0.8 Memory Snapshot and v3 Replica contracts. Old profiles
remain explicit, and older tools refuse the fenced old content path. Cold recovery preserves logical
software backend identity, never physical inode/Node/use/online continuity. See
[ADR-0148](../../docs/decisions/0148-artifact-owner-identity-and-retained-layout.md).

[Profile v0.8](../../contracts/recovery/bundle-v0.8/README.md) adds the actual Memory Snapshot
reference producer. Explicit provider `--enable-snapshot-references` drains and upgrades original
v3 SQL to v4; default v3 remains supported by its original profiles. v0.8 retains Replica storage
v3 and the complete v0.7 original process journal contract. All source writer/inode leases precede
original command/content/kernel-budget intent and snapshot pins. Full actual bundle verification
precedes original source release. Cold restore preserves its pre-publication active intent;
explicit reconciliation can retire it using the verified original bundle, without copying.
See [ADR-0147](../../docs/decisions/0147-original-provider-snapshot-reference-lifecycle.md).

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery reconcile-snapshots \
  --profile /absolute/current-v0.8-profile.json --bundle /absolute/original-bundle \
  --manifest-digest sha256:ORIGINAL_PINNED_MANIFEST_DIGEST
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery abort-snapshots \
  --profile /absolute/original-v0.8-profile.json --destination /absolute/unpublished-bundle \
  --command-id original-backup-id
```

Reconciliation checks the exact original provider instance, operation descriptor and all original
SQL/CAS bytes. It never performs another copy or renews the original budget. An unpublished
interrupted operation cannot retry its copy; explicit abort must take every original writer lease
and durably fence the original command before retiring pins. It refuses a published/replaced
destination and preserves the partial directory. A live actual copier or provider refuses these
leases. This is a local synchronous-copy fence, not a distributed abort or device-stop proof.
Unknown or unavailable proof keeps the pins. Artifact/Raft Snapshot, event Archive, Catalog/lineage
and online cross-host content closure remain separately required.

[Profile v0.5](../../contracts/recovery/bundle-v0.5/README.md) adds the Replica database v1
storage fence and actual database-inode writer lease while retaining the complete v0.4 Memory
contract. Old Replica table names become read-only views; old binaries and profiles refuse the
new format. Restore starts no services and establishes no physical continuity. See
[ADR-0130](../../docs/decisions/0130-replica-database-writer-and-legacy-fence.md).

[Profile v0.4](../../contracts/recovery/bundle-v0.4/README.md) adds the reference Memory provider's
complete writer: root/use locks plus the actual inodes of all four managed SQL databases. Its
original Memory SQL v3 marker is mandatory; old profiles refuse this storage. Closing the main
Store does not permit a backup while an actual use/job/map connection survives. Restore preserves
every schema/row and content byte, creates private empty lease files, and starts no services. A
never-started restored tree can therefore be backed up under the same explicit contract. See
[ADR-0127](../../docs/decisions/0127-memory-provider-complete-writer-leases.md).

[Profile v0.3](../../contracts/recovery/bundle-v0.3/README.md) adds the actual `runtime-mi` SQL
writer: both `database.worker.lock` and the database inode lease are mandatory, together with the
original work instance/nonce format. Active or hardlink-alias workers refuse backup. It preserves
every original call, budget, raw response and independent observation. Earlier profile semantics
stay explicit; older tools refuse v0.3. See [ADR-0122](../../docs/decisions/0122-runtime-mi-recovery-writer-closure.md).

This administrative composition backs up the **declared stores** under their actual process
writer leases. It preserves complete SQLite tables, checkpoint/command/receipt history, CAS and
configured file/archive trees. It creates no Node, device, current-use or physical-stop evidence.
See [ADR-0113](../../docs/decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md).

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery backup \
  --profile /absolute/recovery-profile.json --destination /absolute/new-bundle \
  --command-id original-backup-id
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery verify \
  --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery restore \
  --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST \
  --destination /absolute/new-recovery-root --command-id original-restore-id
```

The [profile schema](../../contracts/recovery/bundle-v0.1/profile.schema.json) selects arbitrary
component IDs, real storage roots and their dependencies. It never supplies Node inventory.
`controller`, `node` and `mission` name their original database directly inside the root;
`artifact` and `memory` derive their maintained database/content layout. `files` covers explicitly
selected trees, or its complete root when `trees=[]`, and requires an actual writer dependency.
Node backup requires the actual v13 journal fence. Ancestor directory symlinks, managed storage
overlap, missing/cyclic dependencies and unknown original writer leases refuse before backup.

SQLite uses its backup API to include committed WAL records, then seals a standalone snapshot;
verification uses immutable readonly connections and never creates WAL/SHM in a published bundle.
All data, hashes, schemas and table identities are checked before manifest publication. An active
service rejects backup. Failures retain an unpublished `.partial-<command-id>` directory for
inspection; reruns do not overwrite it. Completed backup retry verifies its original immutable
bundle and command/profile identity, without taking a new snapshot.

[ADR-0145](../../docs/decisions/0145-recovery-active-content-reference-binding.md) additionally
checks stored active Memory references, Active Map selections and pending handoff source/target
against the same provider's declared CAS bytes during backup and verification. Missing bytes,
durable tombstones or missing mandatory reference tables reject the bundle, even when its new
manifest digest is explicitly pinned. Released references remain history and need no deleted bytes.
The check retains the original operation budget and never invents unregistered remote references.
The zero-Node `tools/quality/check_memory_reference_processes.py` gate exercises real provider/CLI
backup, restore and restart, plus independently damaged copies.

Keep the returned manifest digest outside the bundle. Restore checks every original file and full
SQL identity, creates a new private `stores/<component-id>` tree, fsyncs it and publishes an exact
receipt. Existing destinations are never overwritten. It does not start services, remap frozen
authority paths, renew deadlines or change Unknown into success. Reacquire actual deployment
configuration, certificates and current State through the normal service startup/recovery gates.
Configuration paths and external trust roots remain separate deployment inputs. A bundle of one
declared domain is not automatic online HA replication or a proof that all other domains were saved.

Replica components require explicit [profile v0.2](../../contracts/recovery/bundle-v0.2/README.md),
which acquires both the original supervisor and Raft database writer leases. The old v0.1 replica
profile is rejected. Child Controller, Artifact, configuration and credential paths remain separate
explicit components. The CLI never starts or remaps services.

Version 0.2 requires `artifact_replica_sets`, including an explicit empty list for no additional
placement. A nonempty set is an operator-selected complete 3–9-member infrastructure cluster.
Each Artifact must be a dependency of one original replica; actual locked SQL must agree on the
original cluster, application schema and complete certificate bootstrap universe. Restore copies
only manifest-verified finalized CAS blobs missing from another declared new root, preserving each
original store and every tenant/content ACL. Staging bytes are never distributed. The v0.2 restore
receipt records source, target, digest and byte size separately from original placements; retries
only inspect existing results and fail if a completed copy is missing or corrupt. This does not
create any Node/provider Memory replica or execution evidence.

The actual process check restores a completed formal two-Node file workload, compares every table,
rejects an archived old Node and a live new writer, starts the real Controller, reads authenticated
CAS bytes, and reopens the original retired provider:

```bash
uv run python tools/quality/check_recovery_bundle_processes.py \
  --from /absolute/completed-content-profile --output /tmp/rg-recovery-owned \
  --old-node /absolute/archived-pre-v13-roboguide-node
```
