# Standalone Controller recovery profile v0.32

This tool can inspect, back up and restore explicit `roboguide.controller-storage/legacy`,
`roboguide.controller-storage/v1`, `roboguide.controller-storage/v2` and
`roboguide.controller-storage/v3` declarations. Existing profile formats keep their old admissible
storage generations. A v2/v3 declaration requires `roboguide.recovery-profile/v0.32`; old recovery
programs reject this new profile and unknown storage generations are never adopted.

Recovery profile v0.32 also admits explicitly declared `roboguide.artifact-storage/legacy` for an
actual legacy Artifact component. Its original `.writer.lock`, `blobs/sha256` and `staging` layout
are retained; all finalized blob names must match their actual SHA-256 bytes. Retained-owner,
maintenance, reference-authority, reclamation or `objects-v1` markers refuse this declaration. No
owner identity, Snapshot authority or adoption receipt is manufactured for legacy bytes. The profile
must bind that component to exactly one Controller through the Controller's `depends_on` list.
Original owner-qualified Catalog/Archive references still require their actual retained owner and
cannot be satisfied by an equal legacy blob. Older profiles continue to refuse the new declaration.
For legacy storage, backup holds the real independent writer lease for the complete synchronous
copy; there is no invented retained-owner Snapshot journal. A new-directory restore preserves the
original bare blob layout and creates only the usual empty writer lock.

The historical Mission/Memory Catalog may contain only original publication envelopes: production
Catalog emits no retained-reference transition when no retained store is configured. Recovery v0.32
can preserve this unadopted Memory-only Catalog through one explicitly bound legacy Artifact and no
retained-owner Artifact or Replica. Each original manifest must match the closed Memory v0.1 shape;
every published content digest and byte count must resolve to that copied legacy CAS. The original
SQL/publication bytes remain unchanged, with no adopted owner or inferred historical tenant. Any
qualified reference/adoption still requires complete original coverage and actual owners. Bare Map
publications, mixed missing-reference history and incomplete adoption remain explicit refusals.

The profile preserves every v0.31 authority, content, Archive, parent/Root and cold-writer check.
For standalone deployments, the original application checkpoint must explicitly retain its
nonreplicated state. A storage marker never proves that a database lacks Replica ownership.

V3 public event reads install deterministic verified-body and verified-event SQLite functions on
the actual connection. Bodies retain original UTF-8 bytes, including whitespace, Unicode and float
encodings. Every body and reconstructed original envelope is checked during cold validation.
Missing bodies, checksum changes, altered prefixes/suffixes and detached journal classifications
refuse backup and verification even when SQLite's physical integrity check succeeds. V2/v3 Catalog
heads, ordered checksums, source envelopes, archive-prefix evidence and original high-water coverage
are checked before any dependent content claim is read. Catalog consumers use the checked protected
projection rather than scanning all unrelated telemetry against a small-history capacity limit.

Retention defaults, revisions, exact accepted requests/receipts and the persisted Controller clock
are original authority. The tool does not activate policies or reset missing policy history. The
raw SQL snapshot includes all of those tables and every body/reference. Event ID/sequence, task,
attempt, authorization, allocation and Artifact identity are not regenerated during restore.

The v3 telemetry metrics singleton is also mandatory authority. Cold validation checks its closed
schema, independent checksum domain and cumulative-counter relations, then independently counts
current hot events, observations, bodies, body bytes and original high-water. Metrics retain an
explicit accounting-start sequence: an archived prefix predating v3 is coverage, not fabricated
lifetime traffic. Hot/body retirement reduces current gauges while preserving cumulative totals;
zero hot rows cannot erase the original append high-water. Corrupt or absent metrics refuse restore
instead of rebuilding a plausible zero head from current data.

Decision-input reference coverage is mandatory in v3. Recovery checks the original index head,
counts and ordered edge/gap checksums and resolves each dependency to its original observation and
decision receipt. It compares complete native records with strict JSON value types, preserving
epoch, sequence and original clocks; `true` cannot replace the measurement `1`. Rust's typed record
digest remains opaque to Python, avoiding a cross-language float reserialization rule. The original
event and body byte hashes provide independent integrity. Unresolved dependency gaps and an
unindexed older archive prefix remain explicit unknowns and cannot authorize cleanup. No extra copy
of each multi-kilobyte telemetry value is stored in the reference index.

When a referenced observation or decision has moved out of hot SQL, its original source is resolved
through the committed Archive manifest, first intent and completed publication receipt. The exact
immutable `events.jsonl` segment and publication metadata are checked before matching the original
decision input. Bundle verification binds each original Archive destination to the declared copied
Archive component; it cannot silently read a surviving live deployment to fill a missing bundle.
Missing files, unpublished operations and altered receipts remain errors. Body removal after a
fully verified Archive publication therefore does not make otherwise complete cold recovery fail.

The source-identity index is an accelerator rather than a second copy of all historical telemetry.
Ordinary, expired, unreferenced source identities may be pruned by the original writer only after
the exact archive receipt and dependency checks succeed. Recovery validates the retained source
count against the checksummed head; it does not recreate pruned ordinary entries. A retained
protected decision edge still requires its exact source index row and original archived payload.
Deleting that source cannot be excused by merely updating the count. A later decision that cannot
prove an old source remains an explicit gap rather than acquiring fabricated evidence.

Operator authorization v0.3 or a `verifier` grant requires both recovery profile v0.32 and the actual
Controller checkpoint v60. A verifier remains tenant scoped; global delegation and expiry/revocation
must retain their original meaning. The tool creates no current TLS permission and sends no task.

Use the normal complete topology profile, with each actual Controller component's `storage_schema`
set to its **current** generation. Keep the profile, source SQL, Artifact owner metadata, archives and
all other dependencies together; a Controller-only fixture is not a complete field-deployment
backup. Sensitive credentials must remain outside public evidence and use the deployment's
separately encrypted credential backup.

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery backup \
  --profile /absolute/complete-v0.32-profile.json \
  --destination /absolute/new-backup --command-id original-backup
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery verify \
  --bundle /absolute/new-backup --manifest-digest sha256:ORIGINAL_MANIFEST_DIGEST
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery restore \
  --bundle /absolute/new-backup --manifest-digest sha256:ORIGINAL_MANIFEST_DIGEST \
  --destination /absolute/new-restored-directory --command-id original-restore
```

Backup takes every declared original writer lease before the SQLite backup API captures SQL/WAL.
Restore writes a new directory, validates original content and returns a v0.32 receipt; neither
operation starts services. A retry uses the original command and receipt. Changed command identity,
missing Archive content and incomplete bundles continue to refuse publication.

Migration is performed by the compatible Controller's explicit storage selection, under its
exclusive SQL writer, after a complete verified pre-migration backup. There is no Python migration
that guesses Rust/domain types. First validate the migrated **copy**, recover it to a new directory
and use the compatible program for startup checks. Do not delete `user_version`, rename generation
tables or run an old program against the new database. A rollback after new tasks requires keeping
the new data and a compatible program or forward repair; an old snapshot cannot replace new work.

Software acceptance is independent from ThinkPad deployment, 24-hour stability and physical goal
verification. Replicated v3 publication, promotion and content transfer are not established by a
successful standalone backup.
