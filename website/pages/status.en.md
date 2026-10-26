# Implementation status and evidence limits

Product baseline: `f7f0b1f0`; recovery baseline: `cbf91ffd`, reviewed 2026-10-26. [Reference](reference.md) extracts source versions.
The [single queue](docs/development/roboguide-software-completion-plan-2026-10-15.md) and
[acceptance matrix](docs/development/nonrelease-acceptance-matrix-2026-10-26.md) retain the complete
non-release scope. Implementation and complete local composition acceptance passed, including HA16,
current typed-map/MI and Archive/GC/member/cold recovery. SoftwareComplete delivery requires
[current green CI](https://github.com/Farewell-CK/RoboGuide/actions/workflows/ci.yml) and main at
that same inspected SHA; page status cannot establish external Git/CI state.

| Area | Accepted software implementation and actual local evidence | Final or independent evidence |
| --- | --- | --- |
| MI / Orchestration | Original Request/provenance/Review, automatic runtime MI, original goals and complete scoped typed-map revisions; current chain rechecked | Deployed models and physical goal sources |
| Control / resources | Current-source costs, quota/divisible Compute/Context, POSIX proof, time/space/bookings, normal and recovery Commit, expiry stop/revoke and original HA budgets | Deployed GPU/traffic/physical enforcement |
| Runtime / coordination | Original attempts/intents/stop, running preemption with atomic beneficiary admission, coupled recovery, active hazards and HA composition | Actual device stop/handoff/regrasp |
| State / verifier | Source/conflict/TTL/Belief consumer policies, exact Task/attempt/CAS proofs and independent original goals | Current physical sources and continuous effects |
| Memory / Spatial | Per-Task import/use/revoke, Group handoff, Active Map, scoped typed-map/automatic MI, original producer/GC, full cold recovery and domain/Archive composition | Actual cross-host bytes and localization |
| Replica / recovery | Original child drain, V3 transfer, certificates, learner/joint, upgrades/refused rollback, complete content, online private Archive, original GC and v0.31 two-domain bundle recovery | Deployed RTO/RPO and cross-host continuity |
| Federation | Durable root/actual pinned peer/normal Control, partition Unknown/readonly stop reconciliation, certificate refusal, all-Commit barrier, online Archive/transfer and v0.31 two-domain cold recovery; explicit peer v0.3 | Actual cross-host evidence |
| Operations | All listener/local-adapter capacities and finite waits, real native alerts, default-128 health samples, explicit SLO denominators and runbooks | CI/main delivery gates; long-term SLO and human response |

## Historical acceptance records

The records below preserve their original versions and pending work at that time. Current closure
is tracked by the queue and matrix above; an old pending item does not erase later implementation.

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

[Cross-domain software acceptance](docs/development/acceptance/2026-10-25-federation-execution.md) passes normal execution, actual lost Prepare replies, original Root SIGKILL, completed-owner restart and six declared-data cold recoveries. Observation never claims instantaneous global readiness; execution consumes the original complete decision and all participant Commit receipts through current local gates.
