# RoboGuide Node Contract v0.2

This directory owns RoboGuide Node Protocol v0.2. `roboguide-node.proto` is a formal gRPC
bidirectional streaming contract. Version v0.1 remains only as a historical artifact at
`../v0.1/`; production Server and Node Service compile and serve v0.2 only.

## Formal gRPC session

`RoboGuideNodeProtocol.NodeSession` uses the ordered lifecycle:
Hello -> Welcome -> Register -> Registered, followed by Heartbeat, RegistrationUpdate,
ExecutionEvent, and reconnect ExecutionSnapshot from the Node; Execute, Cancel, Ack, and Error
flow from RoboGuide. The exact protocol and Node Contract versions are selected by Welcome.

## Multiple local systems

One Node identity can report multiple `LocalSystemDescriptor` values. Each descriptor has a stable,
node-local ID plus runtime name/version and metadata. Every Capability, Sensor, and Resource names
one `local_system_id`; receivers reject missing or duplicate local-system IDs and references to an
unknown owner. A canonical capability contract has exactly one owning local system within a
registration. RegistrationUpdate is a complete replacement snapshot under the same rules.

These descriptors identify ownership only. CanonicalInvocation does not expose a local executable,
Atlas/Pilot concept, ROS topic, vendor SDK type, or other Local How.

## Declarative node configuration

[`node-config.schema.json`](node-config.schema.json) defines the strict
`roboguide.node-config/v0.2` shape consumed by `roboguide-node`, including local systems, fixed
HTTP/dynamic gRPC/MCP connections, per-system health observations, capability-owned workflows,
mappings, resources, and sensors. Every local system owns one fixed health step and nonempty,
mutually exclusive Online/Degraded/Offline state mappings; comparison is case-insensitive unless
`case_sensitive = true`. Unknown fields are rejected. At startup, the Node Service also compiles the
complete document and rejects invalid or duplicate identities, overlapping health states, duplicate
capability contracts, broken references, driver/operation mismatches, non-local endpoints, and
missing gRPC descriptor files before serving execution traffic.

The engine ships three generic local drivers: HTTP fixes method/path, dynamic gRPC fixes
descriptor/reflection plus service/method, and MCP fixes the `tools/call` tool name. Workflow
request bodies may read canonical invocation fields and earlier step responses through JSON
Pointer, with only closed conversion functions such as `quaternion_from_yaw`. Configuration cannot
select a shell command or let network input alter routing. A Local EAIOS whose API cannot be
expressed by these drivers must expose a local HTTP/gRPC/MCP facade owned by its deployment, not by
RoboGuide.

## Committed resources

Execute carries `resource_ids`: the node-wide IDs committed by Control for the selected Group/Role.
The IDs are an unordered set and duplicates are invalid. The Node verifies its configured required
resources are a subset before dispatch, but this local check neither grants nor revokes Control
reservation authority. The same `execution_id` can only identify one semantic tuple of canonical
invocation and resource-ID set across sessions; a conflicting reuse is rejected and never dispatched.

## Breaking evolution

v0.2 intentionally breaks the v0.1 Registration and Execute shapes: `NodeRegistration.runtime`
becomes `local_systems`, owned declarations gain `local_system_id`, and Execute gains
`resource_ids`. A v0.1-only peer is rejected; there is no dual-service compatibility path.
