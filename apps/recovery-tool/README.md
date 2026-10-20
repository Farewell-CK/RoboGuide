# RoboGuide recovery tool

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
