# Implementation status and evidence limits

[Historical retained-Log content catch-up](docs/development/acceptance/2026-10-24-retained-log-content-catchup.md) passed the stable-membership native restart/mTLS/SQL/Root/failover slice and independent original Log/pin/CAS checks. Network Snapshots, learner/joint membership, first admission of archived sources, complete GC/upgrade and all other non-release requirements remain required.


[Complete Replica cold data recovery and native restart](docs/development/acceptance/2026-10-24-replica-content-cold-recovery.md) passed explicit v0.19, a second restore and three leaders' catchup/transfer/original-byte reads. Original intents/receipts, budgets and Unknown remain; zero Nodes/devices are added. Full online Archive/Snapshot/GC, absent historical-content routing, joint/upgrade/rollback and all other non-release matrices remain required.
Content baseline: `815ffcd` (ADR-0177–0180), reviewed 2026-10-24; [Reference](reference.md) records the full code baseline. This table uses code and retained acceptance records. See the [October 22 audit](docs/development/roboguide-branch-audit-2026-10-22.md).

| Area | Existing software paths | Remaining or independently validated work |
| --- | --- | --- |
| MI / Orchestration | Clarification, Review, admission, DAG, satisfaction, versioned revision and automatic MI work | Complex topologies, other Memory types and composed fault matrix |
| Control / quota | Match/Commit/Bind, calendar, recovery, atomic tenant/category quota and usage view | Divisible allocation, local enforcement of all resources, full space/time/cost evidence |
| Runtime / stop | Attempts/generations, durable intents, relations, whole-Group stop and original-process exit proof | Running preemption, coupled Role migration, hazard/regrasp and device stopping |
| State / verifier | Source policies, Belief lineage, TTL, original Task verifiers and CAS proof | Actual deployment sources, conflicts and continuous physical effects |
| Memory / Spatial | Preparation/use/revoke/handoff, file algorithms, provider-local GC; original import Replica, Catalog/lineage, Snapshot/Archive references, relocated provider Snapshot retirement, Archive abort/private recipient GC, source upload retirement and offline GC over supported standalone reference closure | Complete Raft/online producer coverage, cross-host content and composed recovery |
| Replica / recovery | Certificate rotation, original child drain, V3 transfer, inode/writer fences, supported quiescent bundles, qualified Root reads, Prepared recovery, cross-owner upload release and original recovery assessment | Root reader provenance, parent8/Root4 bundles, complete readiness/online recovery, upgrade/rollback, online GC and cross-host faults |
| Federation | Durable Control sessions | Actual cross-domain transport, authorization, routing and dual-domain recovery |
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
