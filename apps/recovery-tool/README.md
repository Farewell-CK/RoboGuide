# RoboGuide recovery tool

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
