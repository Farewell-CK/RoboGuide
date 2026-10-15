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
