# Repository Guidelines

ADR-0160 adds explicit content SQL v3/layout v0.3 and parent Raft storage v5/schema v0.5.
Actual Log/View/Snapshot intent and complete pins precede effects; original SQL inode/process/budget,
publication, actual scope close, independent observation and process absence remain separate.
Mandatory local heads commit with parent metadata; missing heads/rows refuse before retirement.
Original IDs never repeat uncertain effects, and Snapshot never imports member-local authority.
Opted-in heavy asynchronous work owns the real leases/queue budget inside bounded blocking workers;
wait cancellation cannot close them. The 39-process gate is check_content_archive_processes.py
--original-producer-journal with its explicit Snapshot probe. Owners are content_store/producers.rs,
store_producers.rs, store_apply.rs, store_snapshot.rs, store_work.rs and replica_producer_processes.py.
Actual legacy v2 upgrade preserves raw rows and unavailable original budget/source; parent v4 is not
implicitly upgraded. Full original deployment adoption/migration, network same-voter ACK, reader Drop
and other N7 paths remain mandatory. No device/physical/online completion is implied.

ADR-0159's private application v3 and explicit direct-storage v4 retain the complete original candidate,
recomputable batch digest, original bare-event owner bindings and original State recovery projection.
Metadata-only commands/opens refuse it; automatic v0.3 upgrades and independent prefix compaction are
refused. Log append, current view, Snapshot publication/install and each actual SnapshotData reader
have separate pins. Missing actual target bytes refuse Snapshot build/install; stale Snapshot cannot
regress accepted commands, originals or floors. `content_snapshot_probe` plus the zero-Node Archive
gate's `--replica-snapshot-probe` exercise real original Controller uploads and direct storage/owned
processes, never an actual network quorum or leader authority. Content layout v0.2/SQL v2 adds explicit
retention-only scopes; actual v1 producer upgrade preserves raw rows/owner/budgets and fences old
binaries. Identity v0.1 remains original. Owners include content_application.rs, store_content.rs,
snapshot_data.rs and content_store/pins.rs. Production ReplicaConfig v0.3 still rejects v0.4. Real
mTLS byte transfer/same-voter ACK, storage producer interruption reconciliation, complete online
Archive/GC/recovery/upgrade and Node/Mission routing remain mandatory; N1/N3 are not complete.

ADR-0159 is implementing original-owner content durability for the future explicit Replica v0.4.
State's `replica_recovery.rs` now exports original auxiliary recovery under the actual original
writer and atomically imports it beside complete events/commands/checkpoint. It retains raw original
Archive ID, zero-operation journals, pending copy/abort, Catalog prefix and manifest/floor evidence;
modern metadata-only materialization refuses before writes. Explicit new ports receive this complete
projection; old v2 ports never acquire it implicitly. The actual original v0.3 zero-Node certificate
process gate reproduced a missing Archive marker after peer restart at the 5e1d307 baseline. The
full State API's SQL tests do not close its transport/Replica storage/Snapshot/content wiring.
`core/state/examples/replica_state_probe.rs` and the owned zero-Node Archive check's optional
`--replica-state-probe` retain actual capture/import PID/wait, original metadata identities and
Controller/CAS reopen evidence. The helper is `tools/quality/replica_state_materialization_processes.py`;
it uses the actual same original CAS and does not prove online replication or fix the product's
separate hardlink/writer-generation gap. See the complete-original-State acceptance.
`core/controller-replication/content.rs` validates same-member complete metadata/byte majorities
including joint configurations; DTOs alone are never transport or storage evidence. The new
member-local `content_store/` retains actual root/database-inode leases, mandatory bounded SQL
heads, qualified original owners, copy intent/receive pins and managed reader pins before I/O.
Actual file closure and separate original-process absence release only their own pins; interruption
keeps Unknown and never invents a byte/OS-wait/device receipt. Current production v0.3 still
replicates metadata only. Real mTLS/admission, full Catalog/Archive/Snapshot, old-profile upgrades,
configured Node/Mission routing and all remaining P1-P7 requirements are not closed by this backend.

ADR-0158 adds `retire-provider-snapshot` for the original unpublished provider copy after relocation
or profile change. Complete original/current profiles and all actual root/use/four-SQL-inode writers
precede exact original instance/request/pin checks. Only the current provider's original v0.1 abort
receipt and generation-1 pins change atomically; the separate historical source, partial files and
original budgets stay. Released/published copies require original publication proof. SQL failure,
missing/changed source or occupied writers retain pins. Memory SQL v4/writer v0.2, wrapper v52 and
Node contracts stay; this reuses ADR-0147 authority. Recovery directory validation accepts only
explicit SQL/tree structural ancestors, refusing added siblings after manifest repinning. Owners are
recovery/provider_snapshot_retirement.py and the native
check_snapshot_reference_processes.py --relocated-abort gate. It uses actual provider/copy/wait/
cold restore and creates zero Nodes/devices/models/simulators. Raft/online producer, hot-journal
maintenance and all remaining P1-P7 requirements still apply.

ADR-0157 adds explicit source upload retirement under wrapper v52/event v31 and offline source
reclamation under Artifact storage v5/recovery v0.16. Original Completed uploads and Catalog/lineage
are independent claims. All actual original writers and every known Controller authority precede
the bounded complete reference check, original GC intent and irreversible tombstone, before unlink.
Same-command retry only observes; a new explicit command may handle remaining bytes without
rewriting earlier Unknown. SQL DELETE-header/mandatory heads/rows and actual read/finalize fences
survive cold recovery; released history needs no deleted bytes. Current v0.16 artifact components
declare actual storage v3 or v5; v4 stays terminal private Archive. Runtime MI declarations require
explicit original-policy revocation, terminal Mission, closed switches and independent original-goal/
kernel completion evidence before Catalog retirement. Old-profile Snapshot retirement retains both
complete original/current profiles and actual writer evidence without copying or rewriting source
history. Owners are artifact_http/upload_retirement.rs, core/state/upload_retention.rs, Artifact
layout/reclamation.rs and recovery's artifact_* reclamation/reference modules. The native gate is
check_artifact_reclamation_processes.py with actual adoption or completed MI inputs; it creates no
new Nodes/devices/models/simulators. Replica/online authority is refused. Raft/online producers,
legacy provider Snapshot closure, hot-journal maintenance and complete P1-P7 remain required.

ADR-0156 adds explicit tenant Artifact-admin adoption of an existing unaccounted publication under
wrapper v51 / codec v30 / recovery v0.15. Actual original raw envelope/metadata and same-owner CAS
are verified; typed parents require their own original references. Complete current stable admin,
TLS/grant/command and immutable original publication commit atomically with Catalog/lineage pins.
Historical publisher/tenant remain unavailable, never filled from current identity. No read/use/import
permission is added. Original retries retain time/evidence; missing same-transaction adoption receipt
or changed raw source refuses replay/recovery. Archive preserves complete envelopes. The actual old
v50 file producer plus current mTLS/SQL/rollback/restart/cold gate is check_catalog_adoption_processes.py;
CI separately uses the explicit legacy storage profile without claiming an older binary. Node/Memory
contracts stay. Source Artifact full-reference GC, Raft/online and all P1-P7 requirements still apply.

ADR-0155 adds offline private Archive-recipient retirement and explicit GC under recovery v0.14.
All original writers and the actual recipient descriptor precede a sole-producer/empty-consumer
check, complete original compact/publication proof and nonrenewable OS-admin retention intent.
Durable tombstone and terminal storage v4 seal precede unlink; v4 is not an ordinary writable CAS.
Independent original GC intents retain their own actual writer/process/inode evidence. Failed
publication or partial unlink remains Unknown; retries only observe, new explicit GC may handle
remaining bytes without rewriting earlier results. Mandatory command heads prevent lost rows from
becoming empty authority. v0.14 cold recovery requires original terminal metadata but no already
deleted bytes; older profiles/binaries refuse. Actual native retirement/GC/cold acceptance passes in
check_archive_recipient_processes.py; no Nodes/devices/models/simulators are created. Source Artifact
full-reference GC, other producer release, legacy Catalog, Raft/online and all N2-N7 remain required.

ADR-0154 adds explicit original unpublished-Archive cleanup under wrapper v50 / operations v0.2
and recovery v0.13. Migration validates v49 history without changing its original embedded
checkpoints, bytes or budgets. Cleanup has its own nonrenewable intent; actual held source/Archive
writer descriptors, current kernel/PID/start ticks precede durable tombstone, which precedes the
exact source SQL receipt. Expired cleanup remains Unknown; a distinct explicitly admitted cleanup
cannot renew or repeat a copy. Original aborted copy IDs never execute again. Final publication
refuses abort; partial files and receiving Archive references stay. Missing mandatory journals or
tombstones refuse recovery. Recent wrappers v45-v49 share the original v45 isolation semantics,
while their independent storage authorities remain strictly validated. Actual gate is
check_content_archive_processes.py --abort-unpublished, optionally --upgrade-original with a pinned
prior Controller binary. Actual upgrade/abort/file/Catalog recovery acceptance passes; see
acceptance/2026-10-22-original-archive-abort.md. Receiving Archive retirement/GC,
Raft/online producers and the full remaining P1-P7 requirements still apply. Software writer
fences do not establish a child exit, Node/device stopping or physical continuity.

ADR-0152/0153 introduce original content Archive command v0.2, shared wrapper v49 and explicit
Artifact storage v3. Original State-store identity/journal are mandatory even at zero operations;
accepted loss refuses replacement. CAS v3 retains every known Controller reference authority.
State's actual Archive intent/pins precede content effects; publication verification precedes the
atomic Catalog recovery/history-floor/exact-command compact receipt. Original retries inspect only;
receiving Archive references retain bytes independently. Recovery v0.12 / bundle v0.4 freezes all
original Controller/CAS/Archive writers, includes all known consumers, and preserves mandatory
nested empty directories. Old formats refuse actual v3 owners. Owners are State's archive modules,
application/archive_content.rs, Artifact layout/authorities.rs and recovery/archive_references.py.
The actual zero-Node gate is check_content_archive_processes.py. Actual file Catalog replay and
original cold recovery gates also pass; see acceptance/2026-10-22-original-content-archive.md.
Archive GC, Raft Snapshot/online producers and all remaining P1-P7 requirements remain mandatory.
No software root, process or backup identity establishes devices or physical continuity.

ADR-0151 adds explicit Artifact storage v2 with the same original owner/inode identity, a persistent
upgrade intent and mandatory member-local maintenance journal. Old v1 formats refuse it. Recovery
profile v0.11 / bundle v0.3 produces immutable original-owner Snapshot intents before actual copy,
retains kernel/UID/nonrenewable budgets, and verifies every active journal input against same-owner
bundle CAS. Original IDs cannot repeat uncertain copy; readonly reconcile copies no files, exact
verified publication retires pins, and all-original-writer abort retains partial evidence. Restored
history keeps the original active intent. Native gates are `check_artifact_snapshot_processes.py`
and `check_artifact_owner_processes.py --reference-journal`; the latter uses actual mTLS/Controller/
old-binary/cold restore with zero Nodes. Archive/Raft Snapshot/online producers, GC and full P1-P7
remain required until separately implemented and validated; no process proof establishes devices.

ADR-0150 connects the existing validated Node reader to the bounded application batch with at most
eight in-flight facts per actual session. Submission and ACK preserve original order; every ACK
still requires actual application Commit. Original receive time and the first enqueue completion
deadline survive subsequent polling. First rejection/unavailability/timeout ends the route; pending
facts remain uncertain rather than being declared unexecuted. Registration and topology barriers,
source TTL, Memory budgets, Node contract and independent stop proofs retain their meaning.

ADR-0149 connects actual retained-owner Catalog publication and application handoff/MI child
publication to original content references in the same writer/SQL authority transaction. Shared
wrapper v48 / event codec v29 retain tenant/principal/authority/generation/time, separate actual
CAS owner/provider/manifest/content identity, and each child's independent parent lineage. Spatial
dependencies keep complete canonical original metadata rather than only its discovery projection.
Explicit tenant-admin retirement requires exact original record and original application context/
provider revoke proof; Unknown retains ownership. Retry never renews acquisition or retirement;
retired selectors cannot publish or prepare/import anew, and historical cache replay never revives
them. Recovery v0.10 checks complete original reference/publication history and every active owner/
CAS dependency in one quiescent bundle; released history requires no deleted content. Native gates
extend the actual file-process profile with `--artifact-retention` and
`check_catalog_reference_recovery_processes.py`. These are software/file/process proofs only;
complete Archive/Raft Snapshot/online producers and P1-P7 remain mandatory.

ADR-0148 adds explicit Artifact storage v1 via `ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA` or
`FileSystemArtifactStore::new_with_retention`. The original root writer precedes OS-random backend
identity/actual directory-inode intent, atomic `blobs` to `objects-v1` migration, regular-file old
path fence and original owner publication. Missing/conflicting identity refuses replacement; default
legacy remains explicit. Operator-bound `GET /v1/artifact-store` reports loaded software owner only;
it grants no content/tenant/device authority. Recovery v0.9 preserves exact owner/fence/CAS and
v0.8 Memory Snapshot/v3 Replica semantics; old binaries/tools refuse the fenced old path. Native
`core/artifact-store/examples/layout_probe.rs` and
`tools/quality/check_artifact_owner_processes.py` exercise actual bytes/Controller/mTLS/old binary/
old CLI/cold restore with zero Nodes. This supplies the actual owner boundary for later Catalog/
lineage/Archive/online references; it does not itself close those producers or P1-P7.

ADR-0147 adds opt-in provider `--enable-snapshot-references` (Memory SQL v4 / writer v0.2)
and explicit recovery v0.8. Actual all-root/inode leases precede a unique original command,
provider-instance/content set and kernel-budget intent plus snapshot pins, before any copy.
Verified original publication precedes exact retirement; interrupted copy cannot repeat or renew
its budget. Reconciliation verifies original bundle bytes and never copies. Explicit abort takes
all original leases, durably fences the synchronous copy and preserves partial evidence; it is
not a distributed transaction abort or physical proof. Restored SQL retains its original active
intent until explicit proof-based reconciliation. Default v3 stays unchanged; old writers/profiles
refuse v4. Owners are `apps/recovery-tool/roboguide_recovery/snapshot_references.py` and the actual
zero-Node gate `tools/quality/check_snapshot_reference_processes.py`. Artifact/Raft Snapshot,
Archive/Catalog/lineage, online replication and the rest of P1-P7 remain separate required work.

ADR-0146 makes the actual original import journal produce a provider-local Replica reference in
the same transaction as intent, before opening the source file. The reserved owner hashes the full
original command/provider instance/preparation; its one immutable import has generation 1. Restart
rebuilds missing projections without reactivating released rows. ContentReclamation consumes exact
irreversible original-provider revoke before retirement; workers/maps/other refs remain independent
gates. This is an existing-v3-authority projection, not a new read grant or cross-owner completeness
claim. See `integrations/memory-provider/roboguide_memory_provider/replica_references.py`.

ADR-0145 verifies stored active Memory references, Active Map refs/heads and pending handoff
source/target against exact CAS bytes in the same quiescent recovery bundle. Missing content,
tombstone conflicts and absent mandatory reference tables refuse backup/verify before publication;
released history needs no deleted content. Scans retain original operation bounds. The actual
provider/CLI/restart gate creates zero Nodes/devices and does not establish global reference
coverage, cross-host content replication or physical continuity.

ADR-0140 connects Control's durable tenant/category quota ledger to normal resource Commit, Mission
Group Commit, recovery Commit and exact Task/Context/Group/recovery release. Claims use original
Role requirement units and immutable Mission tenant. Recovery-admin configures the revisioned policy
through `/v1/resource-quotas`; wrapper v47 checkpoints it with a fail-closed legacy-unaccounted
reservation fence. Over-limit Commit changes neither reservation nor event evidence. This remains
logical accounting over exclusive Control reservations; it does not divide Node resources or assert
GPU, OS, physical, realtime or device enforcement.

ADR-0141 advances the quota read-only view to v0.2: it reports both configured limits and every
existing tenant/category claim, with absent limits and remaining capacity represented as `null`.

ADR-0138 adds the member-local original Controller process journal under Replica storage v3 and
explicit recovery v0.7. Real supervisor spawn intent precedes the actual Child PID/boot/start-tick
registration; only that incarnation's actual wait result becomes exit proof. Transfer fences source
application authority, waits for original durable exit, rechecks original quorum/budget, then sends
TransferLeadershipV3 with exact proof and a receive-relative target budget frozen before queuing.
Unreaped/spawning history survives restart and blocks replacement; old v2 operation values remain
legacy evidence and do not invent drain authority. No application checkpoint/Node contract changes
are implied. This proves infrastructure process exit only, never device or Local EAIOS stopping.

ADR-0139 binds deployment CPU/address-space declarations to each original Local EAIOS worker row.
The actual child must report exact `getrlimit` values and PID; the parent persists that receipt
before the first read permit. Health reports configuration scope; `/v1/file-jobs/quota` is the
read-only per-execution declaration/proof view. File process SQL v2 migrates prior v1 rows with
quota evidence unavailable and rejects old thread writers. The maintained actual-process checker
independently reads SQLite; no device, Node, hard-realtime, GPU or Control quota facts are created.

ADR-0137 preserves the exact relation target when admitting safety stop. Existing admit already
covers all current live Group peers plus that target, including locally Completed without relation
proof. Ordinary Group stop selects one live entry and delegates once. Synthetic contract tests
cover live/all-terminal source cases, original ownership/result and nonrenewing replay; these never
create deployment facts or prove device stopping. No checkpoint/Node contract changes are implied.

ADR-0136 retains bounded member-local transfer history under Replica storage v2 and explicit
recovery v0.6, preserving original IDs and fencing v1 binaries. Raft snapshots never import or erase
this operational authority. TransferLeadershipV2 rejects old target commands; loss/cancellation
remains Unknown and duplicate original IDs never send another election. Completed retains the
target's actual newer linearizable epoch; the source obtains the exact proof over authenticated
readonly observation. Current source application fence/commit is refused while pending. Legacy
weak completion migrates to Unknown with raw evidence preserved. This still requires independent
source-child stop/wait, online content and cross-domain recovery evidence before full HA completion.

ADR-0130/0135 bind Replica storage v1 to the actual database inode plus sibling writer lease.
Migration first validates original cluster/schema and drains legacy SQLite connections using native
DELETE-journal/exclusive locking. Complete tables, exact readonly legacy views, marker and absence
of writer triggers are checked; missing identity never bootstraps replacement authority. Explicit
recovery v0.5 retains the full v0.4 Memory contract and revalidates Replica fences during immutable
bundle verification/restore. The maintained storage_lease_probe and process checker create zero
Nodes/devices. ADR-0131 transfer intent tests do not prove source-child drain, response-loss recovery
or full HA; these remain required alongside online content and cross-domain recovery.

ADR-0129 adds the closed deployment-profile v0.1 and readonly inspector under tools/deployment.
It accepts arbitrary declared components/process groups/hosts/domains, pinned inputs and contract
support, unique store owners, actual TLS leaf pins, fault targets and explicit criteria. Zero Node
is valid; no device or capability inventory is built in. Actual TLS/GET probes do not Register or
Execute, and raw loopback HTTP stays in the same declared host/domain. Receipt file integrity,
declarations, local observations and pending hardware remain separate; neither a JSON passed flag
nor an RTO/RPO target grants authority or announces SoftwareComplete. The actual zero-Node gate is
check_deployment_profile_processes.py. CI now includes it, maintained Python scopes and real Python
3.9 standard-library bridge compatibility, without starting Habitat or a model.

ADR-0128 explicitly supports canonical MissionPlan v0.9 in the provider DTO while preserving the
default v0.8 and its v0.7 output compatibility. A v0.9 output requires the exact selected v0.9
schema; Memory prerequisites cannot disappear through compatibility normalization. Canonical,
Catalog, satisfaction policy and Controller gates still apply. The actual `revision-mi` process
case composes original Request before execution, a native failed-child wait, nonempty original
Memory declarations, actual interrupted SQL workers, independent Review, normal Control/import/use
and independent Task/original-goal proofs. The local Responses endpoint is an explicit protocol
fixture; external models and devices remain zero. This does not close all F0/F4-F10 requirements.

ADR-0127 gives the reference Memory provider one root/use/four-database-inode writer lease,
retained until every actual managed SQLite connection closes. Native journal transitions drain
legacy connections before upgrade. Memory SQL v3 preserves original instance/history/budgets;
old writers refuse it. Explicit recovery-profile v0.4 validates its marker and freezes all these
leases, preserving all four SQL stores and declared content. Missing v3 managed files refuse
without implicit repair. No service starts during restore; software storage facts confer no Node,
current-use, device-stop or global HA authority. See the 2026-10-21 writer/recovery acceptance.

ADR-0126 adds explicit runtime-plan-revision v0.3 original-owner Memory declarations before
candidate output/provider claim/Review. The complete child and source membership/policy/principal
are immutable; Review includes their digest. Original complete stop/revoke and actual CAS verification
precede publication and normal Control creation of the new Group. Staging grants no resource or
availability fact. Per-chunk parent-reference exceptions require the actual child import; each later
Task retains its own preparation/import. At ADR-0138, shared wrapper v45 fenced the prior writer
generation and v0.3 receipts, including zero-event declarations; v44 generation history migrated
unchanged. Node Contract v0.12,
config v0.13/catalog v0.15, Node journal v13, Bridge v23 and event codec v28 retain their meaning.
`tools/quality/memory_revision_processes.py` extends the real file checker with revision, SQL rollback,
confirmed-stop restart, policy and expiry cases. `check_revision_memory_schemas.py` validates their
actual retained commands offline. These are software-process/file proofs, never device stopping.

## Project Structure & Module Organization

ADR-0124 adds explicit Local Memory `ProcessFileJobs`: real owned CPU children, original kernel/use
budgets, PID-before-read, expiring read permits and durable actual OS wait evidence. The process SQL
marker/readonly old view fences thread writers; history gains no process proof. Restarted uncertain
readers retain capacity/GC fences; observations rotate below the State payload limit. Existing
Node v0.12/config v0.13 and safe-state v0.3 are explicitly negotiated; this never proves device safety.
ADR-0125 binds imports to the exact original Task/attempt preparation. Another Task's Completed
import is placement history, not a new use grant. Full requests must match before receipt reuse.
Actual gates are `check_file_process_workers.py` and
`check_memory_preparation_processes.py --process-workers`; both create zero device facts.

ADR-0123 adds bounded read-only runtime MI discovery from committed current Generating tickets,
filtered by actual TLS stable principal and exact tenant. Mission's unique-index/tenant JOIN resolves
the original Request without deliberation. Worker v0.2 explicitly opts into paging; v0.1 retains
static IDs. Listing never creates permission, renews budgets or bypasses original source/claim/nonce/
Review/stop/revoke/Control gates. No authority checkpoint or Node contract changes are implied.

RoboGuide has a current V2 architecture baseline plus development and MVP documents.
The first core bootstrap has started; the full runtime and MVP are not complete.

ADR-0122 adds explicit recovery-profile v0.3 `runtime-mi` stores, taking both the actual worker
sidecar and database inode leases and checking original instance/nonce storage markers. Actual active
and hardlink-alias workers refuse backup. Complete Controller/Node/Request/MI SQL survives cold
restore without renewing budgets or changing uncertain outcomes; restore never starts a service.
v0.1/v0.2 keep their original semantics, and old tools refuse v0.3. See
`tools/quality/check_runtime_mi_recovery_processes.py`; this is declared-data recovery, not online HA
or physical continuity. Full F0/F4-F10 requirements remain mandatory.

ADR-0121 adds explicit runtime-plan-revision v0.2 generation delegation, immutable SQL worker claims and
one original provider nonce per phase under shared wrapper v44. Actual fault/hold/complete stop/revoke
precedes MI work; SQL intent precedes provider/HTTP I/O. Replaced or regressed worker stores cannot
repeat original provider phases. Original code/config/Catalog/Prompt/dependencies, raw responses and
kernel budgets remain durable; interrupted HTTP outcomes stay uncertain beside independent readonly
observations. Models offer candidates/advice only; separate actual Review and original Control/goal
gates remain mandatory. The worker has explicit mTLS peers and bounded configured Request IDs, with
no fabricated devices. Legacy Controller nullable export conversion preserves raw source and authority
digests. `tools/quality/check_plan_revision_processes.py --automatic-generation` uses an explicitly
labelled protocol fixture and actual Request/SQL/TLS/Node/CPU processes, never external model inference
or physical evidence. See `docs/development/runtime-mission-intelligence-v0.2.md`; automatic discovery,
scoped Memory transfer and the complete F0/F4-F10 matrix remain required.

ADR-0120 originally added Orchestration plan history under wrapper v43/inner v23/event codec v28. Runtime revision
requires an independently frozen original Mission goal/source; all Task completion alone cannot
complete that Mission. Source Group retirement preserves completed facts and Runtime history, and
permanently fences its Execute outbox. Actual original stop/revoke/current ownership and nonrenewable
same-kernel budgets precede a fresh Control Group and normal Match/Commit/Bind. Pending revisions
independently fence resume, dispatch and recovery; explicit Mission cancellation closes their retained
history in the same authority transaction. New policy/work/Review checks the actual peer leaf under
the original writer; stable-principal rotation cannot lend another leaf's permission. Original grant,
leaf, goal receipts and decision clocks survive restore. Software-process proofs never establish
device safety. See `docs/development/runtime-plan-revision-v0.1.md`; current wrapper v44 retains actual
v43 history without renewing original permissions or budgets. Scoped Memory transfer and complete
cross-stage conformance remain required alongside the other F0/F4–F10 work.

ADR-0119 bounds Operator transports before TLS (default 128, explicit 1–1024), returns the network
worker before each complete request poll, and bounds response I/O without cancelling committed work.
Unchanged complete committed grants have a read fast path; actual mutation witnesses still recheck
current authority under the original writer through Commit/rollback. Registry/identity/attestation,
verifier, Memory and Mission candidates release live Controller mutex during work and SQL; metrics
query storage after a short committed snapshot. Replica child connection environment remains
policy-bound. Sampled active/limit and actual rejection counter are observation-only. See
`docs/development/operator-network-bounds-v0.1.md`; other listener budgets, long-term SLO and all
remaining F0/F4–F10 requirements remain mandatory. No Node or physical facts are created.

ADR-0118 returns the Tokio network worker before complete timer/Node-fact/Artifact synchronous
authority, audit, SQL and content work. Original writer/TLS witness/Commit/ACK/outbox ordering and
budgets stay. Explicit replica child TOKIO_WORKER_THREADS is limited to 1–64 and remains policy-bound.
Leader transfer now serializes each source and restores automatic heartbeat/election through Drop
on success, error, deadline and future cancellation, including after becoming follower. Cancelling
the wait never proves a previously sent target request had no effect. Old-source timer/round-trip/
eight-second cancellation counterexamples fail; current actual single-worker file/monitoring and
three-replica certificate/full cold recovery paths pass. See
`docs/development/acceptance/2026-10-20-worker-and-transfer-liveness.md`. Other Operator blocking paths,
full upgrade/durable transfer/online content and complete F0/F4–F10 remain required; no physical proof.

ADR-0117 requires recovery profile v0.2 for replica components, acquiring original supervisor plus
Raft writer leases; old v0.1 replica profiles refuse. Explicit complete same-cluster Artifact sets
verify actual locked cluster/schema/bootstrap and copy only finalized manifest-verified CAS blobs
to declared new roots. SQL/ACL/budgets and original placements remain; staging is not propagated.
Restore receipt v0.2 records original source/target/hash/size and retry only inspects. Actual cold
recovery preserves 6 SQL databases/39 tables/10 certificate receipts and independently reads real
bytes from all three restored leaders, with zero Nodes/devices/models/simulators. See
`tools/quality/check_replica_recovery_processes.py`. Observed intermittent HTTP interruptions remain
F10 work; online cross-host recovery and profile/binary upgrades remain mandatory F5 requirements.

ADR-0116 adds explicit replica v0.3 same-member certificate authority in Raft transactions/snapshots.
Stage/Retire/Abort retain immutable actual callers, original revisions and irreversible retired leaves;
retirement requires all configured members' independent actual target mTLS proofs. Lost-key Abort
only reduces permission. Atomic local PEM bundles and CA overlap/removal reload on real connections;
exact pins, current chain validity and actual TLS witnesses are rechecked under the member writer.
Application checkpoint/revision/budgets do not change from these commands. The current root/leaf
observation never grants authority or claims quorum. Legacy v0.1/v0.2 cannot acquire this authority;
do not relabel their SQL/profile as v0.3. The actual three-replica, zero-Node gate is
`tools/quality/check_replica_certificate_processes.py`. Binary upgrade and full HA/CAS recovery remain
mandatory F5 work; no infrastructure certificate establishes device identity or physical continuity.

ADR-0115 adds default bounded observation aggregation, fixed-label histograms, explicit queue/drop
coverage and opt-in private replica metrics. Prometheus/Alertmanager native config is generated by
`tools/observability/observability_profile.py` from closed explicit deployment endpoints/TLS/budgets.
Controller Operator authorization v0.2 adds `metrics-reader` with only health/readiness/metrics;
accepted v0.2 cannot downgrade to v0.1 even at higher revision. Shared wrapper v42 fences
older writers; v41 standalone migration preserves all process/Memory/verifier evidence and rejects
new metrics authority hidden in old wrappers. Node Contract/config/catalog and journal v13 stay.
Mission metrics formats share authentication, current revocation and metrics-only global access;
authorization v0.2 preserves original v0.1 grants but cannot downgrade after acceptance. The actual
Prometheus/Alertmanager/owned-loopback notification gate extends the two-Node file profile with
`--authority-recovery --monitoring-tools <explicit-tools-root>` and sends no messages to people.
See `docs/development/observability-v0.1.md`; short software observations never establish long-term
SLO, operator acknowledgement, device stopping or overall SoftwareComplete.

ADR-0114 adds opt-in shared EventLog write isolation, enabled by the runnable Controller with a
random actual process identity. Persistence failures latch the original generation before the
writer releases; timers/facts remain available while mutations/ACK/outbox are fenced. Operator
status reads committed memory; global recovery-admin rechecks the actual witness under the same
writer before an unchanged-checkpoint/journal-head zero-event probe can clear the latch. Original
timestamps/budgets/ownership remain. Replica uncertainty and catalog fences require startup replay;
protected replica reads still require current epoch/quorum. Artifact admission/chunks retain the
same fence and original audit. See `docs/development/controller-write-recovery-v0.1.md` and the actual
two-Node `check_memory_preparation_processes.py --authority-recovery` gate. This does not close all
F10 alerting/SLO or remaining F4-F9 requirements.

ADR-0112 replaces caller-asserted GC permission with original-provider reclamation over actual
imports, irreversible revoke receipts, workers and map references. Tombstone precedes unlink;
original completed import history is retained while current availability stays false. Active Map
effects bind the complete original typed input/Node/Group/attempt and freeze the handoff target.
ADR-0113 adds `apps/recovery-tool/` for quiescent complete SQLite/content bundles, pinned manifest
verification and restoration to new roots. All actual writer leases precede source reads. Node
journal v13 now owns canonical sibling and inode leases before SQL/recovery, fencing v12 writers;
Engine clones retain the original journal. Controller wrapper v41 and Node contracts are unchanged.
The actual six-store/SQL/CAS/old-binary recovery gate is
`tools/quality/check_recovery_bundle_processes.py`. Local-copy deletion or a recovery receipt never
establishes global content/physical continuity; complete F4/F5 and F6–F10 remain required.

ADR-0111 integrates Memory remediation, published plan validation and independent process-exit
evidence under Node Contract v0.12 / config v0.13 / catalog v0.15. Published Memory tags 8–10
retain their meaning; original attempt generation uses tag 11 and is identical in preparation
and Execute. Safety metadata requires explicit config/contract negotiation; old v0.8 is map-only.
Shared Controller wrapper v41 and Node journal v12 fence old writers. Ambiguous legacy fork
checkpoints containing process-exit authority refuse automatic migration. The fork ADRs are now
ADR-0109/0110; Memory ADR-0106/0107 remain unchanged. Artifact's shared application writer lease
explicitly unlocks after every store clone and upload is dropped, independently of descriptors
inherited by nonwriter children. No process evidence establishes device safety. See
`docs/development/roboguide-integration-status-2026-10-20.md`; full F4 and F5–F10 remain required.

- `docs/architecture/v2/` contains the current V2 source of truth and summary.
- `README.md` and `docs/project-goals-and-mvp.md` define project scope.
- `docs/implementation-backlog.md` tracks deferred decisions.
- `docs/mvp-definition.md` records the Draft MVP and its freeze gate.
- `docs/development/` proposes module layout and defines engineering rules.
- `docs/decisions/` stores numbered Architecture Decision Records (ADRs).
- `docs/extensions/` contains executable developer paths and offline extension-conformance evidence.
- `docs/images/` stores diagrams.
- `core/` contains maintained Rust responsibility modules; `apps/` contains runnable
  composition roots.
- `core/state/` implements deterministic Shared Node State, Allocation State v0.1,
  source-aware State records, and generic/Spatial Memory catalog projections behind
  transport-neutral ports. Catalog metadata is rebuildable evidence; Memory blobs never live in
  State, Runtime checkpoints, or the Node Protocol.
- ADR-0102's application stop authority freezes the exact operation-owned State export, current
  attempt generation/session, policy and deadline. Durable Cancel receipt, terminal Cancelled and
  independent fresh safe-state evidence are separate gates. Mission holds and release/resume gates
  retain ownership; timer expiry works without Node input, confirmed evidence is monotonic, and
  legacy unverified stop strings migrate to manual evidence inside a startup transaction.
  SoftwareProcess assurance never establishes device stopping. The shared Controller wrapper
  schema lives in `core/state`; both runnable binaries expose read-only `--application-schema`.
- ADR-0110 adds explicit safe-state v0.3 original-software-worker exit evidence. The Local EAIOS
  reaps the original process and publishes its instance/PID/exit code plus complete Group attempt
  generations; terminal rows and Cancel flags alone never prove exit. Natural Failed/Completed
  reconciliation preserves the original Runtime result and does not invent a Cancel receipt.
  Cancelled retains its durable receipt gate. Unified wrapper v41 fences this authority, retaining original
  frozen coverage across restore; software proof never claims device stopping. The offline actual
  Controller/two-Node/owned-child gate is `tools/quality/check_process_stop_evidence.py`.
- ADR-0103's Mission HTTP command journal atomically links Request saves to the exact authenticated
  caller/tenant/method/path/key and immutable progress evidence. `/v1/http-commands/observe` and
  `/v1/http-commands/reconcile` never re-execute create, model calls or Controller submission.
  Interrupted outcomes remain explicitly uncertain. The local SQLite HTTP profile owns one process
  lock; closing the server waits for its workers before releasing that lock.
- ADR-0081 adds bounded opaque checkpoint deltas in `core/state/src/checkpoint_store.rs`.
  Exact schema/sequence/revision/digests, atomic compaction and transaction-pending cache rollback
  preserve the complete authority snapshot. Chain storage fences old binaries even for zero-event
  revisions. Default storage remains full; incremental is explicit, and readonly inspection
  reconstructs the current result. It does not provide complete event reducers or control replication.
- State is not a Global Truth store. Records distinguish Node/World/RoboGuide objects and
  Desired/Committed/Reported/Observed/Derived/Belief semantics, retain source/channel/time/TTL,
  and never collapse independent sources implicitly. The Controller State API is a read-only
  federation over existing authorities.
- ADR-0103's identity/entry slice introduced Controller wrapper v29; ADR-0104 advances it to v30. Control persists the Node
  registry, stable operator grants, immutable Mission tenants and explicit source-bound migration
  receipts; Integration consumes committed lifecycle projections and captures the actual TLS leaf
  digest/notAfter independently of optional diagnostics. Existing sessions recheck revocation and
  expiry on ingress/commands. Matching/Proposal/Commit/Bind/Recovery/Rebind/continuation check tenant
  ownership; lifecycle Commit requires current State. Attestation verifies real signatures, nonce,
  exact PCR selection/digest and original budgets, retains full evidence and rechecks on restore;
  pinned AK admission never establishes manufacturer/physical origin. Mission Service stores
  revisioned grants with stable principals so certificate rotation does not repeat idempotent
  commands; legacy tenant migration is explicit. Empty identity registration is not device facts.
  `tools/quality/check_identity_processes.py` exercises real Controller mTLS/admin/restart with zero
  registered Nodes. Memory scoped content and Replica rotation/upgrade remain F4/F5 work.
- ADR-0104 adds explicit tenant/source/schema/TTL/conflict/condition consumer policies in Control.
  Match/current-State Commit/Bind/Rebind/continuation/outbox readiness and application satisfaction use
  current evidence; Bind checks Commit's policy identity and legacy State-free APIs cannot bypass it.
  Registry commands retain expected revision, canonical digest, original principal and receive-time receipt
  in the authority transaction. Wrapper v30 and event codec v21 fence earlier binaries.
  Explicit scalar Belief and bounded linear-Gaussian CI preserve raw event evidence and lineage;
  confidence never becomes variance, duplicate derivations never renew TTL, old Node epochs cannot
  authorize restored Belief, and unavailable/conflicting/covariance-free data remains unknown.
  Generic verifier sources freeze per accepted Task, bind complete current attempts/plan/predicate,
  verify optional required CAS bytes, preserve original deadlines and keep local completion distinct.
  Continuous expiry/revocation uses whole-Group stop/hold; late terminal reports remain incidents.
  MI Request SQL atomically retains original code/config/Catalog/Prompt/dependency/provider-call
  provenance, with bounded readonly pages and configuration fences before draft reuse. Legacy original
  absence remains unavailable. The actual POSIX compute/CAS/independent-recompute/restart check in
  `tools/quality/check_state_verifier_processes.py` proves software paths only, never device safety,
  hardware identity, physical continuity or overall SoftwareComplete. F4–F10 remain mandatory.
- Memory is separate from realtime State. Execution, Spatial, Semantic, Experience, and Artifact
  manifests retain local ownership and use shared discovery plus selective CAS-backed exchange;
  discoverable metadata may remain content-local and no full replication is implied.
- Memory Scope, Visibility, and Placement are independent: Scope limits semantic consumers,
  Visibility governs catalog discovery/content exchange, and Placement is provider-qualified replica
  evidence. `Local + Discoverable` is valid; visibility never broadens scope, and an Artifact
  reference never proves node-local placement.
- Configured Memory workflows are the Local Memory Provider integration boundary; a real EAIOS
  retains semantic and backend-storage authority. `FilesystemMemoryLedger` is only the RoboGuide
  Node immutable-manifest ledger, rebuildable JSONL index, and workflow-free reference fallback;
  configured EAIOS imports do not copy payload bytes into that ledger.
- ADR-0105's preparation slice adds MissionPlan v0.9 semantic-only Memory requirements and
  Node Contract v0.9 / config v0.10 (catalog v0.12). Control selects only a current operation-owned
  receiving provider after binding. Application retains the original context, policy/membership,
  source session, imports, probe generations and budgets before outbox delivery. Node Prepare ACK
  precedes import; actual original-backend availability precedes Execute, with a whole-Group pending
  prerequisite barrier and final receiver checks. Node journal v9 / Controller wrapper v35 / inner
  v22 fence old binaries. Receiver Linux boot/monotonic deadlines survive process restart without
  renewal. Revocation persists before ACK, even before Prepare; streamed acquisition rechecks
  current permission and receiver budget per bounded write. Restore fences original outstanding
  grants. Unknown import reconciliation uses bounded read-only original-provider queries with
  backoff and never reimports. `tools/quality/check_memory_preparation_processes.py` uses two real
  software Nodes, actual TLS/files/SQL failure/CPU hashes and independent verifiers; no device facts
  are fabricated. Full Group lifetime/handoff, typed-map consumption, active-map/GC and F5–F10 remain
  required; this slice does not establish hardware continuity or overall SoftwareComplete.
- Typed-map byte imports retain complete original Spatial provenance in
  `MapMemoryReplicaRecorded`, keyed by selector/Node/consumer provider. Artifact writer ordering and
  replay require the exact published source; no generic manifest alias is created. Memory API byte
  `replicas` and map-workflow `map_replicas` are separate evidence. Controller wrapper v36 / event
  codec v25 fence old writers. Byte placement never proves active-map selection or localization.
- ADR-0106's current-use implementation negotiates Node Contract v0.10/config v0.11/catalog v0.13
  with preparation v0.2; original v0.1 never carries hidden use authority. Controller wrapper v37
  and Node journal v10 retain separate renewable use commands, original issue/receive budgets,
  provider proofs and irreversible revocation. Node/backend share an explicitly configured actual
  Linux kernel/time namespace and private same-user Unix endpoint. Backend restart fences old
  grants without replacing their original receipts. File jobs durably admit their original compute
  budgets before bounded owned background workers; duplicate HTTP commands do not start another.
  Current-use receipts are not physical stop proof. Full lifecycle/handoff, active-map/GC and later
  stages remain mandatory until their complete conformance records pass.
- ADR-0106's scoped release slice advances the shared Controller/supervisor wrapper to v38.
  Local completion may record AwaitingSatisfaction while only that Task's original context/provider
  revoke ACKs gate normal release; live peers retain permission. Whole-Group cancellation/failure,
  final negative verifier and recovery retain all original receiver/provider fences, separately from
  physical stop proof. Revoke proofs pin the original backend instance/contract even with empty
  import IDs. Application MemoryDispatchPermit retains the earliest preparation/availability/use
  deadline; each Execute send rechecks all pending Group inputs with a fresh local clock. No-Memory
  Groups do not acquire extra clock reads. Real file conformance uses two enrolled software Nodes,
  mTLS/CAS/independent CPU verifiers and original-budget restart; it creates zero device facts and
  does not close full Group handoff, partial-Unknown reconciliation, F5-F10 or HardwareEvidencePending.
- ADR-0107 adds per-original-provider revoke progress and independent readonly use queries.
  Initial Unknown receipts and effect budgets remain immutable; confirmed backend replies are reused,
  and one failing provider never skips independent peers. One explicit observation session per revoke
  has a frozen five-minute/sixteen-generation bound; original GET intents persist before I/O and never
  create control/import effects. Current owner/session/tenant checks gate evidence consumption; late
  results remain history. Query views are paged and bounded. Config v0.12/Contract v0.11/catalog v0.14,
  Node journal v11, wrapper v39 and codec v26 fence older writers. Content revoke proof remains separate
  from device stop, physical continuity and overall SoftwareComplete.
- ADR-0108's application Group Memory handoff freezes both Control memberships, the complete
  original bound attempts, policy revision, stable principal/tenant and original five-minute deadline.
  Source independent stop confirmations and all original context/provider revoke proofs precede
  immutable scoped-child publication; the original source owner, bytes and production provenance
  remain unchanged with full parent lineage. Original target import/probe/use and current State are
  rechecked at explicit commit before Execute. Pending/manual holds retain ownership; source never
  resumes automatically. CAS parent-reference exceptions require the actual original child import
  per chunk and never authorize ordinary aliases. Node Group exports use bounded live original
  journal contexts, with actual Controller provider/member/session admission. Complete receiver
  proofs survive grant pruning. Later accepted Tasks require complete original receiver retirement,
  fresh Control binding/membership and their own new preparation/use; initial grants never renew.
  A post-commit original-delivery fence retains its original commit receipt and proof. Wrapper
  v40/codec v27 fence older writers. SoftwareProcess worker
  exit remains distinct from device stop, physical handoff and overall SoftwareComplete.
- Generic replica durable identity is exact `(MemorySelector, NodeId, ConsumerProviderId)`; admission,
  event replay, projections, and APIs preserve that provider dimension, and accepted evidence remains
  monotonic after Imported. Explicit application-owned selective-import commands and bounded
  original-provider queries are durable; discovery never authorizes automatic replication.
- ADR-0105's current continuity slice negotiates Node Contract v0.7 / config v0.8 over Protocol
  v0.4 (contract catalog v0.10). Original workflow/connections, actual backend instance, complete
  command and manifest metadata persist before import effects. Typed durable original-provider
  completion is mandatory in this profile. Unknown fences replacement provider/selector IDs;
  queries retain original generations/deadlines/counts and never repeat import or a query ID.
  Node journal v7 uses readonly legacy views to reject actual v6 binary writers; Controller
  wrapper v31 and event codec v22 fence earlier software. Late/changed-ownership query evidence is
  history only. Replica Imported history never grants current availability or Task satisfaction.
  The shipped immutable-file provider and process conformance use actual files/SQL/OS processes,
  zero devices and no model/simulator. Full Group authorization, revocation/GC, active-map and
  distributed Spatial activation/use remain required F4 work; this slice is not overall completion.
- ADR-0105's content gate adds explicit Control policy/history and stable Artifact certificate grants.
  Actual binding changes advance Group membership generation; historical absence stays unavailable.
  Import intents retain original tenant/provider/policy/membership and authenticated submitter.
  Artifact writes recheck actual TLS/grant authority under writer ordering; blob reads recheck per
  bounded chunk. Actual complete staged bytes are hashed/fsynced before a durable upload proof;
  existing CAS hashes and new manifest aliases never establish byte ownership or broaden scope.
  Wrapper v32, inner checkpoint v21 and event codec v23 fence old readers. Upload history remains
  protected from archive deletion. `contracts/memory/authorization-v0.1/` and
  `tools/quality/check_artifact_authority_processes.py` define the software/API slice; no Node or
  device is invented by its certificate/upload check. Node-wide Group grants/revocation ACK,
  pre-Execute Memory readiness, full handoff, active-map/GC and complete Spatial consumption remain required
  F4 work. Do not claim complete distributed Group authorization from these current gates alone.
- Operator HTTP mutation transactions retain an actual-transport witness through the real writer gate.
  Current leaf expiry, committed/deployment grants, principal, role, tenant and Mission ownership are
  rechecked after acquiring that mutex, before candidate/SQL mutation. The same guard spans Commit or
  rollback; no nested/fake mutex grants authority. Registry SQL hands the Tokio worker back; other
  blocking/degraded-read paths remain F10 work. `tools/quality/check_identity_processes.py` uses actual
  HTTPS, SQLite and inotify to observe original grant reads, queue work, revoke the leaf and verify
  that no second registry revision commits. It registers zero Node facts.
- `core/orchestration/` owns complete MissionPlan acceptance, Mission-level Group
  creation, DAG-driven TaskExecution readiness, and explicit Mission completion.
- ADR-0092 fences the reviewed reliability slice: wrapper v26 and failure-window event codec v19
  retain at most three rolling ten-minute failure samples. Same-outcome replay and rejected resumes
  are not new failures; legacy samples remain unavailable, and unreviewed quarantine never expires.
  Operator mutation routes authorize certificate-mapped roles before application mutation; headers
  never grant authority. Raw loopback defaults to operator; a development administrator requires an
  explicit startup flag and cannot be combined with mTLS or remote binding. Replica clients pin the
  exact target leaf before sending frames, and startup verifies the local member identity.
  Local archive uses bounded segments, immutable sidecars, a durable paged manifest index and managed
  SQL transactions. Catalog/Memory/localization replay evidence cannot be deleted until a complete
  snapshot/archive-replay contract exists. Active/Unknown and replicated maintenance are refused.
  Recovery view v0.2 separates current blockers from retained history and exposes actual attempt IDs;
  its sequence is guidance. Software tests do not establish physical continuity or E6 readiness.
- ADR-0082 adds Orchestration-owned durable logical priority/aging and bounded round dispatch.
  Resource deferrals preserve first waiting ordinals; actual new bindings/preparation consume age.
  Due Control windows retain precedence. Defaults are priority 128, aging per 8 logical turns and
  at most 32 Task attempts per input/timer. Deployment admission limits (128 nonterminal Missions,
  4096 declared slots) count holds/cancellation, reject before Group creation and never revoke
  ownership. Operator priority updates persist through the existing application transaction.
- Orchestration scheduling dispositions are typed: deployment shortages remain observable,
  durable deferrals, bound Actor reconciliation remains Control-owned, and invalid contracts
  remain separate from internal failures. Application dispatch never parses diagnostics to
  decide retry policy; one Mission's current entity cardinality shortage cannot stop the timer.
- Control reservations remain the sole commitment authority; Allocation State is
  a whole-view observable projection that may lag and never grants or revokes ownership.
- ADR-0077 adds Orchestration-owned, durable Mission execution isolation for conclusively local
  typed contract failures. Application candidates and event savepoints roll back together; shared,
  Control and persistence errors retain fail-closed behavior. Holds retain physical ownership and
  raw evidence while fencing new Execute and derived work. Explicit generation-bound resume uses
  Control's current eligibility rules and commits before delivery; cancellation retains its stop rules.
- ADR-0079 adds a production exact-Task completion-deadline violation source. Runtime persists the
  first terminal Controller receive time; Orchestration checks accepted timing, and application
  TaskOutcome alone admits the proven Mission-local hold. Queue delay, replay or restore never
  refresh that time. Missing legacy timing remains unavailable. This observation contract neither
  proves physical deadline violation nor authorizes stop, resource release or automatic repetition.
- Resource-bearing Commit requires current State and revalidates every selected ResourceId's
  Node, kind and capacity before any mutation. State-free compatibility Commit accepts only
  zero-resource proposals without independent operation constraints.
- The default MI output uses `roboguide.mission-plan/v0.8` (ADR-0040); explicit v0.9 adds semantic
  Memory prerequisites with the preparation contract above. Mission Actors are declared once,
  ContextRoles reference Actors, and TaskRoles reference ContextRoles while declaring multiple exact
  capability requirements, independent semantic ExecutionIntent, resources, Mission timing
  constraints, expected effect, and satisfaction evidence basis. Runtime/State duration estimates
  are separate source-aware Scheduler evidence. v0.2-v0.7 remain compatibility inputs; their
  `execution-report` basis does not claim independent physical-world verification.
- v0.8 Context executor constraints refer to distinct PhysicalEntityIds, not distinct NodeIds.
  Deployment owns a revisioned entity-to-Node registry; Control validates it at Commit/Bind and
  reacquires it after checkpoint restore. The current one-entity-per-Node routing profile is an
  explicit Node Protocol limitation, never a Mission Actor identity rule. Grounded entity refs
  must come from admitted fresh Mission evidence, not invented provider output.
- Control persists a registry identity/highest-revision/content-digest watermark independently
  of live topology and bind-time provenance. Restore still requires deployment topology;
  historical physical-binding checkpoints without this watermark require trusted migration.
- Runtime successful terminal facts mean local execution completed, not that a Task or Mission is
  semantically satisfied. Control records `AwaitingSatisfaction` and retains bindings;
  positive Mission-declared evidence emits `TaskSatisfied`, then releases Task-scoped ownership,
  advances the DAG, and evaluates Mission completion. A final negative verifier emits
  `TaskSatisfactionRejected` and enters `SatisfactionRejected`; Mission policy may fail/release the
  Group without emitting `TaskExecutionFailed`, which remains reserved for local execution failure.
- Accepted-plan Execution Session v0.1 is immutable Group topology evidence carried with
  Runtime Execute through Node Protocol; it is not Mission semantics, a Node selector, or
  resource authority. The Habitat shared-world deployment admits either two independent
  Actors on two endpoints or one independent Actor whose Tasks reuse one endpoint and one
  reset world after each Control-owned Task release. Other topologies fail closed; benchmark
  success still comes only from Habitat's official metric. In a one-Actor segment, only the
  assigned endpoint calls its original Stage2 model. Unassigned endpoints use a scoped,
  model-free policy selecting the original EMOS wait skill and restore their CrabAgent after
  the segment; two-Actor assignments retain both original model paths. See ADR-0045.
- Execution Relation endpoints are exact logical `(TaskId, RoleId)` slots inside one Context,
  never NodeId or adapter handles. Runtime resolves them to current attempts, persists live
  relation state/fences, and emits evidence; Control retains commitment and recovery decisions.
- The current executable relation profile contains `RequiresActive` and `SharedSpatialReference`.
  Strong localization evidence must match the current attempt, current Node owner, immutable map
  revision, and frame; other typed relation syntax fails implementation preflight before Group
  creation rather than remaining an indefinitely `Unknown` execution.
- Mission Actor is logical metadata, not a physical-node selector. Deployment-owned
  actor placement constraints may narrow first-use Control Matching, but create no
  reservation or ActorBinding before Proposal -> Commit -> Group Bind succeeds.
- Runtime observation ingestion normalizes `NodeGateway.status()` into reported
  health and separately records RoboGuide-observed liveness; it does not trigger
  reconciliation or automatic recovery.
- `core/runtime` owns the transport-neutral live execution registry, stable execution
  identity, logical-slot versus physical-attempt history, durable command intents, ordered fact
  reduction, recovery-required fencing, and its checkpoint.
  `core/orchestration::IntegrationRuntimeBridge` is the Controller composition facade
  and must not keep a second authoritative execution map or directly mutate Task lifecycle.
- Runtime `Unknown` means recovery-pending physical ambiguity, never terminal Task or
  Mission failure. Integration persists the evidence; application orchestration applies
  Runtime lifecycle transitions, while Control retains recovery decisions.
- Observation time semantics preserve source-local `NodeStatus.observed_at` while
  State ordering and Control freshness use RoboGuide-local `received_at`; do not
  compare independent source clocks or claim distributed clock synchronization.
- ADR-0076 adds exact-session continuous liveness evidence projected by Integration and admitted
  only by Control inside application transactions. It does not refresh health or acknowledge queued
  raw facts. Process-local session anchors clear on restore; expiry/route-loss fences require new
  registration. Bridge ingress preserves original receive time while decisions use fresh local time.
- State checkpoint restore preserves each record's original `received_at`; restart must never make
  expired evidence Fresh. Group views and relation evidence use the same persisted receive-time
  semantics.
- Assigned-node reconciliation lives inside `core/control`, reuses the shared
  eligibility predicate and existing Group lifecycle APIs, and consumes an
  externally supplied recovery proposal; it never selects a replacement node.
- Recovery reassignment follows role-scoped Match -> Propose -> Commit -> Rebind:
  candidate matching may be empty, proposal creates no reservation, commit uses
  the single Control reservation authority, and rebind requires commitment.
- Explicit cross-entity Actor takeover is a separate Control authorization (ADR-0070), never an
  ordinary recovery side effect. It binds exact blocked Group/Task/Role, source and standby physical
  entities, registry revision and evidence digest; Match/Schedule/Proposal/Commit remain mandatory,
  and Rebind atomically updates ActorBinding only after current-attempt stop proof is retained by the
  application. Grounded Actors and fixed placement do not migrate.
- Committed-but-not-bound recovery assignments are Control-owned pending
  commitments keyed by Group/Role; Rebind consumes, Abort releases replacement
  resources, and terminal Group release removes all related ownership.
- `core/control/src/scheduler.rs` implements the stateless deterministic bounded joint `Who should /
  Where / When` policy over supplied Candidate Sets and immutable Control calendar snapshots. It
  backtracks across Role/Node/exclusive-resource/time choices but does not re-run eligibility,
  validate proposals, commit resources, or mutate State/Groups. `units` is a minimum selected
  resource capacity, not divisible quota.
- Control durably owns future Ready-Task scheduling intervals and their generation. Due intervals
  re-run Match -> Propose -> Commit -> Bind before dispatch; activated intervals are planning
  evidence beside the existing physical reservation authority. Overrun becomes open-ended
  occupancy and never preempts running Local EAIOS work. Normal and recovery Commit both reject
  conflicting future intervals; Recovery reads the same calendar snapshot but does not create a
  future reservation. Scheduled activation requires exact committed Role coverage and a live
  selected/latest/deadline-derived activation window. Window-missed deferrals are durable and
  deduplicated per Task and leave automatic timer dispatch so one Mission cannot terminate or spin
  the application timer.
- ADR-0084 permits explicit higher-priority Orchestration requests to preempt only an exact-generation,
  unactivated/unbound/uncommitted strictly future interval. Current due windows, held Missions,
  Context resource ownership, physical bindings and pending commitments reject; original waiting age
  and plan remain. Typed codec-v17 evidence and candidate/calendar changes commit together. No stop
  or immediate Execute is authorized; running preemption remains a separate stop/repeat/handoff gate.
- `core/artifact-store/` contains the filesystem implementation of the transport-neutral
  ArtifactBlobStore port; it stores opaque bytes and does not own map, task, or execution policy.
- `core/integration/` owns the formal gRPC Node Protocol v0.4 transport;
  `core/node-service/` owns node-side lifecycle, configuration, durable execution
  continuity, and the declarative Local Integration Engine.
- `core/integration/` contains only formal Node Protocol wire/session/router code;
  Controller composition and the Runtime bridge belong to `core/orchestration/`.
- `ports::NodeProtocolPolicy` freezes transport lease and application-completion timing; optional
  transport-neutral diagnostics preserve exact ingress metadata without granting authority.
  `apps/integration-server/src/diagnostics.rs` owns bounded, default-off local JSONL observation;
  observation loss cannot change facts, checkpoints, ACKs or scheduling. See ADR-0075.
- Node Protocol `Registered` and sequence `Ack` mean Controller application authority plus durable
  checkpoint acceptance, not transport receipt. Integration waits on an application completion
  envelope but never makes the Control/State/Runtime decision itself.
- ADR-0080's application `fact_batch.rs` processes ordered inputs under distinct input and Mission
  savepoints, sharing only the final checkpoint/Commit. Registration/update/route-loss are independent
  barriers. Batches default to at most 8 inputs within one 2ms coalescing window (bounds 32/5ms);
  `ROBOGUIDE_FACT_BATCH_LIMIT=1` restores single-input behavior. Rejected exact sessions are fenced
  before post-Commit outbox delivery; SQL/shared failures abort the entire uncommitted batch.
- Node config v0.7 separates exact capability profiles from canonical operation workflows. Every
  profile owns typed scalar attributes plus one fixed readiness observation; Node Service observes
  before Register and emits complete RegistrationUpdate snapshots on change. Node Contract v0.6
  preserves these profiles plus exact canonical Operation Support through State into Control
  Matching. Operations independently map
  semantic `ExecutionIntent` to Local How. Node config v0.2-v0.6 remains an explicit combined
  declaration compatibility input. The config also declares fixed State exports and Memory
  providers and optional provider-local discover/export/import workflows; v0.2-v0.4 normalize
  those declarations to empty and v0.5 providers remain metadata-only. Optional v0.6+
  peer-channel observers use fixed read-only routes and configuration-owned LocalSystem
  identity; their response observes established endpoints and never requests transport setup.
- Node Protocol v0.4 with Node Contract v0.6 carries complete capability-profile,
  operation-support, and State/Memory
  provider snapshots, semantic invocation objective/operation/parameters, bounded periodic State
  observation batches plus identified peer-channel readiness facts. Local EAIOS establishes the
  actual peer channel; Controller verifies Node/LocalSystem/committed ContextRole ownership, and all
  logical peers must provide non-expired receive-relative acknowledgements for one
  instance/profile/schema before Runtime marks it Ready. Expiry, route loss, and restart fence it;
  a bound waiting Task remains durable Ready for later dispatch. It also carries immutable Execute/
  Cancel command identities and Node-journal receipts; receipts prove durable command admission, not
  execution outcome. Sampling failure only causes
  staleness and never changes health, readiness, execution lifecycle, or recovery. The v0.2
  endpoint is rejection-only.
- `integrations/` contains deployment-owned Local EAIOS adapters (for example the
  Robonix map adapter and Habitat Local EAIOS bridge); these adapters own vendor calls,
  simulator calls, and local file layout only,
  and must not become a second Control, Runtime, State, or Node Protocol authority.
- `integrations/spatial-provider/` owns actual CPU file geometry in an independent Python 3.11 /
  Open3D 0.19.0 runtime. Inputs freeze exact bytes/units/frames and original bounded process work;
  verified FPFH/RANSAC, ICP and pose-graph transforms produce observed-point immutable tile bundles.
  Parent revisions remain unchanged, unobserved space stays unknown, and metrics never become
  covariance or current Node localization. Original-job retries are readonly and never rerun native
  computation. `tools/quality/check_spatial_provider_files.py` verifies attributed actual files,
  independent coordinate truth, units/loop/incremental and adversarial failures with zero devices.
  The root environment tests pure contracts only; do not install native geometry into MI.
  The explicit planar occupancy profile has separate SE(2) search/ICP/graph and conservative cell
  coverage policies; missing cells remain unknown and contradictory observations remain conflicts.
  `contracts/spatial/file-provider-v0.1/` contains the five structural JSON schemas. The once-only
  worker binds the actual original Linux monotonic clock/boot before source acquisition; audit wall
  rollback, reboot and another host cannot renew work. Completed original proof remains readonly
  after reboot. Its private Unix HTTP service retains the actual backend UUID and original attempt
  identity; cancellation needs actual original worker exit and remains software-process evidence.
  The deployment generator declares arbitrary Node IDs and semantic output profiles, never devices.
  `tools/quality/check_spatial_node_processes.py` exercises maintained Node workflows, actual CAS,
  an independent source/tile verifier and Controller/Node restart without another worker. The explicit
  mTLS profile uses actual enrolled software Node credentials and scoped Artifact/Operator principals.
  Config v0.9 / Contract v0.8 / catalog v0.11 carry original provider-bound map v0.2 publication.
  Runtime freezes dispatch-time contract/session/provider declarations outside semantic model input;
  retry/restore retain original unavailable fields. Changed declarations/session fence new Execute;
  catalog publication rechecks original completed build and exact live publishing attempt under the
  actual writer gate. Node journal v8 retains v7 table layouts/views but rejects the actual v7 binary;
  wrapper v33 and map codec v24 independently fence old writers, including zero-event metadata.
  `tools/quality/check_spatial_upgrade_gates.py` checks actual archived binaries; the independent
  schema checker uses pinned jsonschema outside MI. Complete Group/pre-Execute/active-map/GC and
  current availability and lineage remain required F4 work.
- Memory authorization v0.2 additionally binds complete typed-map metadata; raw Spatial provenance
  is checked by Control, and generic projection-only reads/imports cannot strip that requirement.
  Explicit v0.1-to-v0.2 narrowing retains immutable original digests/history; removing or changing a
  typed binding and schema downgrade reject. Wrapper v34 fences older writers, including zero-event
  registry changes. Positive software conformance uses real mTLS, CPU files and independent readers;
  it never establishes device identity, physical continuity, active-map or Group preparation.
- The C1-S0 Habitat bridge supports existing `mobility.navigate@v1` without changing Core schemas.
  It keeps EMOS/Habitat dependencies in their independent Conda environment, maps semantic
  destination to the existing local Oracle navigation action, owns one persistent simulator process
  behind a responsive loopback HTTP facade, and reports terminal state only from the local execution
  outcome. Cancel acceptance is not `CANCELLED`. Node Service reacquires bounded transient status
  observation failures using the durable local handle and never redispatches Execute; budget
  exhaustion remains explicit physical ambiguity. The Controlled backend injects the
  Control-owned assignment at the EMOS Stage1-to-Stage2 boundary and binds its one navigation
  target in the execution-scoped Provider tool schema. It runs the original EMOS
  `MultiLLMPolicy`, `HierarchicalPolicy`, and skill stack, with original `CrabAgent` for assigned
  endpoints, without rewriting a model-selected action; the independent Contract Guard still
  rejects wrong targets. Local skill
  completion,
  benchmark PDDL success, episode termination, and RoboGuide Mission outcome remain distinct facts.
  Controlled Stage2 uses deployment-owned observed local skill feedback (ADR-0069):
  exact call/agent/invocation-bound receipts distinguish completion, budget expiry,
  interruption and unknown rather than returning synthetic success before execution.
  Original termination methods execute once; existing post-step local terminal facts
  may supersede earlier policy-input evidence. This changes local model input, not
  selected actions, robot abilities, simulator/model budgets or official truth.
  The opt-in bound relocation feedback v0.4 adds sparse exact-object arm/grasp
  observations and existing peer canonical scopes. Missing reads remain unknown;
  geometric distance or repeated failures never establish manipulation infeasibility.
  Original peer messages cannot transfer Control-owned Tasks. Completed model-free
  idle recipients are withheld and raw requests fail before dispatch; wait completion
  is neither delivery acknowledgement nor peer action evidence. Navigation and
  disabled binding retain their previous inputs and tool contracts.
  It grants no Control, Task satisfaction or MI replanning authority and must be
  disclosed separately from the original native EMOS feedback path.
  Optional physical diagnostics v0.7 additionally tap the original pick/place arm,
  workspace-clipping, FK/IK and gripper calls once with unchanged arguments/results
  and exceptions. They add no solver, simulator, grasp, contact or RNG call, retain
  bounded vectors/calls and pending failed-step evidence, and remain diagnostic-only.
  The shared-world adapter freezes the exact Node-config floor-transition facts in a digest-bound
  deployment snapshot and checks actual reset-state semantic regions before Stage2 acts. Only an
  explicit incompatible assignment fails locally; unresolved region evidence remains unknown and
  does not prove route feasibility or authorize adapter-side reassignment. See ADR-0046.
- The explicit independent-live/v0.1 Habitat deployment supports one to four source-bound
  endpoints and at most 32 independent Task/Role slots with sparse dispatch and Task reuse.
  Roles in one Task use distinct logical Actors and capacity-one endpoints; Control alone
  makes their atomic resource commitment. Different Tasks may reuse an Actor's endpoint.
  Only actual Control dispatch activates an original Stage2 attempt; unassigned/completed
  endpoints use original model-free wait. Same-attempt native zero-action reinitialization
  is fenced by an instance hook, a disclosed Local How change. Relocation readiness may be
  a typed subset of the full world; no Drone manipulation is synthesized. Optional canonical
  observation.verify@v1 reads only existing detected(entity-ref) sensor caches: negative
  acquisition is not affirmative satisfaction and unavailable evidence is never invented.
  B1 deployment v0.3, feasibility v0.5 and Local How v0.9 carry exact source identity; legacy
  profiles, Core authority, common MI Prompts and official/Formal rules remain unchanged.
  New populations require physical/reset/archive release gates; the three-agent Task4
  native mapping remains unresolved. See ADR-0074 and the live deployment guide.
- The default-off Habitat relocation profile reuses canonical `object.relocate@v1` and
  exact object/source/destination through durable endpoints and shared-world IPC. Readiness
  requires loaded, observable original skills; the guard advances only on observed pick/place
  completion. Navigation is an intermediate step, not relocation success. Observed original
  place completion switches only the completed endpoint to original wait while its peer continues;
  this scoped model-free idle is disclosed as a controlled-arm difference. Both distinct-object
  dual-Actor and single-Actor sequential sessions reuse one reset world. Concurrent manipulation
  of one object, retained manipulation continuation and any_at-specific goal-region profiles
  fail explicitly. Navigation-only floor/progress evidence remains unknown for relocation;
  declared source identity does not prove actual source placement. Local completion, official
  PDDL, Task satisfaction and Formal admission stay separate. Task3 registration and episode-start
  source evidence and generic B1 runner wiring are implemented and offline-tested; real physical
  preflight remains pending. The explicit relocation deployment freezes original Habitat config
  and budget, run-local Node/MI profiles and fresh reset sources before the one production MI
  request. Prepare-only mode starts no service or world; relocation recovery is group-stop with
  unsupported continuation, never silently inherited navigation repeat support. See
  `docs/extensions/habitat-relocation-profile.md`; no Core contract or vendor source is changed.
  Explicit relocation publishes deployment feasibility v0.4 with a separate neutral
  operation-admission v0.1 profile binding reset object sources and configured endpoints.
  Navigation floor exclusions never prove manipulation feasibility. Common topology and
  exclusive resource requirements remain, and Control still checks current registration.
  The combined source digest fences restored candidate restrictions; v0.3 cannot enable
  relocation implicitly. See ADR-0071.
  Controlled shared relocation obtains Stage2 robot types from that frozen profile only
  after actual loaded-class verification; contradictory native resume identities reject.
  Missing resumes remain explicit gaps, never synthesized capabilities (ADR-0072).
  The separate default-off exact-object place completion profile (ADR-0073) binds
  canonical attempt/object/destination and actual grasp release through instance-local
  observation/termination hooks, with the original release action, 0.02m threshold and
  budgets. Local How v0.8 / feedback v0.3 disclose this controlled-arm difference.
  The enabled guard admits holding reset after observed skill exit and offers phase-bound
  navigation targets; it never fabricates placement, rewrites actions or changes official truth.
  `relocation_completion.py` owns these local bindings, never Control or Task satisfaction.
  B1 relocation deployment v0.2 explicitly selects completion binding while the generic
  adapter remains default-off. The runner freezes a run/input/deployment-bound requirement
  and checks actual child Local How v0.8 / feedback v0.4 plus loaded completion readiness
  before Controller/MI startup. Missing or contradictory evidence is a preserved harness
  failure, never invented benchmark truth. Legacy v0.1 and navigation retain compatibility.
- Optional goal-region navigation is a deployment-owned Local How variant for direct
  conjunctive official `any_at` goals (ADR-0051). It preserves the exact model-selected
  entity and original Oracle control loop, but may select a different physical target
  point on the agent-specific navmesh after bounded route checks. It never changes
  canonical intent or official PDDL truth. The mode is off by default and must be
  disclosed as a RoboGuide-versus-native-EMOS arm difference when enabled.
- A separate default-off reset-route-support observer (ADR-0052) uses copied
  NavMeshSettings and a detached PathFinder after the existing reset. It queries
  only original/projected-center candidates, with at most 128 records and 256
  path queries, and never primes action caches, samples RNG, or scans mesh vertices.
  The digest-bound artifact distinguishes static witnesses, bounded misses, and
  unavailable evidence. It is diagnostic-only: no MI input, Actor-wide exclusion,
  Control authority, Formal admission, or benchmark change. Opted-in B1 preflight
  checks identity/source/geometry and attributes archival failures to the harness.
- An independently enabled initial-operation preference consumer (ADR-0053)
  projects positive reset witnesses into exact-intent costs. The Controller
  compares bounded initial endpoint combinations; Control admits transient
  Task/Role ordinals and Scheduler only reorders current eligible candidates.
  Unknown costs never exclude a Node. The first successful Bind discards the
  Mission's preferences, expiry restores stable order, and recovery/restore
  never renew reset evidence. This is an opt-in initial policy, not MI input,
  resource authority, route feasibility proof or automatic terminal replanning.
- Optional reset-route geometry (ADR-0062) reads only the detached starting
  component under explicit retained-array, triangle-work and processing limits.
  Full triangle interiors, reference-offset rotation and boundary uncertainty
  distinguish static intersections, scoped disjoint geometry and unknown.
  Opted-in B1 requires identity/source/budget consistency and the v0.2 archive;
  the separate initial preference consumer may rank scoped misses after exact
  source checks but never exclude a Node. MI inputs, operation intent, physical
  navigation parameters, Core contracts and Formal admission stay unchanged.
- Optional initial-support feedback (ADR-0066) is a read-only Controller
  projection for the first fresh-world admission. It binds the exact generated
  plan and source/Local How/lifetime, inspects existing Control eligibility on
  a private copy and returns neutral logical-slot support counts without Node
  inventory. Complete scoped disjoint combinations may hold the reviewed
  Request under an explicitly enabled deployment readiness policy; bounded
  misses and unknowns never independently block. Explicit retry rechecks the
  same plan by default, with no model regeneration, reservation or automatic reassignment.
  Recovery evidence v0.2 durably fences interrupted queries. Attributable holds
  remain Formal system failures with benchmark unavailable. The neutral schema
  lives in `contracts/mission/initial-operation-assessment-v0.2/` (v0.1 archives remain readable); public Request,
  MissionPlan and Core authority are unchanged, and defaults remain off.
- Optional pre-submission MI deployment reconsideration (ADR-0067) uses exact-plan
  assessment v0.2 with bounded query-time exclusion counters, never live inventory.
  The configured Responses Repairer proposes recheck, complete draft revision or
  wait under durable, nonrenewable count/time bounds. Revisions re-enter full
  validation, Reviewer, risk approval and Control preflight. Identical feedback,
  source/context change, expiry and restart cannot renew calls; ambiguous submission
  permits existing read-only reconciliation only. Immutable session v0.1 is carried
  by observations v0.4 while public Request/MissionPlan stay unchanged; zero attempts
  keeps the unchanged-plan path. This is not execution-time replanning or proof of
  model semantic fidelity, physical feasibility or benchmark success.
- Optional step-aware NavMesh resolution (ADR-0063) is a default-off Local How
  profile, separate from diagnostic observation. Active navigation and reset
  observation use identical copied settings; vertical cells may be refined to
  preserve declared positive climb, with explicit minimum-resolution/refinement
  limits. Robot dimensions/climb/slope, canonical target, original Oracle control
  and official PDDL remain unchanged. Local How v0.4 and action evidence v0.3
  disclose the arm difference from original EMOS; paths never prove success.
- Optional spatial route arrival (ADR-0064) is a separate default-off Local How
  controller for the existing differential-base motion profiles. It preserves the
  exact entity, copied mesh/ability and original thresholds, velocities and budgets,
  but follows the route until 3D proximity before facing the entity. Missing or
  unusable routes fail explicitly; there is one original base dispatch per action,
  no extra model/action/Gym step and no external EMOS edit. Local How v0.5 and action
  evidence v0.4 disclose this change. Reset observation retains identical copied
  settings; bounded pre-motion decision diagnostics never become official truth.
- Optional live-reference goal-region arrival (ADR-0068) is a separate default-off
  Local How controller requiring the goal-region, step-aware and spatial profiles.
  It admits an actual routed point inside the unchanged goal region and permits
  local completion only when the actual PDDL reference is inside the existing local
  margin plus original point/heading conditions. Original speeds, abilities and
  budgets remain intact; missing/changed geometry fails before motion. Local How
  v0.7 and selection v0.6 disclose the arm difference. Reset observation retains an
  explicitly declared conservative stop-envelope probe; its bounded miss is never
  an exclusion. Official PDDL and Orchestration satisfaction keep their authorities.
- Enabled spatial route arrival now uses Local How v0.6 joint navigation preparation
  (ADR-0065): the vendor-decoded selected navigation actions prepare target/path/command
  before the original Gym step. Expected bounded preparation failure prevents that step,
  preserves Completed peers and retains exact canonical attempt attribution. Failure before
  the first physical step has no official benchmark execution result; reset truth remains
  diagnostic. Preparation initializes execution caches and may use the original target
  helper; it is not a read-only or RNG-neutral observer, a global impossibility proof,
  Control route authority or arbitrary simulator rollback. Disabled profiles retain their
  legacy paths; historical Local How archives remain supported.
- The shared-world child now performs its one Habitat reset before endpoint readiness, freezes
  a digest-bound, run-local negative feasibility matrix from actual agent starts, PDDL entities,
  and the exact Node-config floor facts, then reuses those observations for Stage2. The optional
  Controller deployment adapter intersects each logical Actor's exact-intent candidate Nodes
  with current Control eligibility; it does not select or bind an executor. B1 cross-checks the
  matrix against the frozen workload and source digests before Controller startup. The current
  paired endpoint profile requires one exclusive `space:1` slot per Role and one capacity-one
  slot per endpoint; unsupported topology or known candidate shortage fails before submission.
  A floor mismatch does not exclude a distance-based `any_at` goal that may be satisfied from an
  adjacent floor. Such reachability stays unknown until actual execution; the local post-assignment
  gate uses the same semantics. See ADR-0047 and ADR-0050.
- The Robonix map adapter exposes process health separately from exact capability
  readiness. Its startup-fixed ROS service discovery command is read-only and
  deployment-owned; execution requests must never supply commands or service names.
- The `apps/integration-server` application `deployment_sources.rs` owns concrete startup source decoding,
  `deployment_document.rs` owns canonical source identities, and `verifier_source.rs` owns the
  neutral immutable verifier codec. Existing candidate/plan/attempt/satisfaction consumers retain
  Control and Orchestration authority; no producer gets execution mutation authority.
- `apps/integration-server/` is the formal gRPC server composition root and
  `apps/roboguide-node/` is the configured node-side daemon composition root.
- `core/controller-replication/` owns OpenRaft-backed durable opaque accepted batches, FULL SQLite
  consensus storage and bounded replica RPC; the default profile is trusted-loopback, while
  ADR-0086 adds opt-in transport identity/envelope authentication. It never makes Control/Runtime
  decisions.
  `apps/controller-replica/` owns the opt-in static three-member supervisor and only its Controller child.
  Exact base/tail/profile and majority acceptance precede local SQL Commit/ACK; original epoch/quorum
  fences each Execute/Cancel. Promotion rematerializes complete metadata then restores Unknown and
  retained ownership without replay. Artifact/Memory bytes stay separate. See ADR-0083.
  Node config v0.7 optionally permits two bounded fallback Controller endpoints; session loss rotates
  fixed routes while preserving Node identity, command journal and Local EAIOS handles.
- Each node machine runs only `roboguide-node`; new Local EAIOS integrations use
  startup-validated HTTP, dynamic gRPC, or MCP workflow configuration and never
  add an EAIOS-specific code branch or RoboGuide-side service.
- `apps/real-node-smoke/` probes the formal Node Protocol v0.4 handshake by default; its explicit
  `--simulate-execute` mode submits a synthetic Mission through the Controller HTTP API, emits only
  synthetic lifecycle facts after formal dispatch, uses a session-unique capability contract so it
  cannot select an existing Node, and never performs hardware I/O.
- Execution commands carry canonical `ExecutionIntent`; Matching and Scheduler do not interpret
  objective or parameters, Runtime only routes them, and the configured Node Service operation
  workflow maps the intact intent to Local How. Node Contract v0.4 cannot silently drop a meaningful
  objective during compatibility routing.
- Spatial map bytes use the independent Artifact data plane. `MapId`/`MapRevisionId` references
  may be carried as opaque intent parameters, while digest verification and local staging belong
  to Node Service and the independent Artifact data plane. Node Protocol never carries map bytes.
- Strong localization verification uses
  `roboguide.localization-verification-evidence/v0.1`. Node journal persistence precedes remote
  delivery, Artifact HTTP records the evidence transition, and State distinguishes strong
  evidence from legacy `has_map=true` smoke verification. Real facade field mapping remains a
  hardware-validated deployment responsibility.
- `mission/` contains the Python Mission Intelligence package and its tests.
- Optional deployment planning input is startup-frozen abstract capability-class evidence plus
  versioned environment-authoritative world facts. The latter enters Grounding Context
  v0.3 with explicit unknown gaps and exact run/episode/scene/dataset identity; v0.2 remains
  compatible. Neither source grants MI live Node/Resource inventory or executor-selection
  authority. A Role feasibility constraint requires grounded task and world evidence; Control
  alone matches current Node registration and commits resources. After the existing single
  shared-world reset, planning-world evidence v0.2 may add an exact official geometric
  single-location witness for a joint terminal goal. It is a snapshot planning option, not a
  route, physical assignment, task completion, or benchmark outcome. See ADR-0044.
- Deployment Planning Profile v0.2 may additionally declare canonical-operation reads and possible
  changes to documented neutral state domains. Mission emits an exact-plan/profile-digest-bound
  advisory composition review to Reviewer and Repairer; possible overlap is not a proven goal
  conflict, admission rule, physical binding, or official verifier result. Legacy v0.1 remains
  readable without inferred effects. These declarations never select Nodes or Physical Entities.
- `apps/mission-service/` is the Python Mission Request composition root. It owns text instruction
  ingress and durable deliberation state, then submits accepted complete plans to the existing
  Controller API; its Interpreter does not consume live Node/Resource inventory, and current
  provider absence is a Control scheduling condition rather than a Mission semantic rejection. It
  must not mirror execution lifecycle or choose physical nodes. Mission Request v0.4 captures one
  immutable, digest-bound Mission Grounding snapshot per deliberation from deployment-admitted,
  attributed World State payload schemas and Global Semantic/Experience/Spatial Memory metadata.
  `World` classification is not visibility: schema admission fails closed, its identity is retained,
  and native per-record consumer scope remains deferred. Nested evidence uses defensive-copy access,
  canonical restore ordering, bounded diagnostics/final bytes, and shared Rust/Python facade fixtures.
  Each captured context is immutable digest-addressed history, is retrievable by Request/digest, and
  must match the exact model-input Dialogue revision before use. HTTP protocol interruption remains a
  bounded-retry, fail-soft source gap. A Grounding gap is acquisition evidence rather than user
  ambiguity. `open_questions` contains only blocking Mission semantic uncertainty; reasonable
  defaults enter assumptions, Control/Local-How questions are excluded, and multi-question answers
  require an explicit current question identity for exact reply binding. It excludes live Node/Resource,
  lease, reservation, calendar, and Runtime-attempt facts; Memory metadata never claims content was
  read. Interpreter, Planner, Reviewer, and Repairer share the same snapshot, while Controller
  freshness and source provenance remain intact. Mission Request v0.3 introduced the separate
  Planner, Reviewer, and Repairer ports, persists revision-bound structured review evidence, bounds
  automatic repair, and routes missing user facts back to clarification instead of letting Repair
  invent them. Structured DialogueTurn history remains separate from internal review evidence, and
  context-aware approval rules persist the exact reasons bound to an immutable draft.
  Planner and Repairer strict provider schemas bind `mission.id` to the current Request;
  local identity validation remains authoritative. A violating raw/normalized draft is
  persisted as bounded rejected-draft v0.3 evidence and fails without regeneration or
  Controller submission; transport/authentication faults have no invented draft evidence.
- MI recovery observations v0.3 preserve typed, digest-bound recovery decisions outside the
  unchanged Mission Request v0.4. The Request Engine durably fences Controller submission before
  POST; ambiguous results allow only one read-only lookup per explicit retry, never another POST
  or dialogue replacement. Exact transmitted-body fingerprints and the durable Controller
  admission endpoint may reconcile acceptance; identity/status alone never proves it. The original
  POST fault stays intact. Only definitive 400/409/422 rejection permits unchanged resubmission.
  Unchanged-dialogue retries retain one frozen context; new dialogues archive the old review cycle.
  These observations do not grant physical-stop, resource, Runtime or execution-replanning authority.
  See ADR-0054 and ADR-0055.
- Optional operation progress uses the existing registered, operation-owned Node State export
  (`roboguide.execution-progress/v0.1`). Runtime retains bounded attribution/counters, original
  receive times and TTL; working, intentional waiting, blocked and unknown remain separate.
  A supplied stall interval interprets measured work only and never initiates recovery. See ADR-0056.
  The default-off Habitat navigation observer (ADR-0058) publishes bounded exact-attempt,
  operation-specific best-distance improvement via a fixed read-only HTTP workflow. Producer
  freshness is checked before State projection; wait has no counter, geometry gaps stay unknown.
  No progress observation proves arrival, official PDDL truth or safe independent Role stopping.
- Execution recovery is explicitly repeat-authorized with exact current owner and durable time/count
  bounds. Actual current-attempt Cancelled evidence precedes Control partial release and the normal
  Match -> Schedule -> Propose -> Commit -> Rebind path; Unknown and Cancel receipts retain ownership.
  Stopped-owner matching may retry the original eligible Node while preserving Actor authority;
  ordinary replacement matching still excludes it. Same-owner Rebind creates a new attempt once.
  Expired pending recovery Commit is aborted by Control; unavailable candidates remain observable
  pending. No automatic stall cancellation or execution-time MI plan rewrite is provided. See ADR-0057.
- Individual Role recovery additionally requires dispatch-frozen exact operation/LocalSystem support
  for isolated execution stopping and context-preserving repetition, unchanged in current registration.
  The bounded, closed LocalSystem metadata contract `roboguide.local-execution-recovery/v0.1`
  uses existing Node Protocol/Contract transport; legacy absence never grants support. Recovery Cancel,
  partial release, replacement Matching/Commit/stateful Rebind and Execute delivery recheck support.
  Default Habitat shared-world declares `execution-group` / `unsupported` and fails Role recovery
  before Cancel; normal dual endpoints and single-Actor next-Task execution remain supported.
  Recovery support is neither repeat permission nor physical-stop/resource authority. See ADR-0059.
  Optional default-off shared-world continuation retains the same world after actual joint
  cancellation (ADR-0060). Exact session/intent/new-attempt admission, unchanged simulator budget,
  a world-wide 16-continuation ceiling, completed-peer passive wait and restart-before-reset fencing
  belong to the Local EAIOS. Stop snapshots remain separate from final official benchmark evidence.
  Group continuation is separate from the existing isolated Role recovery API;
  neither local admission nor a continuation declaration authorizes
  repetition, resource release, reassignment or a new simulator world.
  Explicit Group recovery (ADR-0061) freezes the complete current attempt set and per-operation
  repeat permission with immutable time/count limits. Actual full-set stopping precedes Control
  revalidation of the retained original bindings, Actor identity, operation support and exclusive
  resources. No ownership is partially released; fresh attempts are prepared atomically and only
  delivered after checkpoint Commit and a fresh clock/support/current-set check. Completed peers
  remain Completed. The first profile requires one immutable independent Execution Session;
  migration and coupled semantic recovery remain unsupported. Ordinary Mission cancellation
  overrides the recovery round. Controller checkpoint v18 preserves the purpose and accepts
  older checkpoints with no hidden Group permission. Single-Actor retained-world continuation has
  real physical coverage; multi-Actor physical continuation remains unproven. Actual Controller/
  Node process conformance covers both all-live members and preservation of a Completed peer.
  The B1 launcher default-off `ROBOGUIDE_B1_RETAIN_STOPPED_SESSION` derives only run-local recovery
  metadata, validates actual Node configs, and freezes/compares byte identities against both
  adapters' bounded read-only support before registration. It never requests recovery by itself.
  `tools/quality/check_group_continuation_processes.py` owns optional zero-Provider, zero-simulator
  actual Controller/Node process conformance with synthetic Local EAIOS outcomes and fresh local
  logs/journals/checkpoints; its authored fixture plans are never B1 experiment results.
- Mission Responses adapters share a startup-frozen, configuration-owned satisfaction policy with
  reference/digest provenance. Generated verifier freshness must match that policy; absent policy
  supplies no numeric default. Verification names do not prove affirmative outcomes, and policy
  admission never proves generic verifier evidence will arrive. An optional, deployment-owned
  terminal verifier source now feeds exact Task/Role/physical-attempt evidence into the existing
  Orchestration satisfaction boundary; its official Habitat projection lives only in the Local
  EAIOS adapter, while Controller and B1 provenance validate the neutral source/verdict contract.
  Missing evidence never becomes local completion or benchmark success. See ADR-0039 and ADR-0048;
  MissionPlan, Grounding, and Control commitment authority remain unchanged.
- The B1 shared-world Mission config opts into authoritative-goal confirmation (ADR-0049):
  every DAG-terminal Task in a generated plan must use the exact full frozen goal and configured
  verifier contract with `verifier-evidence`. Prerequisites may retain `execution-report` for their
  own local effects. This is a Mission policy, not an Actor count, Node selection, or benchmark
  admission rule; unsupported goal syntax fails before a Provider call.
- `evaluation/` contains the RoboGuide Eval Harness, an independent evaluation infrastructure
  outside Core, Runtime, Control Plane, State & Memory Plane, and Local EAIOS. It owns
  ExperimentSpec contracts, external-process orchestration, run manifests/metrics/trace
  evidence, and the `roboguide-eval` CLI. It never imports EMOS/Habitat-MAS packages, never
  re-implements their internals, reaches external systems only across process boundaries
  (machine-specific Conda/working-directory/credential configuration stays in Git-ignored
  `evaluation/local.yaml` or `ROBOGUIDE_EVAL_*` variables), never modifies Core contracts or
  Proposal/Commit/Binding/Runtime semantics, and never commits real experiment results. The
  RoboGuide system runner drives the real Controller/Node/Runtime/Local-EAIOS path and reduces
  persisted evidence; it never bypasses RoboGuide to call a simulator skill.
  Its process-only `e1-batch` supervisor runs frozen external pair commands. Exclusive
  lifetime-bound worker receipts forbid outcome retries after restart; SUT failures continue,
  while source/evidence drift and three consecutive infrastructure pairs pause new dispatch.
  The first measured pair gates optional two-worker capacity. Port isolation is deployment
  configuration, never a Core or Mission semantic change. See `evaluation/docs/e1-batch.md`.
  Explicit driver-owned HTTP continuation policies retain all Provider incidents and never add
  client retries or prove success. Ordered owned-session termination lets B1 archive before
  services stop; bounded accounting draining must precede final evidence sealing.
- Optional B1 goal-geometry diagnostics read the terminal Habitat snapshot and its actual
  `robot_at_thresh` to compare the deployed 3D `any_at` result with a world-X/Z-only
  counterfactual. They require matching provenance and official terminal evidence, remain
  unavailable on missing/mismatched inputs, and never enter Formal admission, Mission
  satisfaction, or benchmark success. The Local EAIOS records the threshold read-only;
  evaluation owns the offline calculation and sidecar.
- Opt-in Habitat physical diagnostics v0.5 observe the original Oracle target and
  pathfinder calls once, record their actual selected navigation point and path
  query result, and use exact read-only semantic-region containment for bases and goal entities.
  They never issue a second path query, choose a route, or alter official success;
  ambiguous or unsupported observations remain unavailable. Evaluation accepts
  archived v0.4/v0.5 and current v0.6 terminal geometry evidence. Diagnostics v0.6
  additionally copy original step-filter requests/returns and base-update positions
  through single-call, exception-isolated instance taps. Bounded per-step call
  records retain exact supplied canonical attempt identity, explicit unavailable
  fields/loss and pending failed-step evidence; they never prove collision,
  arrival, route feasibility or benchmark success. Action/mesh scalars describe
  the observed deployment navigation model, not hardware certification.
- `evaluation::e1_pair` is a process-only paired-driver consumer, with bounded
  credential-redacted Provider body evidence, frozen source/port/workload gates and actual-reset
  comparison. Its separate `habitat_local_eaios.native_workload` entry point selects an exact
  original dataset episode before environment construction, then observes original reset/step
  results and cached official metrics without additional physical/RNG/model calls. It does not
  change native Leader/Stage2 decisions, B1 admission or Pair Validator dimensions. See
  `evaluation/docs/e1-pair.md`; experimental manifests/results remain local. Each arm uses
  byte-verified private vendor code copies, preserving production checkout containment and
  exact child-module origin checks. Bounded native progress proves only successful-step lower
  bounds after abrupt worker death, never official terminal truth or non-execution.
  Post-construction workload observation isolates argument reads as well as JSON/storage;
  source selection and the original constructor still fail closed. The adapter-owned
  `native_usage_observer` isolates only the previously added usage-detail callback in explicit
  native observers and controlled Stage2, with original required token accounting outside the
  diagnostic boundary. It never edits vendor sources or changes requests, decisions, skills,
  original history persistence or official metrics. Bounded loss sidecars are best-effort,
  never proof of complete Provider accounting; absent original callbacks stay unchanged.
- The opt-in `habitat_local_eaios.native_reset_observer` entry point decorates the original
  EMOS environment factory inside its workers, preserving the first returned reset with the
  existing bounded diagnostic reader. It never edits the external source, invokes another
  reset/action/step, changes multiprocessing mode or fabricates simulator RNG state. Evaluation
  still reaches vendor code only across the external-process boundary. The harness accounting
  proxy observes returned model identity independently of the requested model; a missing identity,
  dropped log or unclosed/in-flight observer cannot prove complete pairing evidence.
- `console/` contains the experimental read-only Mission Journey visualizer
  (early development; layout and features are still evolving): a zero-dependency
  static frontend (`index.html`, `js/`, `css/`) plus `serve.py`, a stdlib static
  server with same-origin `/proxy/controller|mission` reverse proxies. It only
  consumes existing Controller/Mission Service HTTP APIs (event evidence log,
  inventory, mission/calendar projections, and the existing Mission submit/cancel
  endpoints); it never becomes a second Control, Runtime, State, Node Protocol, or
  Memory authority and introduces no Node toolchain. Built-in demo replay events
  mirror the exact `core/domain::EventPayload` serde shapes for offline display
  but are never real evidence.
- `website/` contains the MkDocs Material technical documentation site. At build
  time the stdlib-only `website/sync_docs.py` mirrors a curated list of repository
  documents into the gitignored `website/docs/`, renames mirrored `README.md`
  files to `index.md`, rewrites internal links, falls back to GitHub URLs for
  intentionally unmirrored repository paths, and generates the ADR overview page
  and the literate-nav `SUMMARY.md`; it never edits source documents. Site builds
  run `sync_docs.py` and then `mkdocs build --strict`, so broken internal links
  fail the build. The site deploys through Cloudflare Workers Builds as free
  Workers static assets (`website/wrangler.jsonc` points at `website/site/`);
  it never becomes a second authority for any runtime contract; it only
  publishes existing repository documents.
- `contracts/mission/` stores versioned cross-language contracts; `config/` stores
  non-secret runtime configuration; `scenarios/` stores deterministic artifacts.
  The Formal B1 RoboGuide scenario runner is workload-generic: it extracts the
  executed episode, simulator seed, and dataset identity from the frozen B1 input
  document (`roboguide_eval.b1_workload`), forwards the seed as `habitat.seed`,
  and records the consumed seed plus the actually observed initial agent positions
  in the shared-world summary identity. No episode is embedded in the runner; the
  episode-51 input remains one regression fixture.
- `contracts/capability/v0.3/` is the deployment-independent Canonical Capability Catalog consumed
  by Mission Intelligence. It separates capabilities/attributes from operations/parameters and
  validates exact requirements without current providers; live Node inventory remains Control
  eligibility evidence. The current catalog does not freeze embodiment taxonomy, structured entity
  schemas, or Catalog distribution/negotiation. Operation `required_capabilities` are direct
  provider-level feasibility baselines and never an expansion of integrated Local EAIOS steps.

ADR-0094 adds deployment-owned bounded `observation.acquire@v1`, separate from
snapshot verification. Its opt-in Local How v0.16 retains v0.11 exact-attempt cached condition/budget
feedback and a disclosed at-most-16-step original-wait mapping; it neither edits raw model
choices nor guarantees persistent effects. Already-observed acquisition needs no extra model
or motion. A guarded fresh acquisition navigation call rearms only its endpoint's original
completed-command latches; arrival is recomputed by the original action with its target cache,
pathfinder and budgets intact. It never writes a true sensor value or establishes visibility.
Earlier Local How versions retain their own semantics. Default-off pre-dispatch
Stage2 model reselection remains separately bounded (at most three per
bound attempt; no Provider retry or action rewriting). Reset geometry v0.4 is neutral,
source-bound world evidence, not inventory or a route guarantee. Feasibility v0.6 checks
final-verifier resource lifetimes without selecting endpoints; closure observations record
actual publication and preserve archival failures separately from SUT outcomes. See
`docs/extensions/bounded-observation-and-evidence-closure.md`. Physical release gates remain explicit.
Live failure continuation is unsupported: an actual local failure closes the session after active
dispatches finish or receive an existing explicit cancellation. Missing successors remain undispatched;
no Task success, Control release, Cancel receipt or physical result is synthesized. Session progress
v0.1 and separate unreported dispatch records retain parent/child delivery boundaries; only actual
child terminal coverage enters normal final verifier publication. Official truth remains unchanged.

The v0.13 acquisition tool `scan_for_object` is backed by an independently scoped local HRL
skill and verified original base-yaw interfaces. Only a real guarded model selection invokes it;
32-step/90-degree/1.5rad/s bounds clip to the original remaining acquisition budget. Zero
translation is commanded, no camera node is edited, and scan completion never proves detection.
Navigation/relocation/idle retain their tools. `perception_diagnostics.py` is default-off under
the existing physical-diagnostics flag; bounded same-cache pixel/threshold/camera comparisons
separate own-agent evidence, derived current-world union and unavailable official truth, with
no sensor/render/predicate calls. Failed final capture cannot skip buffered evidence flush.
`view_motion.py` adds exact-call post-step quaternion observations to scan receipts and subsequent
model inputs. A completed skill timer never proves rotation; fully observed stationarity and
unavailable evidence remain separate. No actuator, collision threshold, NavMesh or benchmark
predicate is changed. The v0.14 declaration is checked independently of legacy v0.13 evidence.
`view_recovery.py` and `view_recovery_binding.py` add a bounded same-entity local arrival-point
search only after an observed stationary scan and a fresh model-selected navigation. Original
route and nonempty noncylindrical scan-footprint checks must support a candidate; unknown geometry
or a bounded miss is a typed local recovery failure before movement, never a straight-line
fallback. Two searches, 24 candidates each, up to 32 original scan checks per candidate and at most
16 footprint points remain inside the original acquisition budget. A one-second Python search
deadline does not preempt blocking native calls. No target cache, external EMOS source, collision
threshold or official predicate is changed. Exact own-endpoint hooks are removed at the next
navigation or attempt close. Local How v0.15 discloses this controlled-arm capability; v0.14 and
earlier frozen artifacts retain their original contracts. Map-supported candidates do not prove
physical safety, arrival, detection or persistent satisfaction of another Task.
Activation is per endpoint's actual supported nonempty footprint; other controller interfaces
keep ordinary navigation. Readiness records support separately from unknown physical feasibility.
`observation_optics.py` owns the source-bound fixed primary pinhole camera declaration and
read-only loaded-camera checks. Optional Catalog attributes describe height above the kinematic
base, forward offset/elevation, horizontal/vertical FOV and absence of workflow pan/tilt; they
never claim visibility. The same typed facts enter Node registration and MI capability classes.
Local How v0.16 requires the run-local optics declaration, rejects mismatches before READY, and
uses fixed-camera sphere/frustum necessary conditions with at most 512 local grid requests,
64 route queries and 32 full scan candidates per original one-second search. The grid is
0.15m-spaced within 1.2m half-width; accepted displacement stays at most 1m and path length 1.5m.
Legacy v0.15 retains its radial declaration. No articulated camera is treated as fixed and no
unknown camera is assigned invented optics. Camera/target drift fences selected-point hooks.
Local acquisition is not persistent satisfaction; final joint goals require original independent
verification and proximity/detection need not belong to the same Physical Entity.


Keep `AGENTS.md` at the root. The bootstrap may create only the maintained paths
listed above. Create future paths only with their first implementation; never add
empty placeholders or a top-level `src/`.

## Build, Test, and Development Commands

ADR-0109 adds the inventory-free, read-only Controller `POST /v1/missions/validate-plan`
query (`roboguide.plan-validation/v0.2`, with explicit v0.1 read compatibility). The digest-bound
execution-planning evidence v0.2 publishes supported modes, DAG/Context-relation support and
session-stable Actor binding without inventory. Missing v0.1 fields never grant new support;
configured v0.2 topology must match the Controller response. Typed logical code/path feedback
separates mode, capability and retained-resource conflicts without rewriting real cooperation.
Published deployment/verifier checks are shared with
submission; the query never creates Groups, events, bindings or reservations. Configured MI
execution-planning evidence enables the query for every newly generated or repaired candidate.
Source drift and HTTP failure are system failures, while exact-plan negative findings retain
original output and use the existing bounded regeneration budget. Ordinary endpoint reuse and
timely per-Task verifiers must not inherit a global terminal-world barrier. See ADR-0109.

For documentation changes, run:

```bash
git status --short --branch
git diff --check
file docs/images/roboguide-v2-overall-architecture.png
```

The bootstrap pins Rust and Python toolchains. Python commands use the repository's
uv-managed environment. New implementation paths must update this file and
`README.md` when their ownership is established.

For Mission changes, run:

```bash
uv run ruff format --check mission apps/mission-service tools/quality integrations/robonix-map-service \
  integrations/habitat-local-eaios
uv run ruff check mission apps/mission-service tools/quality integrations/robonix-map-service \
  integrations/habitat-local-eaios
uv run mypy --strict mission/src mission/tests apps/mission-service tools/quality \
  integrations/memory-provider/roboguide_memory_provider \
  integrations/robonix-map-service/robonix-map-service.py \
  integrations/robonix-map-service/tests \
  integrations/habitat-local-eaios/habitat_local_eaios \
  integrations/habitat-local-eaios/tests
uv run python tools/quality/check_python_function_docs.py \
  mission apps/mission-service integrations/robonix-map-service integrations/habitat-local-eaios
uv run pytest -q
```

Habitat deployment compatibility additionally runs the real worker interpreter:
`PYTHONPATH=integrations/habitat-local-eaios <habitat-python> -m habitat_local_eaios.runtime_check`.
This zero-Provider, zero-simulator gate exercises canonical parsing, SQLite round trips and the
real loopback HTTP boundary. B1 runs it before starting SUT processes or calling MI; it is not
physical readiness evidence. Set `ROBOGUIDE_HABITAT_PYTHON` for the matching pytest subprocess test.

For evaluation harness changes, run:

```bash
uv run ruff format --check evaluation
uv run ruff check evaluation
uv run mypy --strict evaluation/src evaluation/tests
uv run python tools/quality/check_python_function_docs.py evaluation
uv run pytest -q
```

Recovery-tool and Memory lifecycle changes additionally run tests in `apps/recovery-tool/tests`
and `integrations/memory-provider/tests`, strict typing of `apps/recovery-tool/roboguide_recovery` plus
its tests, and the actual process gates above. The recovery CLI is a separate standard-library
Python application, never a dependency of Mission Intelligence or a substitute for online HA.

Memory process changes include `integrations/memory-provider/roboguide_memory_provider` in the main
strict-mypy source group. Check that package plus `integrations/memory-provider/tests` separately:
independent test roots contain distinct `conftest.py` modules. Run its Ruff/format/function-doc checks,
actual parent-death/old-writer gate and formal two-Node handoff cases. Isolated handoff SQL rollback
checks suspend the actual owned publishers and drain facts before each SQL window, then use the
real generation-bound recovery API. Compound SQL/reconnect cases retain their original safety holds;
do not loosen session fences to make a test pass.

Observability changes additionally check `tools/observability` with Ruff, strict mypy, function
documentation and its tests. Native Promtool/amtool validate generated configs; the real scrape/
firing/resolved gate and actual replica status endpoint regression must pass. Keep metric labels
fixed and bounded, loss explicit, and observation completely separate from authority decisions.

For documentation site changes, run:

```bash
cd website
uv run python sync_docs.py
uvx --from mkdocs --with-requirements requirements.txt mkdocs build --strict
```

## Coding Style & Naming Conventions

Use Markdown, relative links, UTF-8 Chinese, and lowercase-hyphenated asset names.

Every handwritten Rust `fn` and Python `def` or `async def`, including private and
test helpers, requires a useful documentation comment or docstring. Document
responsibility, effects, invariants, and failure behavior. Rust uses `rustfmt` and
Clippy with warnings denied. Python uses Ruff, strict typing, and Google-style
docstrings.

## Testing Guidelines

Core tests are deterministic and offline, use fake nodes and a virtual clock, and
cover normal, rejection, timeout, conflict, and recovery paths. Mark Isaac Sim,
network, model, and hardware checks as adapter/system tests. Cross-module behavior
produces an inspectable event trace. State tests keep observed facts separate from
Control eligibility policy and cover registration, freshness, health updates, and
cross-mission reads. Recovery tests must preserve partial versus whole-group release.
Runtime-State tests distinguish local reported health from system-observed liveness
and prove that new observations affect later Control decisions. Time tests must
cover source-clock divergence, receive ordering, heartbeat receive time, and lease
liveness without weakening Recovery lifecycle tests.
Reconciliation tests separate read-only assessment from mutation, preserve
unaffected bindings and Multi-Mission isolation, and keep missing replacements
Blocked/Pending rather than Failed.
Recovery pipeline tests prove candidate membership, proposal/commit separation,
atomic conflicts, committed-only rebind, and stable unaffected bindings.
Commitment lifecycle tests cover one-pending-per-role, stale handles, atomic Abort,
terminal cleanup, zero-resource roles, and Multi-Mission ownership isolation.
`tools/quality/measure_controller_pipeline.py` probes isolated production Controller processes with
bounded two-session synthetic protocol waves, retaining binary/source identity, stage costs, durable
completion denominators and all failures. It starts no simulator, Provider, hardware or E6.
`core/control/src/tests/ownership_model.rs` adds an independent lifecycle-aware allocation model,
fixed-seed interleavings, temporary counterexample retention and explicit extended/replay checks.
Its model preserves pending normal/recovery commitments and single-owner Context reuse.
Scheduler tests require stable joint node/resource/time choices, bounded backtracking,
CandidateSet confinement, normal/recovery policy consistency, decision-local exclusive-resource
avoidance, Context binding reuse, future admission/restore/due/cancel/overrun behavior,
deadline-derived activation bounds, Commit conflict protection, durable deferral deduplication,
NoSelection/NoFeasible/SearchLimited outcomes, and Decision/Proposal/Commit separation.
Allocation projection tests cover Committed/Bound/RecoveryPending, partial release,
Abort/Rebind/Release, orphan rejection, stable ordering, projection lag, and
Control-to-State one-way authority. Scheduler v0.2 must not read Allocation State.
Adapter tests cover contract-version rejection, wire/domain conversion, identity
matching, transport errors, intent round trips, and heterogeneous local mappings.

## Commit & Pull Request Guidelines

Use concise Conventional Commit subjects, for example `docs: define development
baseline`. Pull requests identify the V2 responsibility and module, explain failure
behavior, link ADRs/issues, and list checks.

Authorized AI tools may manage Git operations. GitHub must show the responsible
human as commit author/committer and PR author, never a tool or anonymous bot. The
human owner reviews the full diff and checks.

## Architecture and Scope

Preserve V2 semantics: Proposal differs from Commit; the Group Manager stays in
Control while the Group runs across Runtime and Nodes; State & Memory is horizontal;
Local Systems retain Immediate How and final safety. Do not freeze schemas,
transports, databases, algorithms, or hardware APIs without evidence and a decision.
Changing ownership, dependency direction, authority, lifecycle, or public contracts
requires an ADR; architecture-semantic changes update V2 first. Do not describe
Shared Node State Slice v0.1 as complete State, Belief, Allocation, or Memory.
