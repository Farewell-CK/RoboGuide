# ADR topics and decision summaries

Start from a system concern. Summaries guide reading; original ADR numbers, status, compatibility and history remain authoritative. The complete catalog is generated without a handwritten count.

[All ADRs](../decisions/README.md) · [Acceptance archives](../archive/README.md)

## Objects and identity

Actor supplies logical continuity, PhysicalEntity identifies the deployment executor, and Node bounds routing/capability.

[ADR-0040, original language](../decisions/0040-mission-actor-binding-semantics.md).

## Commitment and recovery

Proposal owns no resources, Commit creates obligations, and Rebind consumes an already committed recovery assignment.

[ADR-0004, original language](../decisions/0004-recovery-commitment-lifecycle.md).

## Completion and satisfaction

Local failure differs from a final negative verifier. TaskSatisfactionRejected does not become execution failure.

[ADR-0093, original language](../decisions/0093-task-satisfaction-rejection-and-evidence-projection.md).

## State and evidence

Explicit source policies, original lineage, TTL and exact Task verifiers govern actual consumers.

[ADR-0104, original language](../decisions/0104-state-consumers-belief-and-semantic-verifier.md).

## Memory preparation

Persist original context/provider/budgets; Prepare/import/query/availability precede Execute.

[ADR-0105, original language](../decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md).

## Memory use and revocation

Current use leases and scoped release remain separate from historical placement and pin original backend proof.

[ADR-0106, original language](../decisions/0106-group-memory-use-leases-and-active-map.md).

## Read-only Unknown reconciliation

Original revoke budgets/Unknown remain immutable; bounded independent queries append original-provider evidence without repeating effects.

[ADR-0107, original language](../decisions/0107-original-revoke-observation-and-provider-progress.md).

## Group content handoff

Publish a lineage-bound child after complete original stop/revoke; target preparation and explicit commit gate execution.

[ADR-0108, original language](../decisions/0108-durable-group-memory-handoff.md).

## Version and integration fences

Preserve published Memory fields, separate attempt generation and refuse ambiguous fork-authority migration.

[ADR-0111, original language](../decisions/0111-branch-integration-and-authority-version-fences.md).

## GC and quiescent recovery

Actual writer leases protect declared SQL/content backup; restore uses new roots without starting services or renewing budgets.

[ADR-0113, original language](../decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md).

## Runtime revision

Original Mission goal and stop/revoke gates precede a new Group while preserving completed facts and ownership history.

[ADR-0120, original language](../decisions/0120-versioned-runtime-plan-revision.md).

## MI work and Memory

Original scoped Memory declarations, independent Review, complete stop/revoke and actual CAS precede new-scope publication.

[ADR-0126, original language](../decisions/0126-scoped-memory-for-runtime-plan-revision.md).

## Executable deployment inputs

Closed profiles declare actual components/stores/TLS; inspection never Registers or Executes.

[ADR-0129, original language](../decisions/0129-executable-deployment-profile.md).

## Original Replica child drain

Actual spawn/PID/boot/start-tick and OS wait journals establish exit of the original transfer incarnation.

[ADR-0138, original language](../decisions/0138-original-controller-child-drain-before-transfer.md).

## Logical quota

Original Role units and Mission tenant are accounted atomically at Commit/release without claiming GPU/OS subdivision.

[ADR-0140, original language](../decisions/0140-control-resource-quota-commitment.md).

## Active references and CAS

Active Memory/map/handoff references must resolve to actual bytes in the same bundle; missing content or tombstone conflicts refuse.

[ADR-0145, original language](../decisions/0145-recovery-active-content-reference-binding.md).

## Coupling modes and peer readiness

MissionPlan distinguishes `Independent`, `SequentialHandoff`, `ConcurrentCooperation`, and `TightlyCoupledCooperation`. Runtime enforces `SharedSpatialReference`, Group shared views, and two-ended `PeerChannelReadiness` fences while keeping high-frequency relative control in Local EAIOS.

[ADR-0026](../decisions/0026-execution-coupling-and-group-views.md) · [ADR-0027](../decisions/0027-runtime-coordination-evidence-completion.md).

## Cancellation settlement and explicit Unknown

Automated no-effect settlement and administrator reconciliation (`reconcile_manual`) never fabricate physical stop proof; missing local terminal outcomes stay non-terminal `Unknown`.

[ADR-0232](../decisions/0232-task-cancellation-settlement.md) · [ADR-0233](../decisions/0233-local-administrator-cancel-settlement.md) · [ADR-0250](../decisions/0250-explicit-local-execution-unknown.md).

## Hot enrollment, online publications, and storage governance

Covers explicit online adoption of static Node certificates, online entity/feasibility publication, streaming Catalog recovery projections (storage v2), and lossless body deduplication (storage v3).

[ADR-0239](../decisions/0239-online-static-node-identity-adoption.md) · [ADR-0242](../decisions/0242-online-deployment-publications.md) · [ADR-0243](../decisions/0243-catalog-recovery-checkpoint.md) · [ADR-0244](../decisions/0244-state-retention-and-deduplicated-storage.md).

## Conversation workbench and preflight extensions

`roboguide chat` provides read-only evidence queries, explicit submission intent, and a native agent loop; private Node preflight and dependency-window preflight validate workflow sources and DAG timing before admission.

[ADR-0251](../decisions/0251-readonly-conversation-workbench.md) · [ADR-0254](../decisions/0254-installed-chat-workbench.md) · [ADR-0255](../decisions/0255-private-node-plan-preflight.md) · [ADR-0256](../decisions/0256-complete-node-preflight-sources.md) · [ADR-0259](../decisions/0259-dependency-window-preflight.md).

## Telemetry and liveness slimming

Steady-state heartbeats advance process-local leases without SQL commits; `ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY` persists periodic diagnostics only on semantic change or max interval while always retaining decision, World, and safe-state records.

[ADR-0257](../decisions/0257-process-local-node-liveness.md) · [ADR-0258](../decisions/0258-periodic-node-telemetry-persistence.md).
