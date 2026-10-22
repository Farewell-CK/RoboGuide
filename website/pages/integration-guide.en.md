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

The [Node catalog](https://github.com/Farewell-CK/RoboGuide/tree/main/contracts/node/v0.15) and [extension specification](docs/extensions/device-extension-conformance-v0.2.md) document fields. Local MCP/HTTP/gRPC drivers do not change Control/Runtime authority.

## Independent verification

Define Task expected effect, predicate, source and evidence schema before configuring verifier policy. Bind reports to complete current attempts, plan digest, tenant/source, freshness and finality; verify actual bytes when CAS proof is required.

An operation's successful return proves only its contracted local semantics. A Task requiring independent evidence retains `verifier-evidence`; integration difficulty cannot justify weakening it to execution-report.

## Development checks

Use `rust-toolchain.toml`, `pyproject.toml` and locked dependencies. Checks include fmt, Clippy, pytest, Ruff, mypy and function docs. New integrations also exercise the actual deployment Python compatibility path.

The [module map](modules/domain-ports.md) locates code. [Original integration archives](archives.md) retain complete commands and historical limits.
