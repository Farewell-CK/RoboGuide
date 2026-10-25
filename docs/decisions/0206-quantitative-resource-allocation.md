# ADR-0206: Explicit quantitative resource allocation

Status: Accepted

## Authority and compatibility

Control owns allocation. A ResourceId continues to name the actual declared resource; an allocation
ID names a commitment and never creates another Node or device. Exclusive minimum-capacity
requirements retain their existing semantics. Explicit MissionPlan v0.10 and Node config v0.14 /
Node Contract v0.13 opt into divisible Compute units. Space and Time retain their independent
exclusive/interval meaning; their occupancy contracts are not reinterpreted as integer compute.

New allocation records freeze Node, resource, original Task/Role or ContextRole owner, tenant,
positive units, source capacity and original borrower identities. IDs hash typed canonical input.
Context claims retain the explicit original ContextRole resource envelope, never an inferred first-Task limit; each live Task borrows from it and exact Task release
ends only that borrower. A larger later demand needs a fresh Context/plan rather than silently
resizing the original grant. Unknown readers/attempts retain capacity. No timer or process restart
releases ownership or renews original permission.

Commit, Group Commit and recovery Commit validate the complete resource/quota batch before effects.
Bind/Rebind consume only exact committed claims. Task/Context/Group/Abort release only original
owners. Scheduler joint search and its future calendar account for allocation units alongside
exclusive occupancy; the observable Allocation view remains a projection of Control.

The Node receives versioned allocation grants outside semantic model parameters. It checks original
Task/Role/Group/Node, resource declarations and exact units before durable execution admission.
Original active and uncertain journal entries retain local capacity across restart. Local How keeps
operation locks and final safety. Logical units do not assert GPU partitioning, OS isolation,
hard realtime or physical stopping. Actual configured CPU workers retain their separate kernel
quota and independent exit evidence. Unsupported deployment enforcement stays unavailable.

Controller wrapper v56 and Node journal v14 fence older writers, including zero-event declarations.
Old v55 authority and original Node v13 rows migrate without granting historical shared units.
Old contracts cannot carry a new grant through compatibility normalization. Model default output
remains v0.8; explicit v0.10 must retain its complete selected schema.

Recovery v0.24 retains the complete original v0.23 Replica contract. It additionally supports an
explicit standalone v56 deployment: every retained Controller must actually declare no Replica
profile, repeated standalone authorities refuse, and no Root or source binding can be omitted
from a declared Replica set. Backup/verify/restore preserve exact original active ledger, quota,
Node grants and unknown outcomes without starting restored services. External Local EAIOS storage
is not silently added to a bundle or inferred from its receipt.

## Acceptance

Use arbitrary deployment inputs, never built-in device identities. Verify combined allocations,
over-limit atomic refusal, Context borrowing, exact release, normal/recovery lifecycles, future
intervals, original Unknown/restart and old-writer refusal. Actual software process evidence and
hardware evidence remain separate. This decision does not waive the other non-release stages.
See [actual acceptance](../development/acceptance/2026-10-25-quantitative-resources.md).
