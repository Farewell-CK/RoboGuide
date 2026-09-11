# RoboGuide Node Contract Release v0.8

This repository contract release keeps the established bidirectional session and command
lifecycle at `roboguide.node-protocol/v0.4` while advancing the semantic boundary to
`roboguide.node.v0.5`. Node configuration advances independently to
`roboguide.node-config/v0.7`.

Node Contract v0.5 adds two semantics already present in MissionPlan v0.7 and Core Domain:

- exact capability profiles carry readiness plus typed feasibility attributes;
- canonical invocations carry an `ExecutionIntent` containing `OperationRef`, semantic
  `objective`, and typed scalar parameters.

The checked-in [`../v0.7/roboguide-node.proto`](../v0.7/roboguide-node.proto) remains immutable.
The additive protobuf fields in this release are admitted only after explicit Node Contract v0.5
negotiation. A v0.4 registration uses `capabilities`; a v0.5 registration uses
`capability_profiles`. A v0.4 invocation uses the legacy `capability_contract` and `parameters`
fields; a v0.5 invocation uses `intent`. The two representations must not be mixed.

An original v0.4 invocation remains valid on a negotiated v0.4 route. A semantic v0.5 invocation is
downgradable only when its objective equals its canonical operation identity, which is the explicit
legacy-equivalent objective. A meaningful independent objective is rejected on v0.4 rather than
being silently discarded.

Protocol, semantic contract, configuration, and operation versions are independent:

| Concern | Current identity |
| --- | --- |
| Stream/session lifecycle | `roboguide.node-protocol/v0.4` |
| Registration/invocation semantics | `roboguide.node.v0.5` |
| Node Service configuration | `roboguide.node-config/v0.7` |
| Canonical operation | each `OperationRef.version` |

Verifier evidence is not added by this release. A later generic evidence-ingress slice may use
Node Protocol as one producer transport without granting Nodes Task-satisfaction authority.
See [ADR-0035](../../../docs/decisions/0035-node-semantic-profile-and-intent-boundary.md).
