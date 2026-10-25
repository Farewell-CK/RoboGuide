# ADR-0215: Fair admission to the original application writer

Status: Accepted for implementation, 2026-10-26.

The original application mutex serializes Node facts, timers, Operator commands and Artifact
transactions. Actual replicated Federation requests showed several seconds between receive and
writer admission while repeated producers continued to commit. Transport capacity and a larger
socket wait cannot guarantee a waiting command gets a turn.

Use one process-local FIFO ticket writer at the existing composition boundary. Every producer
enqueues exactly once before entering the original transaction. Releasing a turn admits the oldest
waiting original ticket; an immediately returning producer cannot overtake it. The guard remains
non-Send and lasts through the same original authorized Commit/rollback. Actual TLS/configuration,
tenant, epoch/quorum, ownership and original-clock checks still happen after admission and at their
existing final boundaries. Waiting never renews a command, stop, import or Federation budget.

A panic while holding a turn poisons the writer monotonically and wakes queued callers to refuse.
Ticket exhaustion refuses without wrapping. A process restart creates only a new runtime queue;
all durable commands, uncertain outcomes and startup-recovery fences remain in their original
authorities. No queue ticket enters a checkpoint or constitutes permission, reservation or device fact.

The native repeated-producer/queued-command, SQL refusal, original ownership and complete HA gates
exercise the same writer. Unit counterexamples check actual thread ordering, panic poison and
nonwrapping arrival identity. Software scheduling fairness does not establish hard realtime,
cross-host latency, hardware stopping or long-term SLO.

Replica TCP and Federation transports also disable Nagle for their already bounded separate frame
and TLS writes. This changes packet scheduling only: every original frame limit, actual identity,
fresh quorum check, byte hash, network timeout and Unknown rule remains mandatory. Actual latency
and original failure records are reported separately from these implementation choices.
