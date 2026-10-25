# Implementation status and evidence limits

Content baseline: `fe82daec`, reviewed 2026-10-25. [Reference](reference.md) extracts current source versions.
[Quantitative allocation](docs/development/acceptance/2026-10-25-quantitative-resources.md) and
[application generation upgrades](docs/development/acceptance/2026-10-25-application-generation-upgrade.md)
pass actual software process acceptance. The complete non-release scope and independent device,
cross-host and long-term evidence requirements remain; SoftwareComplete is not declared.

| Area | Existing software paths | Remaining or independently validated work |
| --- | --- | --- |
| MI / Orchestration | Clarification, Review, admission, DAG, satisfaction, versioned revision and automatic MI work | Complex topologies, other Memory types and composed fault matrix |
| Control / quota | Match/Commit/Bind, exact quota/release, source costs, divisible Compute/Context envelopes, Node admission and recovery; explicit spatial footprints/shared time domains, original booking budgets, recovery deadlines and v0.27 cold recovery | Cross-domain/HA clock composition and actual GPU/traffic or other deployment measurements |
| Runtime / stop | Attempts/generations, durable intents, relations, whole-Group stop, original-process exit proof, reviewed running preemption with atomic beneficiary admission, coupled recovery and active hazards; v59/v0.28 recovery | Device handoff/regrasp/stop observations and global composition |
| State / verifier | Source policies, Belief lineage, TTL, original Task verifiers and CAS proof | Actual deployment sources, conflicts and continuous physical effects |
| Memory / Spatial | Preparation/use/revoke/handoff, file algorithms, provider-local GC, complete scoped typed-map/current-use/handoff/automatic MI/original-goal/cold-recovery/old-writer composition; original import Replica, Catalog/lineage, Snapshot/Archive references, relocated provider Snapshot retirement, Archive abort/private recipient GC, source upload retirement and offline GC over supported standalone reference closure | Complete global Archive/reference coverage and cross-host content |
| Replica / recovery | Certificate rotation, original child drain, V3 transfer, inode/writer fences, supported quiescent bundles, qualified Root reads, Prepared recovery, cross-owner upload release and original recovery assessment, Root8/parent10, Log/Snapshot/learners, final member GC, v55-to-v56 rolling/bad-target repair and v0.25 three-leader cold recovery | Complete global content/reference composition and cross-host fault matrix |
| Federation | Actual independent participant HTTP, tenant/domain mTLS grants, original prepare/commit/abort receipts and restart reconciliation | Durable coordinator, real peer requests, resource/epoch and cross-host recovery; global dispatch remains disabled |
| Operations | Write-disabled, committed views, recovery probes and short real monitoring checks | Remaining listener/blocking paths, complete runbooks, long-term SLO and overall exit criteria |

Original content Archive and actual Catalog compaction/restart/cold recovery are recorded in [this acceptance](docs/development/acceptance/2026-10-22-original-content-archive.md). Storage v3 validates known consumers, original budgets and same-owner bytes together. These process checks create no device or external-model facts. The complete non-release remediation plan remains required.

[Unpublished original Archive cleanup](docs/development/acceptance/2026-10-22-original-archive-abort.md) adds actual writer fences, original tombstone/SQL receipts and actual v49-to-v50 upgrade verification. Partial files and receiving references remain; cleanup does not authorize deletion of receiving content.

[Private receiving-owner reclamation](docs/development/acceptance/2026-10-22-private-archive-reclamation.md) separately checks all original writers, an empty known-consumer inventory and the sole producer. The terminal storage v4 fence and original tombstone precede unlink. This v4 expresses only irreversible retirement and is not an ordinary writable CAS.

[Explicit legacy Catalog adoption](docs/development/acceptance/2026-10-22-original-catalog-adoption.md) binds the original raw publication envelope, actual owner/bytes and typed parent, retaining current admin/TLS evidence separately. Historical publisher/tenant remain unavailable; no extra read/use/import permission is added. Wrapper v51/recovery v0.15 refuses missing original receipts.

[Offline source Artifact reclamation](docs/development/acceptance/2026-10-23-source-artifact-reclamation.md) uses wrapper v52/storage v5/recovery v0.16. Uploads, Catalog/child, Memory/MI, Snapshot/Archive and verifier claims are checked independently before the original tombstone and unlink. Actual MI policy revocation, independent original-goal evidence, parent/child release, file/SQL faults and cold restore pass. This entry refuses Replica/online authority and supplies no majority content ACK.

[Prepared recovery and repeated-validation optimization](docs/development/acceptance/2026-10-24-prepared-recovery-and-runtime-latency.md) passed actual three-replica/Controller/SQL/CAS fault checks with zero devices. Original Prepared and Unknown evidence remains; an independent normal upload establishes the new owner's Completed result. Current permissions and a fresh complete-quorum checkpoint protect failure observations. Final recovery took about 77 seconds and still needs optimization; short observations do not establish long-term SLO. Explicit recovery v0.18 supports the original standalone storage contracts, excluding parent8/Root4 and new recovery events.

[Cross-owner Prepared release](docs/development/acceptance/2026-10-24-cross-owner-prepared-release.md) also passed actual post-fault child wait/restart, DELETE and replay checks. The upload input is released while original origins, Unknown and independent Root pins/bytes remain. Completed retirement is covered by ADR-0182 below; complete online recovery remains required.

## Reading the evidence

[Original recovery assessment](docs/development/acceptance/2026-10-24-original-recovery-assessment.md) passed actual fault/restart/source-abandonment checks. Independent replacement completion and original Unknown remain separate; missing records stay unavailable and retirement receipts are explicit. It cannot fill completion or restart work; full Root binding/recovery remains required.

[Cross-owner Completed retirement](docs/development/acceptance/2026-10-24-cross-owner-completed-retirement.md) passed actual Root integrity reading, original-owner receipts, replay and independent SQL/file checks. The original upload reference lifecycle is closed; Root producer reclamation and complete online HA remain independently required.

[v53 standalone compatibility](docs/development/acceptance/2026-10-24-recovery-profile-propagation.md) passed ten actual GC SQL fault cases, adoption/source reclamation and Catalog/child/Archive cold recovery. Original v0.16 and parent8/Root4 fences remain. Actual software Nodes perform file/CPU work, with zero devices, external models or simulators.

- **Accepted contract** fixes semantics, not complete implementation.
- **Implemented code** establishes an actual path, not validation in your deployment.
- **Offline/process acceptance** proves the stated deterministic, file, SQL, TLS or owned CPU-child software behavior.
- **Deployment/physical validation** requires fresh actual sources, devices, topology and independent observation. Missing evidence remains unavailable.

Internal TaskSatisfied and Habitat's official metrics are separate judgments. Original experiment reports retain denominators, frozen model/code identity and fairness limits. Software checks do not imply benchmark gains.

## Maintaining status

Update both languages together, following contract → actual code → original acceptance → remaining gap. [Reference](reference.md) extracts versions. Merges, types, test doubles or Accepted status alone cannot announce SoftwareComplete.

[Complete scoped typed-map software acceptance](docs/development/acceptance/2026-10-25-scoped-typed-map-lifecycle.md) passes two actual retained-storage process chains and complete v0.26 cold recovery. Every Task has independent preparation/import/use; original production provenance remains unchanged. Device, cross-host and other stages remain independent.

[Spatial and Time software acceptance](docs/development/acceptance/2026-10-25-resource-topology.md) covers explicit deployment declarations, cross-ID conflicts, original budgets and active/uncertain-stop recovery. Actual CPU processes do not establish physical exclusion or device stopping. Remaining A/E/F work stays mandatory.

[Reviewed execution coordination](docs/development/acceptance/2026-10-25-execution-coordination.md) passes actual CPU preemption, coupled recovery, file-integrity hazards, original-instance restart and complete declared-data cold recovery. Device coordination retains reviewed Memory and independent device-assurance gates. No devices or external models were used.
