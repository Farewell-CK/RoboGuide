# Typed-map handoff provenance acceptance

状态：实现完成，组合门禁继续进行（2026-10-25）。

ADR-0199 adds a `typed_map` field to the durable handoff request. A typed request uses
`roboguide.memory-handoff/v0.2`; the complete `MapArtifactManifest` is checked against the
catalog source and converted to the exact generic projection before Control authorizes the child.
Control checks the original typed-map manifest digest, tenant, principal Handoff grant, immutable
owner/provider/bytes, source/target scope, exact source/target membership generations and current
target Group policy. Retry and restore re-run the same typed policy check; generic v0.1 requests
keep their existing path and cannot carry typed data.

The implementation is in `core/control/src/memory_authority.rs`,
`core/control/src/memory_handoff.rs` and `apps/integration-server/src/application/memory_handoff.rs`.
Existing integration-server tests remain green (277 tests), and the Control memory-authority tests
remain green (8 selected tests).
This closes the handoff ingress provenance gap; it does not claim active-map selection, localization,
physical continuity or the complete cross-stage B matrix.
