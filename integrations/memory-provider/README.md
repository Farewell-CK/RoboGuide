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
default five-second/worker bounds do not establish OS-enforced CPU quota, hardware safe-state or realtime.
The HTTP compute route returns the original durable handle promptly; an owned bounded background
worker performs actual reads/hashing and the status route reports its outcome. Original computation
budgets persist before worker admission; `--max-job-seconds` is an explicit deployment bound from
1 through 300 seconds, default 5. Restart never reruns interrupted work or renews that budget.
`--max-job-rounds` separately bounds new requests (default 256, maximum 16777216); the actual
requested count is part of the immutable invocation. Tightening admission does not rerun or hide
historical results. The original computation budget still bounds actual work at every read phase.

The legacy thread profile's GET `/v1/file-jobs/safe-state` observes its worker after original cancel.
The backend retains the original canonical invocation, validated generation and actual cancel
command identity. A live worker, natural completion or UNKNOWN does not emit confirmation.
The configured State export must explicitly declare `SoftwareProcess`; these observations cannot
establish device stopping. ADR-0108's Controller handoff separately requires durable Cancel receipt,
terminal cancellation, independent current-session observation and original context/provider revokes.

ADR-0124 adds explicit `--worker-processes` alongside `--enable-file-jobs`. This mode owns one actual
OS child per computation. The parent journals the original instance/kernel budget before launch and
the PID before permitting reads. Only actual OS reaping through `Popen.poll()/wait()` supplies exit
evidence. Natural Completed/
Failed retains its outcome without inventing Cancel receipts. Node config v0.13/Contract v0.12 must explicitly
negotiate safety/generation metadata and the existing `roboguide.safe-state-observation/v0.3` export.
The default thread mode supplies no v0.3 OS process proof.

Children check absolute permit expiry after OS suspension. Each permit ends at the earliest original
compute/use deadline or 100ms; current revocation/cancellation is checked by the parent. Only verified
readonly input descriptors and control pipes are inherited. Restart cannot manufacture a missing
wait, release an uncertain reader or rerun an old job. Unverified originals retain capacity/GC fences
and `health.safe = false`. The new SQL format preserves results behind a readonly old `jobs` view
and refuses old writers; historical thread results gain no PID. Retention is bounded at 4096 process
records; State observations rotate within 60KiB, under the 64KiB limit. Polling/retention configuration
determines observation latency; this is software assurance, without a hard realtime guarantee.

ADR-0125 fixes later Tasks: each original preparation has its own import identity. Existing CAS bytes
can be verified under a new authorized receiver; another Task's import is never relabelled. Full
import/availability/use/revoke checks remain mandatory. See the
[actual process and handoff acceptance](../../docs/development/acceptance/2026-10-20-file-process-memory-ownership.md).

`tools/quality/check_memory_preparation_processes.py` uses this shipped code through actual TLS
and two formally enrolled software Nodes. It proves the explicit pre-Execute permission barrier,
actual final-import SQL failure/read-only reconciliation, current availability, two real hash
jobs, two independent verifiers and no duplicated computation or renewed budget after restart.
Schema and archived-binary checks are maintained separately. Devices, models and simulators are zero.

Config v0.11 explicitly declares current Memory-use control/query on the same private Unix backend
as original import/instance/availability. Start that profile with `--listen-unix --enable-use-leases`;
the root is private to the current user and `local.sock` accepts only the same actual peer UID.
POST `/v1/memory-use/control` persists a complete original grant or irreversible revoke;
GET `/v1/memory-use/query` returns its original receipt plus current effectiveness, without renewal.
Node and backend must share the actual Linux boot and time namespace. Namespace/boot changes and
backend startup fence old permission. Each bounded file-read/compute phase checks current use;
historical completed results remain readonly after revocation. The reference backend proves actual
file computation only. It provides no device, hard-realtime or physical stop certificate.
