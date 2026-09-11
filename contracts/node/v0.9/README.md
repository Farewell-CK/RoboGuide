# RoboGuide Node Contract Release v0.9

This repository contract release keeps the established bidirectional session and command
lifecycle at `roboguide.node-protocol/v0.4` while advancing the semantic boundary to
`roboguide.node.v0.6`. Node configuration remains `roboguide.node-config/v0.7`.

Node Contract v0.6 closes the feasibility boundary introduced by v0.5:

- exact capability profiles carry readiness plus typed feasibility attributes;
- registration explicitly declares canonical `OperationSupport` separately from capability
  profiles;
- canonical invocations carry an `ExecutionIntent` containing `OperationRef`, semantic
  `objective`, and typed scalar parameters.

The checked-in [`../v0.8/roboguide-node.proto`](../v0.8/roboguide-node.proto) remains immutable.
The additive field in this release is admitted only after explicit Node Contract v0.6 negotiation.
A v0.4 registration uses `capabilities`; a v0.5 registration uses `capability_profiles` without
operation support; a v0.6 registration uses both `capability_profiles` and `operation_support`.
A v0.4 invocation uses the legacy `capability_contract` and `parameters` fields; v0.5 and v0.6 use
`intent`. The representations must not be mixed.

`OperationSupport` contains only the canonical operation identity and its Local EAIOS owner. It is
Control-visible feasibility evidence, not an operation catalog and not a local Skill, workflow,
service, route, command, or parameter binding.

An original v0.4 invocation remains valid on a negotiated v0.4 route. A semantic invocation is
downgradable only when its objective equals its canonical operation identity, which is the explicit
legacy-equivalent objective. A meaningful independent objective is rejected on v0.4 rather than
being silently discarded.

Protocol, semantic contract, configuration, and operation versions are independent:

| Concern | Current identity |
| --- | --- |
| Stream/session lifecycle | `roboguide.node-protocol/v0.4` |
| Registration/invocation semantics | `roboguide.node.v0.6` |
| Node Service configuration | `roboguide.node-config/v0.7` |
| Canonical operation | each `OperationRef.version` |

Verifier evidence is not added by this release. A later generic evidence-ingress slice may use
Node Protocol as one producer transport without granting Nodes Task-satisfaction authority.
See [ADR-0035](../../../docs/decisions/0035-node-semantic-profile-and-intent-boundary.md) and
[ADR-0036](../../../docs/decisions/0036-operation-support-and-integrated-capability-baseline.md).
