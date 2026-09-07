# RoboGuide Node Protocol Contract v0.7

This contract release advances the formal wire protocol to
`roboguide.node-protocol/v0.4` and the semantic Node Contract identity to
`roboguide.node.v0.4`. Node configuration does not change: the current configuration schema remains
[`../v0.6/node-config.schema.json`](../v0.6/node-config.schema.json).

Protocol v0.4 adds an immutable `command_id` to `Execute` and `Cancel`, plus a management-sequenced
`CommandReceipt`. A `COMMAND_PERSISTED` receipt proves that the Node execution journal durably
accepted the exact command before a Local EAIOS side effect. A `COMMAND_REJECTED` receipt reports
Node admission failure and becomes durable only when the Controller accepts and checkpoints that
observation; pre-journal validation failure is not claimed to live in the Node journal. Neither
status is an execution lifecycle result. Only ordered execution facts can prove Accepted, Running,
Completed, Failed, Cancelled, or Unknown.

The Controller checkpoints its command intent before routing it and retains the same command and
execution identities across same-process retries. After Controller restart, a nonterminal physical
attempt becomes `Unknown`; the Controller does not infer that a missing receipt means the command
was never executed and does not automatically replay it. Application composition submits the
ambiguity to Control's recovery pipeline before committing a replacement. The old attempt remains
in history; a replacement commitment does not prove that its physical work has stopped.

Node Service persists a cancellation tombstone even when `Cancel` arrives before `Execute`. A later
matching Execute is suppressed without invoking the Local EAIOS. A cancel receipt proves only that
this tombstone or request is durable; the Controller retains the Mission in `Cancelling` until
terminal physical evidence arrives.

All Protocol v0.3 State, Memory, peer-readiness, session, sequence, and application-accepted acknowledgement
semantics remain unchanged. Protocol v0.4 still carries no Memory payload, selective-import command,
peer data plane, or peer-channel setup command. The v0.2 endpoint remains rejection-only; v0.3 is
compiled for compatibility evidence but is not served as the current protocol.
