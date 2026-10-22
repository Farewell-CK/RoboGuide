# Implementation status and evidence limits

Content baseline: `45a82410867e01241394d1b9116aa53634ce2a06`, reviewed 2026-10-22. This table uses code and retained acceptance records; documentation work does not rerun hardware or expensive experiments. See the [October 22 audit](docs/development/roboguide-branch-audit-2026-10-22.md).

| Area | Existing software paths | Remaining or independently validated work |
| --- | --- | --- |
| MI / Orchestration | Clarification, Review, admission, DAG, satisfaction, versioned revision and automatic MI work | Complex topologies, other Memory types and composed fault matrix |
| Control / quota | Match/Commit/Bind, calendar, recovery, atomic tenant/category quota and usage view | Divisible allocation, local enforcement of all resources, full space/time/cost evidence |
| Runtime / stop | Attempts/generations, durable intents, relations, whole-Group stop and original-process exit proof | Running preemption, coupled Role migration, hazard/regrasp and device stopping |
| State / verifier | Source policies, Belief lineage, TTL, original Task verifiers and CAS proof | Actual deployment sources, conflicts and continuous physical effects |
| Memory / Spatial | Preparation/use/revoke/handoff, file algorithms, references/GC and active-reference recovery CAS checks | Global reference coverage, other providers, cross-host content and composed recovery |
| Replica / recovery | Certificate rotation, original child drain, V3 transfer, inode/writer fences and quiescent bundles | Binary/profile upgrade/rollback, online content recovery and cross-host fault matrix |
| Federation | Durable Control sessions | Actual cross-domain transport, authorization, routing and dual-domain recovery |
| Operations | Write-disabled, committed views, recovery probes and short real monitoring checks | Remaining listener/blocking paths, complete runbooks, long-term SLO and overall exit criteria |

## Reading the evidence

- **Accepted contract** fixes semantics, not complete implementation.
- **Implemented code** establishes an actual path, not validation in your deployment.
- **Offline/process acceptance** proves the stated deterministic, file, SQL, TLS or owned CPU-child software behavior.
- **Deployment/physical validation** requires fresh actual sources, devices, topology and independent observation. Missing evidence remains unavailable.

Internal TaskSatisfied and Habitat's official metrics are separate judgments. Original experiment reports retain denominators, frozen model/code identity and fairness limits. Software checks do not imply benchmark gains.

## Maintaining status

Update both languages together, following contract → actual code → original acceptance → remaining gap. [Reference](reference.md) extracts versions. Merges, types, test doubles or Accepted status alone cannot announce SoftwareComplete.
