# Local immutable-file Memory provider

This deployment adapter stores actual immutable files in a private CAS with a SQLite WAL original
operation ledger. It has no Mission, Control, Runtime, robot or localization authority. The provider
ID, roots, size bounds and publish-eligible exports are deployment inputs.

Run with Python 3.11+ on a POSIX host:

```sh
PYTHONPATH=integrations/memory-provider python -m roboguide_memory_provider \
  --root /absolute/backend --provider-id file-store \
  --input-root /absolute/node-staging --listen-port 8120
```

The listener requires a literal loopback address. The trusted Node/local-process boundary owns
authentication to this local adapter. Remote content access continues through the authenticated
Artifact service and Controller authorization. The local facade exposes fixed routes:

| Route | Effect |
| --- | --- |
| GET `/v1/instance` | Read the actual persistent backend UUID/provider |
| GET `/v1/import-query` | Read original durable operation metadata and rehash current bytes |
| GET `/v1/availability` | Prove current original-backend bytes independently of Imported history |
| POST `/v1/import` | Journal exact original identity, copy/hash/fsync bytes, then record completion |
| GET `/v1/discover` | Return only explicit configured publish-eligible exports with verified bytes |
| GET `/v1/export` | Resolve only an exact configured immutable manifest to its controlled handoff path |

The GET query/import routes receive bounded JSON through configuration-owned Node workflow
bindings. Import requires `memory_import`, `memory_manifest`, and `staged_artifact`; query requires
the original `memory_import` evidence. Configure v0.8 continuity with instance `/instance_id`, query
`/result`, and import workflow result `/steps/<configured-import-step>/result`.

Optional `--export-config` and `--export-directory` are paired. The config is a bounded JSON array
of `{ "manifest": <complete immutable manifest>, "artifact_path": <relative path> }`. Node provider
`storage_directory` is that controlled handoff root. Discovery is explicit publication authority,
not a scan of all backend Memory. Every export rechecks actual bytes against its manifest.

Import publication uses fsync plus atomic nonoverwriting hard-link publication. A failure after
file publication and before final SQL update retains pending metadata. For this file backend the
complete local semantic effect is the original durable manifest plus exact immutable content;
readonly query can prove that effect from those real files without updating SQL or copying again.
Missing/currently corrupt bytes remain unknown. The same import cannot copy again, even after its
content is removed. There is no automatic GC or repair by reimport in this slice.

`tools/quality/check_memory_processes.py` drives the shipped backend, Controller and Node binaries,
injects an actual final SQL commit failure, queries through the formal protocol, verifies reboot
budgets and an actual old binary's journal refusal, and independently hashes imported-file output.
Its result is software process evidence with zero devices.

Config v0.10 can additionally declare the fixed read-only availability route. Its response binds
the original import, actual persistent backend instance, immutable workflow/manifest/content
digests and byte size. Deletion or same-size corruption returns unavailable without rewriting
historical import state. Prepared/Imported alone cannot authorize Execute.

Optional `--enable-file-jobs` exposes actual bounded CPU hashing through `/v1/file-jobs/health`,
GET `/v1/file-jobs/status`, POST `/v1/file-jobs/compute` and POST `/v1/file-jobs/cancel`. Jobs read
only explicit startup exports or exact previously admitted provider inputs, retain once-only
execution IDs in SQLite, independently hash actual opened regular files, and preserve interrupted
outcomes as UNKNOWN. Cancellation acceptance is distinct from observed cancellation; shutdown
requests cancellation then joins actual HTTP workers before closing storage. The cooperative
five-second/worker bounds do not establish OS-enforced CPU quota, hardware safe-state or realtime.

`tools/quality/check_memory_preparation_processes.py` uses this shipped code through actual TLS
and two formally enrolled software Nodes. It proves the explicit pre-Execute permission barrier,
actual final-import SQL failure/read-only reconciliation, current availability, two real hash
jobs, two independent verifiers and no duplicated computation or renewed budget after restart.
Schema and archived-binary checks are maintained separately. Devices, models and simulators are zero.
