# Current implementation and evidence boundaries

Code reviewed: 2026-11-26 · `main@3c7136d6` (PR #42). This page describes repository software. Historical acceptance, unit tests and physical observations support different conclusions.

## Capability map

| Capability | Responsibility and entry | Independent evidence still needed |
| --- | --- | --- |
| Conversation to plan | [Request engine](../../mission/src/mission/request_engine.py), [Chat tools](../../mission/src/roboguide_workbench/task_tools.py): grounding, clarification, review, approval and ambiguous-submission reconciliation | Actual models, tool sources and operator scenarios |
| Admission and joint scheduling | [Orchestration](../../core/orchestration/src/lib.rs) owns DAG/satisfaction; [Control](../../core/control/src/lib.rs) owns admission, Commit, Bind, resources and Groups | Deployment-specific candidates, topology, leases and quotas |
| Reliable execution | [Runtime](../../core/runtime/src/lib.rs), [Node Service](../../core/node-service/src/service/mod.rs): attempts, durable intents, journals, idempotence and ACKs | Physical workflow effects, stops and timing |
| Completion and stopping | [Satisfaction](../../core/orchestration/src/mission/lifecycle.rs), [safety stop](../../apps/integration-server/src/application/safety_stop.rs): Completed, TaskSatisfied, stop confirmation and release remain distinct | Independent goal evidence and current SafeState sources |
| State / Memory | [Source-bound State](../../core/state/src/state_record.rs), [Memory preparation](../../apps/integration-server/src/application/memory_preparation.rs), [spatial provider](../../integrations/spatial-provider/README.md): freshness, scope, visibility, placement and use permission | Calibration, map registration, localization accuracy and privacy grants |
| Recovery and revision | [Recovery](../../apps/integration-server/src/application/recovery.rs), [plan switching](../../apps/integration-server/src/application/plan_revision/switching.rs), [recovery tools](../../apps/recovery-tool/README.md) | Original stop/revoke and policy gates; Unknown never authorizes blind replay |
| Four cooperation modes | [Relation Runtime](../../core/runtime/src/relation/manager.rs), [Sync Provider](../../integrations/sync-provider/README.md): independent, barrier, shared Context and edge-coupled execution | Local feedback, disconnected stops and actual multi-device accuracy |
| Deployment and replicas | [Controller](../../apps/integration-server/src/main.rs), [Replica](../../apps/controller-replica/src/main.rs), [deployment contract](../operations/reference/replicated-federation-deployment-v0.1.md) | TLS/member identity, process ownership, quorum, content availability and long-running SLOs |

## Recent main changes

- **PR #42** adds EAIOS-neutral `mobility.sync-follow@v1` and `choreography.play@v1`, with sim, HTTP chassis and Robonix Lite3 adapters. Tests cover simulated and Controller/Node loopback paths; they do not establish current physical accuracy or stop acceptance.
- **PR #40** adds shared-view and relation-reconcile routes and controlled manual-cancel settlement for terminal attempts. Original relations, current identities and stopping evidence remain gates.
- **PR #32/#33** add independent SafeState and runtime takeover. Natural execution failure does not itself prove effects have stopped.
- **PR #30** adds read-only dependency-window preflight, reserving no resources and promising no successful execution.
- **PR #25/#18** add multi-robot occupancy-grid fusion and recipient/session binding; neither establishes physical navigation safety.

See the [eight-topic atlas](../architecture/v2/current-architecture.en.md), [core flows](../architecture/v2/system-dataflows.en.md) and [source-derived versions](contracts-and-build.en.md).

## Reading evidence

| Evidence | Supports | Does not independently support |
| --- | --- | --- |
| This source review | Current responsibilities, paths, contracts and implemented gates | Enabled or effective behavior in an actual deployment |
| Unit and offline tests | Covered logic and file constraints | Real multi-host faults or physical effects |
| Process / loopback acceptance | The original record's software process loop | Hardware stops, localization accuracy or long-running SLOs |
| Physical acceptance | Observed devices, configuration, environment and time | Every device, deployment or later revision |
| Long-running observations | Metrics within explicit windows, loads and fault domains | Untested loads or undeclared safety guarantees |

This refresh runs no robot workflows and adds no physical, production or long-running performance conclusions.

## Original records

[Acceptance directory](../development/acceptance/README.md) · [Hot enrollment](../development/hot-node-enrollment-acceptance-20261109.md) · [Map replica identity](../development/acceptance/2026-11-17-map-replica-identity.md) · [Manual-cancel settlement](../development/acceptance/2026-11-17-failed-manual-cancel-settlement.md)

The [complete previous status page](../archive/pre-refresh-3c7136d6/website/pages/status.en.md) retains its original claims and dates. The [migration inventory](documentation-inventory.json) and [archive entry](../archive/README.en.md) preserve their reading destinations.
