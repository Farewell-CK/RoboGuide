# ADR-0228: Controlled Local EAIOS execution lifecycle

Status: Implemented; software acceptance and new physical acceptance remain separate.

## Context

Original EMOS's arm controller captures its reset target on its first step. Its
HRL skill separately samples a reference on its first reset command, possibly
after a failed manipulation. The skill may report completion at that entry pose
and fail to recognize its actual controller target. The final timeout does not
prove why the first manipulation failed.

Short controlled acquisition pauses can produce many model turns without a
changed condition, while the original client forwards all accumulated history.
Original CrabAgent inboxes are also indexed by agent name rather than Task attempt.
Working-context bounds and endpoint reuse need explicit lifecycle contracts.

## Decision and ownership

Local EAIOS owns these changes under explicit Local How v0.21. Its
`predecessor_profile` records the previous generation, not the effective
reset/context/pause policy. Only the named lifecycle fields supersede it. Runtime
preflight and independent evaluation require the exact new declaration before
checking the complete predecessor. Unknown, nested and mistyped scopes refuse;
historical generations retain their meaning.

The profile is emitted only by live deployments already opting into bound
relocation or acquisition. Native EMOS and legacy shared-world execution install
none of these policies. No Core, Node, MissionPlan, MI Prompt, canonical operation,
admission or intelligent-supervisor authority is added.

### Reset completion

`reset_completion.py` binds the original mapped reset to its exact endpoint,
invocation/attempt, tool-call ID and increasing sequence. Duplicate, stale, peer,
changed-world and changed-controller references refuse. It resolves the actual
`ResetArmAction.arm_ctrlr` and original `ResetArmSkill` interfaces.

During that skill's existing completion check only, the controller's actually
initialized `_init_joint_pos` supplies its comparison reference. The original numerical
check, tensor type, tolerance and exceptions remain; the prior reference is restored even on
error. Until the controller has actually initialized, completion is withheld. No
extra controller step or invented home pose initializes it. Changed or missing
initialized targets fail closed. Actuator commands, budgets and physics remain
original; actual budget expiry still fails. This does not guarantee reset or pick.

### Bounded working context

`stage2_context.py` scopes the existing synchronous SDK request of a controlled
attempt. System instructions, the initial local planning exchange and current
observed input are mandatory. Up to eight recent whole exchanges fit within a
conservative 128 KiB message bound. Tool calls and receipts stay together;
malformed retained exchanges refuse. Oversized mandatory input fails locally
before Provider I/O, never silently dropping task goals.

Full original `chat_history` and archival remain intact. Selected message objects,
response, model, endpoint, tools and other request parameters are unchanged.
Retained/omitted counts and byte bounds are attributed to the current attempt.
The hook is removed on normal and exceptional exit. A maximum of 16,384 original
exchanges creates no retry or renewed execution budget. A smaller context does
not prove identical future model choices or improved physical success.

### Sensor-responsive waits

New acquisition feedback v0.3 maps a model-selected wait/message to the original
500-step maximum, clipped to the original remaining acquisition budget. Its
offered tool description and dispatch disclose the same duration. The existing
loop still checks actual cached conditions after every existing Gym step and can
complete immediately on the observed current condition. No extra sensor calls,
steps, model calls, historical truth latch or automatic exploration is added.
Navigation and scan remain model-selected. Older feedback keeps sixteen-step
pauses. The exploration cadence can change and requires physical validation.

### Attempt inboxes

Before a new attempt and after a terminal attempt, the private live child clears
only that endpoint's unread original inbox. Peer inboxes remain. Evidence records
the boundary, scope and unread count; original sender-attempt provenance is
unavailable, and sender tool-call records remain raw message evidence. Cleanup
does not acknowledge delivery, delegation or completion. Failed recording cannot
authorize stale delivery. This is scoped to the existing single-world child,
not a cross-process message service.

## Validation and limits

Tests cover independent reset references, unavailable targets, exact feedback
composition, peer/world drift, original errors, storage failure, SDK message
pairing, oversized mandatory input, raw-history retention, independent profile
readers, live-loop per-step conditions and Task/peer inbox isolation. Original
numerical methods and archived requests also replay under actual Habitat Python
3.9 with zero Provider or simulator calls.

These are software facts. Physical convergence, visibility, initial manipulation
geometry, original horizontal-only navigation arrival and Provider failures remain
separate. New physical acceptance freezes the code, effective profile and actual
source of both arms. Historical outcomes are not replaced, the paused batch is
not restarted, and no benchmark success follows merely from these tests.
