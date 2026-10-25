# ADR-0211: Durable cross-domain coordination over original local resource authority

Status: Accepted and implemented; [local software-process acceptance](../development/acceptance/2026-10-25-federation-execution.md) passed. The complete final composed fault matrix remains required.

Root Orchestration owns a reviewed bounded global transaction. Each domain retains its own
Control reservations, local plan, State/verifier/Memory gates and Local EAIOS safety. No coordinator
creates Nodes, divides resources across unregistered owners or implements physical motion.

Explicit federation authorization v0.2 delegates execution coordination to actual enrolled service
leaves and stable principals. Old participant v0.2 receipts remain local metadata evidence only.
Execution v0.1 freezes the complete global manifest, original goal and exact canonical local plans.
Before Prepare, each real participant durably owns one original probe/nonce, actual current writer
epoch and local kernel budget. Prepare atomically admits the local plan through normal Control
Match/Commit/Bind, retaining a mandatory dispatch fence; no Execute precedes a global decision.
Prepared resources remain owned through response loss, partition, restart and uncertainty.

The root Controller retains each original outgoing intent before real pinned mTLS I/O. One bounded
owned worker performs peer I/O outside Controller and application writer locks. Uncertain sends are
observed only; they are never blindly repeated. The complete immutable Commit decision and original
participant/resource/epoch proofs precede the first Commit. Execution release requires all original
participant Commit receipts and current local epoch/readiness. An authenticated explicitly delegated
coordinator supplies this decision; a caller Boolean or ordinary participant-local Commit is insufficient.

Clock translation retains the actual original request/reply interval and participant clock samples,
with explicit deployment drift/uncertainty limits. Remote absolute TimestampMs is never compared
with local time. Each participant uses its own original receive/kernel budget; the root's conservative
remaining interval cannot extend its original budget. Expiry or lost authority fences delivery and
uses complete original stop/revoke before releasing any dispatched work. Metadata rollback cannot
undo a durable global Commit. Independent original local and global goal evidence remains separate
from all-local execution completion.

Controller v60 and recovery v0.29 explicitly retain all new probe, resource, coordinator intent,
clock and decision histories. Old wrappers cannot hide them. Cold restoration preserves uncertainty,
original clocks and complete references, never starts services or resends effects. All v59 cohort,
v58 topology, scoped Memory, Replica and archived-content requirements remain mandatory. Actual
independent Controller/TLS/SQL/software-CPU processes validate software behavior; actual cross-host,
device stopping, physical coordination and long-term SLO evidence remain independently unavailable.
