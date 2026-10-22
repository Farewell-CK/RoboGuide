# ADR topics and decision summaries

Start from a system concern. Summaries guide reading; original ADR numbers, status, compatibility and history remain authoritative. The complete catalog is generated without a handwritten count.

[All ADRs](docs/decisions/index.md) · [Acceptance archives](archives.md)

## Objects and identity

Actor supplies logical continuity, PhysicalEntity identifies the deployment executor, and Node bounds routing/capability.

[ADR-0040, original language](docs/decisions/0040-mission-actor-binding-semantics.md).

## Commitment and recovery

Proposal owns no resources, Commit creates obligations, and Rebind consumes an already committed recovery assignment.

[ADR-0004, original language](docs/decisions/0004-recovery-commitment-lifecycle.md).

## Completion and satisfaction

Local failure differs from a final negative verifier. TaskSatisfactionRejected does not become execution failure.

[ADR-0093, original language](docs/decisions/0093-task-satisfaction-rejection-and-evidence-projection.md).

## State and evidence

Explicit source policies, original lineage, TTL and exact Task verifiers govern actual consumers.

[ADR-0104, original language](docs/decisions/0104-state-consumers-belief-and-semantic-verifier.md).

## Memory preparation

Persist original context/provider/budgets; Prepare/import/query/availability precede Execute.

[ADR-0105, original language](docs/decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md).

## Memory use and revocation

Current use leases and scoped release remain separate from historical placement and pin original backend proof.

[ADR-0106, original language](docs/decisions/0106-group-memory-use-leases-and-active-map.md).

## Read-only Unknown reconciliation

Original revoke budgets/Unknown remain immutable; bounded independent queries append original-provider evidence without repeating effects.

[ADR-0107, original language](docs/decisions/0107-original-revoke-observation-and-provider-progress.md).

## Group content handoff

Publish a lineage-bound child after complete original stop/revoke; target preparation and explicit commit gate execution.

[ADR-0108, original language](docs/decisions/0108-durable-group-memory-handoff.md).

## Version and integration fences

Preserve published Memory fields, separate attempt generation and refuse ambiguous fork-authority migration.

[ADR-0111, original language](docs/decisions/0111-branch-integration-and-authority-version-fences.md).

## GC and quiescent recovery

Actual writer leases protect declared SQL/content backup; restore uses new roots without starting services or renewing budgets.

[ADR-0113, original language](docs/decisions/0113-quiescent-recovery-bundles-and-node-writer-lease.md).

## Runtime revision

Original Mission goal and stop/revoke gates precede a new Group while preserving completed facts and ownership history.

[ADR-0120, original language](docs/decisions/0120-versioned-runtime-plan-revision.md).

## MI work and Memory

Original scoped Memory declarations, independent Review, complete stop/revoke and actual CAS precede new-scope publication.

[ADR-0126, original language](docs/decisions/0126-scoped-memory-for-runtime-plan-revision.md).

## Executable deployment inputs

Closed profiles declare actual components/stores/TLS; inspection never Registers or Executes.

[ADR-0129, original language](docs/decisions/0129-executable-deployment-profile.md).

## Original Replica child drain

Actual spawn/PID/boot/start-tick and OS wait journals establish exit of the original transfer incarnation.

[ADR-0138, original language](docs/decisions/0138-original-controller-child-drain-before-transfer.md).

## Logical quota

Original Role units and Mission tenant are accounted atomically at Commit/release without claiming GPU/OS subdivision.

[ADR-0140, original language](docs/decisions/0140-control-resource-quota-commitment.md).

## Active references and CAS

Active Memory/map/handoff references must resolve to actual bytes in the same bundle; missing content or tombstone conflicts refuse.

[ADR-0145, original language](docs/decisions/0145-recovery-active-content-reference-binding.md).
