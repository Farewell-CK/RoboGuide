# Development and integration

Connect an existing Local EAIOS through Node Service without modifying RoboGuide core. Complete [getting started](getting-started.md), then replace the test configuration with a real backend. Example Nodes are not device inventory.

## Integration steps

1. Identify the LocalSystem, actual endpoints, semantic operations and local safety owner. Read `config/node.toml` for the configuration shape.
2. Declare exact Capability Profiles, typed attributes and readiness observations; separately declare Operation Support and private workflow bindings.
3. Workflows call only deployment-configured HTTP, dynamic gRPC or MCP endpoints. Mapping carries semantic objectives/parameters without promoting vendor APIs to global Mission semantics.
4. Explicit State exports declare owner, object, schema, TTL, sampling period and read-only observation. Sampling failure expires existing evidence instead of inventing a negative value.
5. Memory providers declare scope and discovery/export/import/availability/use/query contracts. Artifact transport moves bytes.
6. Run configuration conformance and isolated software checks for registration, readiness changes, Execute/Cancel, disconnect, duplicates and restart.
7. Physical admission separately requires device-stop, capability-readiness and effect evidence. Process health and config checks are insufficient.

## Contracts and compatibility

[Reference](reference.md) generates current versions. Explicitly choose the config/Node Contract required by each feature. `config/node.toml` remains an older basic example and acquires no Memory or safety-generation permission from newer code.

The [Node catalog](https://github.com/Farewell-CK/RoboGuide/tree/main/contracts/node/v0.17) and [extension specification](docs/extensions/device-extension-conformance-v0.2.md) document fields. Local MCP/HTTP/gRPC drivers do not change Control/Runtime authority.

## Independent verification

Define Task expected effect, predicate, source and evidence schema before configuring verifier policy. Bind reports to complete current attempts, plan digest, tenant/source, freshness and finality; verify actual bytes when CAS proof is required.

An operation's successful return proves only its contracted local semantics. A Task requiring independent evidence retains `verifier-evidence`; integration difficulty cannot justify weakening it to execution-report.

## Development checks

Use `rust-toolchain.toml`, `pyproject.toml` and locked dependencies. Checks include fmt, Clippy, pytest, Ruff, mypy and function docs. New integrations also exercise the actual deployment Python compatibility path.

The [module map](modules/domain-ports.md) locates code. [Original integration archives](archives.md) retain complete commands and historical limits.

## Spatial and Time deployment constraints

Global recovery-admin installs [resource-topology v0.1](docs/development/resource-topology-v0.1.md) over actual declared resources. Space requires exact frame/anchor/bounds/clearance; Time aliases share an explicit time domain and local Controller openings. Deployment booking budgets remain outside canonical model output. Admission, Bind/Rebind and every outbox send check original bounds. Declared geometry never creates device inventory or measured occupancy.

## Coordination and active hazards

[Revision v0.6](docs/development/execution-coordination-v0.1.md) uses actual registered recovery/safety support and canonical successor operations; it supplies no vendor motion. Device handoff/regrasp requires original reviewed Memory/context and DeviceObservation. Explicit decision-policy v0.3 active Readiness gates consume actual source/session/TTL/conflict evidence. Node and MissionPlan contracts retain their versions and meaning.
