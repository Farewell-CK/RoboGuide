# Implementation status and evidence limits

Content baseline: `6c5a099bcda9ca1fce9f7039b0c4f53fed5e514b` plus this ADR-0155 implementation, reviewed 2026-10-22. This table uses code and retained acceptance records. See the [October 22 audit](docs/development/roboguide-branch-audit-2026-10-22.md).

| Area | Existing software paths | Remaining or independently validated work |
| --- | --- | --- |
| MI / Orchestration | Clarification, Review, admission, DAG, satisfaction, versioned revision and automatic MI work | Complex topologies, other Memory types and composed fault matrix |
| Control / quota | Match/Commit/Bind, calendar, recovery, atomic tenant/category quota and usage view | Divisible allocation, local enforcement of all resources, full space/time/cost evidence |
| Runtime / stop | Attempts/generations, durable intents, relations, whole-Group stop and original-process exit proof | Running preemption, coupled Role migration, hazard/regrasp and device stopping |
| State / verifier | Source policies, Belief lineage, TTL, original Task verifiers and CAS proof | Actual deployment sources, conflicts and continuous physical effects |
| Memory / Spatial | Preparation/use/revoke/handoff, file algorithms, provider-local GC; original import Replica, Catalog/lineage, Snapshot/Archive references, unpublished original Archive abort, private receiving-owner retirement/GC and recovery CAS checks | Source Artifact GC over all references, complete producer coverage, cross-host content and composed recovery |
| Replica / recovery | Certificate rotation, original child drain, V3 transfer, inode/writer fences and quiescent bundles | Binary/profile upgrade/rollback, online content recovery and cross-host fault matrix |
| Federation | Durable Control sessions | Actual cross-domain transport, authorization, routing and dual-domain recovery |
| Operations | Write-disabled, committed views, recovery probes and short real monitoring checks | Remaining listener/blocking paths, complete runbooks, long-term SLO and overall exit criteria |

Original content Archive and actual Catalog compaction/restart/cold recovery are recorded in [this acceptance](docs/development/acceptance/2026-10-22-original-content-archive.md). Storage v3 validates known consumers, original budgets and same-owner bytes together. These process checks create no device or external-model facts. The complete non-release remediation plan remains required.

[Unpublished original Archive cleanup](docs/development/acceptance/2026-10-22-original-archive-abort.md) adds actual writer fences, original tombstone/SQL receipts and actual v49-to-v50 upgrade verification. Partial files and receiving references remain; cleanup does not authorize deletion of receiving content.

[Private receiving-owner reclamation](docs/development/acceptance/2026-10-22-private-archive-reclamation.md) separately checks all original writers, an empty known-consumer inventory and the sole producer. The terminal storage v4 fence and original tombstone precede unlink. This v4 expresses only irreversible retirement and is not an ordinary writable CAS.

## Reading the evidence

- **Accepted contract** fixes semantics, not complete implementation.
- **Implemented code** establishes an actual path, not validation in your deployment.
- **Offline/process acceptance** proves the stated deterministic, file, SQL, TLS or owned CPU-child software behavior.
- **Deployment/physical validation** requires fresh actual sources, devices, topology and independent observation. Missing evidence remains unavailable.

Internal TaskSatisfied and Habitat's official metrics are separate judgments. Original experiment reports retain denominators, frozen model/code identity and fairness limits. Software checks do not imply benchmark gains.

## Maintaining status

Update both languages together, following contract → actual code → original acceptance → remaining gap. [Reference](reference.md) extracts versions. Merges, types, test doubles or Accepted status alone cannot announce SoftwareComplete.
