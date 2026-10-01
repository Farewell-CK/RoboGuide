# Local Execution Recovery v0.1

The optional LocalSystem metadata key `roboguide.execution-recovery` contains a
closed JSON profile described by [the schema](local-execution-recovery.schema.json).
It travels through existing Node Protocol v0.4 / Node Contract v0.6 metadata;
no new wire field, MissionPlan condition or resource authority is introduced.
Node config v0.7 validates the profile before opening a connection. Registration
admission also verifies exact operation ownership.

Each entry names one canonical operation and its actual cancellation scope:
`execution`, `execution-group`, or `unsupported`. Continuation is either
`repeat-after-stop` or `unsupported`. `repeat-after-stop` requires a new attempt
of the intact operation to preserve its required context and prior effects;
resetting a simulator world or silently replacing that context does not qualify.
An ordinary next Task after local completion is a separate ability.

The encoded profile is limited to 8192 bytes and 1..32 entries. Consumers enforce
unique operation identities, canonical identity grammar and the declaring
LocalSystem's exact operation ownership in addition to JSON Schema validation.
Missing or malformed declarations never imply support. A valid declaration can
explicitly report coupled cancellation or unavailable repetition.

Runtime freezes exact owner-qualified support at dispatch. Current registration
cannot retroactively upgrade a legacy attempt. Individual Role recovery requires
`execution` plus `repeat-after-stop`, unchanged current owner support, explicit
repeat authorization, original time/count budgets and actual current-attempt
Cancelled evidence. Control rechecks support for replacement Matching, Commit
and stateful Rebind. Prepared replacement Execute delivery also rechecks its
frozen declaration against current registration. Declaration alone never grants
any of those authorities.

The Habitat shared-world deployment declares `execution-group` / `unsupported`.
Its normal dual-endpoint and single-Actor sequential paths remain unchanged;
individual stopped-session retry is rejected before Cancel. Ordinary cancellation
still has its declared joint effect; operators must distinguish it from Role
recovery. General Group restart and interrupted-world continuation are not
implemented by this contract.

Controller recovery commands remain v0.1. The read-only recovery view advances
to `roboguide.execution-recovery-view/v0.2` and includes frozen/current deployment
support and a typed support disposition. Controller checkpoints advance to inner
v17 / wrapper v21 so older binaries cannot silently ignore the new safety fence.
Older checkpoints migrate with missing support left unknown; their budgets and
history are not rewritten and cannot authorize automatic replacement.
