# Data governance implementation ledger

This implements the user's original A–F plan on `codex/rg-data-governance`. Later assistant
drafts do not replace the authorized gates. The integration base is `7fd7b2d1`, retaining both
`f0247aa4` and `5ac441d9`; unrelated unfinished mobile work is excluded.

Latest experiment status (2026-11-10): the operator subsequently deferred the four long-term
retention/compaction items and requested continuing real RG experiments. The isolated soak was
interrupted after 1685 seconds and is not a 24-hour pass. Frozen R7 Controller/R9 MI now run an
explicit experimental restored copy on ThinkPad; the original field stores remain preserved.
Actual MI origin capture, central Memory readback/planning and original request replay passed;
navigation remains unapproved. See
[the no-motion field acceptance](acceptance/2026-11-10-dog2-mi-origin-readonly.md).

Subsequent chassis power-on check found and fixed slow sensor sampling delaying chassis
reports in the same Connector. Linux integration tests passed (67); the updated dog2 Connector
returned 23/23 valid reported-posture reads over 44.683 seconds. No Pilot task was added.
MI's current World-only grounding still excludes Node robot-state; this is an explicit remaining
integration boundary, not a completed MI state gate. See
[power-on sampling acceptance](acceptance/2026-11-10-dog2-poweron-state-sampling.md).

## Fixed acceptance contract

- Ordinary telemetry: 24-hour hot window, then 30 days from verified durable archive publication.
- Default policy is evaluation only; incomplete references or clock anomalies block deletion.
- One existing Controller maintenance worker, once per minute, at most 8192 rows / 4 MiB input.
- Original Task, Bind/Commit, allocations, cancel/stop, Unknown, external receipts and decision
  dependencies keep independent protection. Original history is not cleared to obtain results.
- Two Nodes, 30 normal restarts: readiness P95 <= 3 s. Initial migration is separately timed.
- Controller RSS <= 512 MiB; 24-hour post-warmup growth <= 5%; idle average CPU <= one-core 10%;
  control API P95 <= 200 ms, excluding model and Robonix execution time.
- Existing MI goal observer: 10-second window; two new, same-source valid reports; no action retry.
- Single-Controller release precedes replication delivery. Field release needs actual 24-hour
  stability acceptance, not merely a simulated clock or passing unit tests.

## Implementation and evidence

| Stage | Current concrete work | Remaining acceptance |
|---|---|---|
| A | Original branches merged; explicit v3 migration; recovery-tool v0.32 compatibility | Final combined migration/backup corpus and negative compatibility gates |
| B | Lossless shared values, original temporal envelopes, typed command classification | Frozen-source field-snapshot digest comparison and final performance corpus |
| C | Durable policy/receipts, bounded evaluator, admin route and existing-process worker; protected decision index; split-phase hot relocation, bounded source/body GC and transactional metrics | Permanent expiry/tombstones, complete replica/backup pin coverage, completed archive-intent compaction, journal retirement and Catalog compaction |
| D | Actual private-process v3 database accepted by new backup tooling and restored to a new directory | Final release backup/rollback corpus, 100 Node load, 30 restarts, actual 24-hour soak; then ThinkPad deployment |
| E | Restricted verifier authorization and durable MI worker implemented; preliminary actual private-process gates passed | Repeat with final frozen programs; separately authorized physical effect acceptance |
| F | Old replica/export paths refuse unnegotiated v3 | Full replicated content/retention contract and three-member fault/failover corpus |

The first combined regression before reference-index integration passed 159 State, 66 Domain and
326 Controller tests. The real private Controller/Node v3 gate passed 17 checks. These are software
results, not field or physical-effect acceptance. Local evidence is under
`C:/workspace/DEAIOS/lab/rg-data-governance-20261110/` and the task-owned WSL lab directory.
Later changes require targeted reruns before a final frozen acceptance record.

The fixed 100,000-observation repeat-value workload retained one 4147-byte value instead of
414,700,000 logical value bytes (99.999% reduction); all 100,000 original events were read back
and checked, including an epoch change. This meets the repeat-workload body-write target.
It does not claim the mixed field workload has the same ratio: its 308,169 observations retain
43,599 distinct bodies, reducing original value bytes by 83.37%. Real new values remain distinct.

The same immutable 801,957-event field snapshot migrated in 41.63 s in a bounded local release
probe; subsequent Catalog-ready recovery took 0.0182 s and read 25 protected inputs. Events,
checkpoint and 419,021 command rows retained identical logical summaries; the actual old writer
refused v3 without changing database bytes. These probe timings are not the full Controller's
required 30-restart performance acceptance. Migration leaves reusable SQLite pages; file-size
shrink is separately reported and no VACUUM has been silently performed.

## Deployment boundary

Formal long-run release remains unaccepted. A later operator-authorized no-motion field
experiment uses frozen programs against restored copies, as described in the latest status above.
No robot motion is authorized by the data-governance work.
The minutely worker must report incomplete coverage rather than infer permission from terminal
Mission counts or command-name prefixes. It cannot delete data until every required reference
source and the existing durable archive receipt contract are connected. This ledger does not
mark partially implemented stages as complete.

The evolving implementation additionally connects the minutely worker to lossless hot relocation
under an explicitly enabled, original policy revision. Default evaluation still creates no archive
intent. Its outside-writer policy witness observes committed policy through a separate readonly WAL
connection and verifies the original inode. This does not grant permanent 30-day deletion authority.
Routine State classification is limited to Node-produced Reported/Observed Node/World records;
the automatic candidate ends at the first ordinary/protected class transition. Final source-index
audit records original segment class, counts, digest and actual Controller observation time.
Storage metrics retain explicit accounting coverage and avoid full-history scans on each scrape.

## Local validation environment interruption, 2026-11-10

The local Ubuntu-24.04 WSL filesystem returned read-only and I/O errors during the content
archive process gate. Queued test/build commands after that error are not accepted evidence.
In particular, the final hot-history gap compatibility and byte-prefetch changes need reruns;
do not cite overwritten test logs as successful checks. Windows source files remain available.

The operator authorized a local WSL restart. Distribution termination and subsystem shutdown
returned success, but starting Ubuntu still returned `Wsl/Service/E_UNEXPECTED`. Restarting the
Windows `WslService` was refused by the current process's service permissions. No VHD repair,
unregister, filesystem reset or deletion was attempted.

ThinkPad is a separate field runtime. A read-only SSH check of `duchaoke@192.168.2.20` found
both `rg-controller.service` and `rg-dog2-mi.service` active/running; Controller `/readyz` was
HTTP 200 with `ready=true`, `degraded=false`, and zero Unknown attempts. The Controller process
RSS was 6,498,644 KiB, and available host RAM was 4,290,068 KiB at that observation. These are
current old-deployment observations, not performance results for this branch. No service,
database, robot command or field version was changed during the check.

The local Linux environment was chosen for isolated Linux builds and bounded history tests;
RoboGuide itself does not require WSL. The field release target remains ThinkPad. The approved
plan forbids running a second heavy comparison Controller alongside that field deployment.

## Operator-directed ThinkPad validation

The operator subsequently directed implementation and validation to ThinkPad. Source snapshot
`thinkpad-source-20261110-r1.tar.gz` contains 3192 files with individually verified SHA-256 hashes;
archive SHA-256 is `90a50db36d6205c18ebce680c6180a9845d86d900076493c6ee7b24ff60fb135`.
Its separate source directory is `/home/duchaoke/rg-lab/data-governance-20261110/source-r1`.
The deployed Git checkout and field runtime are separate and were not overwritten.

The unused regenerable `workspace/RoboGuide/target/debug/incremental` cache was removed only
after checking that no cargo/rustc/clippy or executable used that target directory. This freed
about 5.5 GiB; runtime executables, SQLite, backups, Artifact content and experiment records were
preserved. The remaining existing build cache was moved into the task-owned build target.

`rg-governance-build-r1-20261110.service` runs one-job compilation/unit checks with cgroup
MemoryMax=1536 MiB, MemoryHigh=1280 MiB, MemorySwapMax=0, CPUQuota=85%, Nice=10 and IOWeight=25.
`rg-governance-python-r1-20261110.service` uses its own test venv with MemoryMax=512 MiB,
MemorySwapMax=0 and CPUQuota=40%. These initial jobs do not launch a second Controller service.
Actual process/startup/stress acceptance requires a separate maintenance phase with the field
Controller stopped, preserving the original no-concurrent-heavy-Controller requirement.
This records a host change, not a lowered correctness, retention or performance threshold.

The operator then explicitly authorized stopping the field runtime. Before stopping, Controller
readiness was healthy, all 48 Mission lifecycle records were terminal, allocations were empty and
Unknown attempts were zero. `rg-controller.service` and `rg-dog2-mi.service` were stopped; no
physical command was sent. The build cgroup was raised to MemoryMax=8 GiB, MemoryHigh=6 GiB and
CPUQuota=300%; swap remained disabled for the job. This changed build resources, not acceptance
thresholds. The initial build-result record includes both resource periods rather than reporting
one constant limit for the entire run.

ThinkPad source-r1 passed 66 Domain, 328 Controller and 186 State tests. Its isolated Python
recovery/MI/Robonix/runtime-harness regression exited successfully. These counts apply to r1;
latest source-index retirement and archived-reference fixes are frozen in r2 and require reruns.

The quiescent original SQL/WAL and Artifact files were captured without model credentials:
11 files, 3,769,786,629 raw bytes, 516,120,551 compressed bytes. The archive was copied to the
Windows task lab and independently SHA-256 checked as
`66c2c14309ae3372af1b6858d0ebb4389c73c2295b40eef5a598273a07795533`.
This is raw rollback preservation, not a claim that the new recovery-profile restore gate passed.

Source-r2 archive SHA-256 is `0dccb170a6fb48f91663ee83421141b40121c838cd4a84c811750f4e1806cd6d`
with 3197 individually verified files. Its validation job waits for the r1 release build rather
than compiling the same target concurrently. Final program and source digests must be frozen
together before any field switch.

R2 ThinkPad verification subsequently passed the full selected Rust suite and workspace all-target,
all-feature Clippy. Python reported 1154 tests and 54 subtests passing. Frozen r1 programs also
passed the actual private posture-goal (17 checks), independent MI verifier (6 checks), and
content-archive publication/final-SQL-failure/retry/cold-restore process gates on ThinkPad.

The first 30-restart software workload used the frozen r1 programs, kept the same two Node
processes, required new Controller-received State after each readiness, and measured P95=0.03594 s.
First initialization=0.35958 s was excluded. This is preliminary program-specific performance
acceptance; the final candidate must repeat it. The harness's two earlier path-placement failures
occurred before test Controller creation and remain retained as failed setup evidence.

The original stopped field database contains 1,465,461 events and 651,747 original command rows,
with all three logical table hashes retained. Its file is approximately 3.76 GB. SQL/Artifact backup
is copied off-host, and 23 deployment/configuration files have a separate encrypted backup with
its key kept only in the local protected task directory; no credential is printed in evidence.

The latest standalone code adds transactional dedup metrics, enabled-policy-only automatic hot
relocation, native ordinary/protected classification, and original pending-operation retries.
Permanent expiry, command journal retirement, Catalog history compaction and replicated v3 remain
incomplete and must not be advertised as accepted long-run data governance.

## Frozen r5 software validation

The r5 source archive contains 3205 individually verified files and has SHA-256
`f2172c4f721d272e0af3ece3d4e2f9c1b64aefbb078377a4413b8be38af3f6c7`.
On ThinkPad it passed 220 State, 66 Domain and 328 Controller tests, workspace all-target/all-feature
Clippy with warnings denied, and 1179 Python tests plus 54 subtests. All 41 changed Python files
passed Ruff and function documentation checks on Windows; the changed Linux Catalog measurement
tool also passed strict mypy. All four release programs were subsequently built and frozen:

| Program | SHA-256 |
|---|---|
| Controller | `4da357d582a401a564ccae7790222088d6b53798ca2356c4e9ab38c8c0016d7b` |
| Node | `b8da550972a82ecfaa6cf887a7e76346b690fc1936d373820bfcab8382aae93e` |
| Catalog probe | `a26234a29e16c85b6adf4bd9214930b0b773bdb43c8e7180fbe3fb4718d50e94` |
| Telemetry probe | `6da1966ded74dcd07d8714dad4a09296227d61d558e118f3b72ce401b052197e` |

These r5 programs passed the actual 17-check posture-goal gate, 6-check MI verifier gate and
content-archive process gate. Thirty normal restarts with the same two software Nodes gave
readiness P95=0.051985 s; first initialization=0.532132 s was excluded. This restart workload is
not the full field-history Controller or the 24-hour soak. Its runtime latency sampler covers
Inventory and State GETs only; full control-write API performance remains a separate gap.

The first full-size field-clone migration was terminated by the measurement helper's fixed
180-CPU-second limit, with exit 137. Its original log, raw output, signal/RSS report and SQLite
hot journal were retained. This is a failed migration attempt, not a pass. A separate follow-up
first validates SQLite's atomic hot-journal rollback and original table hashes, then budgets the
one-time migration at 900 CPU seconds / 1200 wall seconds under a 2 GiB address-space limit.
This changes a migration resource budget, not any approved readiness/RSS/CPU acceptance threshold.
The original field database is unchanged; successful retry and negative recovery gates are still
separately required.

The final gate script freezes release program digests before discarding reproducible debug build
artifacts. It preserves field SQL, MI SQL, original deployed programs, Artifact bytes, encrypted
configuration backups and all prior failed/passing acceptance logs. The already-created independent
SQLite backup clone is reused for migration; no additional full-history comparison copy is created.
After the release build, the checked unused debug cache held 7701 files / 3.524 GiB and was removed.
Free ThinkPad volume space was then approximately 6.32 GiB, before migration growth.

Review also identified two unfinished delivery constraints. The current completed Archive journal
still retains a complete checkpoint per operation and has a 4096-operation/256 MiB limit. At one
operation per minute, the count alone can exhaust the working limit in about 68 hours; automated
hot relocation cannot yet be presented as unbounded long-run maintenance. Completion receipts need
a permanent small online index with original protected intent bytes preserved in verified archives.
Separately, the actual field Artifact root uses the legacy `blobs/sha256` layout; its complete
backup/restore compatibility must be checked explicitly rather than disguising it as a new owner.

R6 adds explicit standalone legacy Artifact recovery under the actual writer lease, with strict
CAS path/digest checks and no invented retained owner. Its source archive SHA-256 is
`3310c6df04cbb823ccb4a842b6f20d50a8ffd92b723cc06bbe362ddfbbffd73f`; 3206 files were verified.
ThinkPad passed the 21 new legacy-Artifact cases, then 1200 Python tests and 54 subtests. Rust
sources and the previously frozen r5 runtime programs were unchanged by this Python increment.

The interrupted migration's hot-journal recovery passed SQLite integrity and all original event,
checkpoint and command-row hashes, with user_version=0 and no partial v3 tables. Its subsequent
migration succeeded in 370.450 s / 347.237 CPU s with peak RSS 11112 KiB. Catalog recovery after
reopening took 0.007152 s and read 25 original protected inputs / 13920 payload bytes. This is
Catalog recovery evidence, not a full-history audit or full field Controller readiness result.

The subsequent audit failed with SQLite OutOfMemory under the unchanged 2 GiB address-space
limit. The probe incorrectly called the full replay API, which accumulates all typed events in a
Vec. A small follow-up introduces an explicit read-only streaming audit: all original SQL replay
rows still receive the original decoding/adoption validation, while only protected Catalog inputs
remain materialized. Original external-archive verification remains separate. The old full-replay
API is unchanged; neither memory limits nor integrity checks are weakened. Its added regression
also corrupts unrelated history and requires the full audit to refuse it. Linux acceptance and
the complete original-row comparison are pending follow-up evidence.

## Streaming-audit acceptance and remaining release checks

R7 freezes 3206 source files at archive SHA-256
`995be23b3499add7bc190ccc64d86450bd72c976cefaaa522a9be3d6d41741fd`.
It passed 221 State tests in release mode and workspace release all-target/all-feature Clippy.
The new frozen Controller SHA-256 is
`06913393cce331c33f5a9cef2f358b12fca798bf46c8aff2605b365574808ae4`;
the Node bytes are unchanged from r5. Its actual posture/MI/content-archive process gates passed
again, and 30 normal restarts gave readiness P95=0.053606 s.

The streaming audit traversed all 1,465,461 original SQL events / 2,838,491,360 payload bytes,
retained only 25 protected Catalog inputs, and passed the full reference-index audit in 52.651 s
with peak RSS 10088 KiB. Recomputed event/checkpoint/651747-command hashes match the original
field source exactly. The earlier OOM and terminated migration remain recorded failed attempts.

The independent 100-real-software-Node workload completed 300.000 s with zero API/freshness errors,
Controller peak RSS 101851136 bytes and Inventory/State-read P95=8.806 ms. Average Controller CPU
was 75.880% of one core under this separate 100-Node stress; this does not pass or replace the
two-Node idle <=10% CPU gate. No hardware, simulation or model was involved.

Full field-size backup/restore remains pending. Its initial preflight correctly refused SQL
sidecars (0-byte WAL and 32 KiB SHM left by a read-only connection), without deleting them. The
follow-up explicitly drains only the owned clone under both original path and inode leases,
requires SQLite checkpoint not busy, switches journal mode to DELETE and checks every logical
row/schema before and after. A subsequent cache preflight could not inspect protected user
session processes; it stopped before cache deletion or SQL replacement. Both failed preflight
records are retained; they must not be represented as passed restores.

The owned clone's page_count=1393943 and freelist_count=778021 show that successful migration
leaves reusable pages; its physical size is 5709590528 bytes. Any VACUUM uses a separately
verified owned candidate, preserves logical SQL, fsyncs candidate bytes and only then replaces
the clone. The field database is never the VACUUM target. Actual restored SQL, legacy Artifact,
MI Request database and deployment-version evidence still need end-to-end acceptance before
the authorized 24-hour two-Node soak and field switch.

## Cold-recovery run and connectivity interruption

The next cold-recovery attempt (`field-r6-restore-gate-retry2`) verified completed task units,
recorded the exact protected system-process visibility exclusions and removed only the unused
task-owned release build cache. It then drained the owned database's WAL through SQLite under
the actual path/inode leases and verified all SQL identities unchanged. The field SQL, MI Request
SQL and original Artifact root were not replacement or cleanup targets.

`VACUUM INTO` produced a 2506993664-byte owned candidate from the 5709590528-byte migrated clone.
The candidate passed the streaming audit and full identity comparison. Replacement, fsync,
complete bundle creation, new-directory restore and old-writer refusal are subsequent separate
steps; their final result has not been observed. The script preserves the old owned clone until
the exact candidate validations, durable-file sync and lease checks permit an atomic replacement.

SSH connectivity to ThinkPad subsequently failed: connections first reset/closed, then timed out
on both 192.168.2.20 and the configured 100.116.9.127 Tailscale address. The local WLAN still had
192.168.2.13; Tailscale marked SW-ThinkPad-X1-Carbon offline. This does not establish whether the
host slept, lost power or lost networking, nor whether the background recovery script completed.
Its last directly observed new evidence was the successful compacted audit and subsequent SQL
descriptor check. Reconnect and inspect the retained original phase result before proceeding.

The one-shot 24-hour wrapper is configured to wait for that exact recovery attempt's strict
`passed=true`, the completed 100-Node result and both successful R6 Python stages. It cannot call
the short runs a 24-hour pass. Its initial pre-start interruption and session-bus environment
failure were retained separately; the corrected wrapper was last observed waiting for recovery.
No 24-hour workload or field deployment is reported as started or passed. The original Controller
and MI services were last directly observed inactive under the operator's stop authorization.

The operator then reported rebooting ThinkPad, and SSH recovered. Boot identity is
`48017a69-9d4a-4f87-aa68-85e80518a839`. The original enabled Controller started automatically;
MI was waiting for Controller readiness. Both were stopped again under the existing maintenance
authorization, and the interrupted MI startup's failed status was cleared without restarting it.
Original SQL still contained 1465461 events, one checkpoint and 651747 command rows; no physical
command was issued. Both owned SQL files survived, including the candidate's empty WAL/SHM.
The interrupted run has no final phase receipt and must be resumed with fresh boot/process leases,
full candidate validation and the original frozen source/program identities. No old process tuple
or unobserved completion is accepted after reboot.

## Compacted copy and legacy Catalog compatibility

The explicit reboot-resume attempt revalidated the original and retained candidate under fresh
leases, reran the full audit and durably replaced only the owned clone. Its retained replacement
receipt records 5709590528 -> 2506993664 bytes with every SQL schema/table and all original
event/checkpoint/command identities equal. The field database was not replaced or vacuumed.

The first complete-profile backup then refused the original Catalog: 24 original v23 upload
transitions plus one v17 `MemoryManifestPublished` (`dog2-origin-field-20261106/r1`) existed with
bare CAS, no retained Catalog reference and no adoption. Production legacy Artifact mode explicitly
allows that history; the recovery tool incorrectly inferred per-publication retained ownership
from the newer checkpoint wrapper. The failed backup and partial bundle remain preserved.

The compatibility fix admits only an explicitly bound standalone v0.32 legacy Artifact store,
with an entirely unadopted Memory-only Catalog and actual copied CAS digest/length coverage.
Mixed retained references, owner-qualified dependencies, adoption, modern stores, Replicas and
bare Map publications retain their refusal paths. It manufactures no owner/reference. A realistic
25-envelope/eight-CAS fixture performs complete backup/verify/restore; ten negative cases retain
missing/corrupt/mixed/qualified evidence checks. An existing qualified-owner fixture initially
failed earlier because it copied a writer lock into its simulated immutable bundle; only that
fixture copy was fixed, retaining the original precise owner-refusal assertion.

R9 source SHA-256 is `ad5834328eaf21d39c22e315413b334092887c7d9e85bf4d8d8ffd5dcbd2ba12`,
3207 individually verified files, commit `8e00fa95`. ThinkPad passed 32 focused legacy tests,
then 1211 Python tests / 54 subtests. R7 Rust sources and frozen programs are unchanged. A new
backup-only acceptance uses these source-bound results and the exact existing compacted SQL
digest, reusing its prior full-audit proof rather than repeating compaction. Full backup/restore
and the dependent actual 24-hour soak remain pending final original receipts.

The frozen R7 repeat-value storage probes also passed 10000 / 100000 / 1000000 observations,
including exact original readback and source epoch changes. Body-write reductions were
99.99% / 99.999% / 99.9999%; subsequent Catalog recovery took 0.002663 / 0.002421 / 0.003933 s.
The million-observation probe peak RSS was 20356 KiB. These probe results are not full Controller
readiness, retention plateau or 24-hour performance acceptance.

## Complete cold recovery accepted; actual soak running

The backup-only acceptance completed successfully on ThinkPad with frozen R7 runtime programs
and the source-bound R9 recovery tool. Backup, independent verification, new-directory restore,
restored original-table comparison, original MI SQL comparison, strict actual old-writer refusal
and final bundle verification all passed. The old writer exited 1 with its original refusal
message; restored SQL SHA-256 stayed
`cdefac89bc9fc8c876ac6db00b6d5e6519d54d5bf0873b5ea27ee1c96a888764`.

The complete bundle contains 21 data/version files / 2650353482 bytes: one Controller SQL,
one MI Request SQL, eight original bare Artifact CAS files and eleven deployment/program evidence
files. Its manifest digest is
`sha256:ec53d43d613d251b11733749494a79d21d93c8fcb6bdb8708510e1a4c5397ce0`.
The transport archive includes that manifest as a twenty-second file, compresses to 415182316
bytes and has SHA-256 `1eb440f3d0537fd8b7d9e2479a984e907c747476da690bc6aaeb3341cd8508ca`.
It was copied to Windows and independently checked, including canonical manifest digest. The
separate encrypted sensitive-configuration backup remains separate; no private-key/env file is
included in the public bundle. Its release status is candidate-not-deployed, not field rollout.

After the original complete recovery receipt, the actual one-shot 86400-second two-Node soak
started around 2026-11-10 14:55 Asia/Shanghai, unit `rg-soak-24h-r9-final-20261110.service`.
The script uses the original fixed workload, 3600-second warmup, 300-second RSS windows,
512 MiB Controller RSS ceiling, <=5% postwarmup growth, <=10% one-core average CPU, <=200 ms
Inventory/State-read P95 and zero freshness/API errors. Aggregate resources are capped at 4 GiB,
two CPU cores and no swap; the private output cap remains 2 GiB. The initial sample was healthy.
This is running evidence, not completed 24-hour acceptance. The full control-write API gap,
permanent expiry/31-day plateau, journal/Catalog compaction, replicated v3 and physical-effect
validation remain explicitly outstanding. Field Controller/MI are stopped; no robot was contacted.
