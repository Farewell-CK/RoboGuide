# Module map: Eval Harness & local integrations

Directories: [`evaluation/`](https://github.com/Farewell-CK/RoboGuide/tree/main/evaluation)
(Python, ~13,500 LOC source + ~8,900 LOC tests),
[`integrations/`](https://github.com/Farewell-CK/RoboGuide/tree/main/integrations),
and [`console/`](https://github.com/Farewell-CK/RoboGuide/tree/main/console).
Module map home · Previous: [Mission Intelligence](mission.md)

## evaluation/ — experiment-driven evaluation infrastructure

**Position**: experiment orchestration and reproducible-result infrastructure
independent of Core/Runtime/Control/State/Local-EAIOS. Hard boundaries: external
systems (Habitat-MAS/EMOS) are reached only across process boundaries and never
imported; the RoboGuide system runner accepts only persisted evidence produced
by the real Controller/Node/Runtime/Local-EAIOS production path and never
bypasses RoboGuide to call a simulator skill.

## Position in the architecture

```mermaid
flowchart TB
    CLI["roboguide-eval CLI<br/>doctor / run / summarize / proxy"]
    SPEC["ExperimentSpec (versioned)"]
    subgraph HARNESS["evaluation/ · ProcessSystemRunner"]
        PROC["process.py<br/>subprocess lifecycle/cleanup"]
        ACCT["accounting.py<br/>LLM accounting proxy :8901"]
        RES["results.py<br/>RunManifest v0.1"]
    end
    EMOS["systems/emos.py"] -->|"official entry point"| EXT["EMOS / Habitat-MAS<br/>(independent Conda env)"]
    RG["systems/roboguide.py"] -->|"production-path driving"| SYS["RoboGuide full stack<br/>Controller/Node/MI/Local EAIOS"]
    SYS -->|"verdict.json + evidence files"| RG
    RG -->|"reduces persisted evidence only;<br/>never calls simulators directly"| METRICS["metrics.json / trace.jsonl<br/>manifest.json"]
    B1["b1_* family<br/>frozen workload → preflight → wait<br/>→ evidence collection → identity checks → admission"] -.-> RG
    CLI --> SPEC --> HARNESS
```

## Formal B1 sequence

```mermaid
sequenceDiagram
    autonumber
    participant H as Harness (b1_*)
    participant MI as Mission Service
    participant RG as RoboGuide production path
    participant EV as evidence files / events

    H->>H: b1_workload freezes input (episode/seed/dataset)
    H->>H: b1_planning_source / b1_deployment_feasibility preflight
    H->>MI: submit (B1 mode)
    MI->>RG: production-chain execution
    loop b1_runner_wait state-driven waiting
        H->>MI: poll request lifecycle
    end
    H->>RG: scenario EXIT trap triggers b1_artifacts
    RG-->>H: mission/events/attempts/action_trace
    H->>EV: b1_event_archive paginated /v1/events archive
    H->>H: b1_provenance independent identity verification (no Habitat dependency)
    H->>H: b1_admission overall verdict (benchmark absence ≠ invalidity)
```

## Module map

| Group | Responsibility |
| --- | --- |
| Core | `models.py` (versioned ExperimentSpec), `config.py` (local.yaml machine overrides), `process.py` (subprocess lifecycle/cleanup), `runner.py` (`ProcessSystemRunner`), `results.py` (`RunManifest` v0.1: manifest/metrics/trace/stdout), `metrics.py` (canonical metric contract + unavailability mechanism) |
| Fairness | `e1_fairness.py` (dataset/task/benchmark-authority/embodiment/simulator/model-config identities + canonical digests), `benchmark_evidence.py` (tri-state benchmark-evidence authority and run-validity classification), `accounting.py` (local LLM accounting proxy) |
| Formal B1 | `b1_workload` → `b1_planning_source` / `b1_deployment_feasibility` → `b1_runner_wait` → `b1_artifacts` / `b1_event_archive` → `b1_provenance` → `b1_admission` / `b1_run` → `b1_live_view` |
| System adapters | `systems/emos.py` (official EMOS entry), `systems/roboguide.py` (`RoboGuideRunner`: reduces only persisted verdict evidence from the run directory) |
| MI probes | `mission_front/` (real-model chain cases/invariants/recording) |

**CLI** (`uv run roboguide-eval`): `doctor` · `run --system {emos,roboguide}` ·
`summarize` · `proxy` · `mission-front`. Machine-specific configuration stays in
the Git-ignored `evaluation/local.yaml` or `ROBOGUIDE_EVAL_*` environment
variables.

## integrations/ — deployment-side Local EAIOS adapters

**habitat-local-eaios** (~7,500 LOC, 147 tests): the C1-S0 reference bridge.
Key mechanisms:

```mermaid
flowchart LR
    subgraph BRIDGE["habitat_local_eaios"]
        SW["shared_world.py<br/>one world serves two agents;<br/>reset freezes the feasibility matrix"]
        S2["emos_stage2.py<br/>injects Control's assignment in place of Stage 1"]
        GUARD["stage2_contract.py<br/>navigation tools bound to the committed target"]
    end
    CTRL["Control's committed assignment<br/>(including the exact target)"] --> S2
    SW --> S2 --> GUARD
    GUARD -->|"out-of-bound target →<br/>Stage2ContractViolation"| EMOS["original EMOS policy stack<br/>(no action rewriting)"]
    RESET["the single Habitat reset<br/>(before endpoint readiness)"] --> SW
    SW -->|"negative feasibility matrix<br/>digest-bound"| ADM["_admit_spatial_feasibility<br/>local admission check"]
```

- Local skill completion, benchmark PDDL success, episode termination, and the
  RoboGuide Mission outcome are four distinct facts that never stand in for
  each other.

**robonix-map-service** (single file, ~940 LOC, stdlib-only): the reference
adapter for "process health vs exact capability readiness" — `/v1/health` only
proves WebUI reachability; `/v1/readiness` runs the startup-fixed read-only ROS
service-discovery command and reports exact per-contract readiness (ADR-0019
semantics).

## console/ — read-only mission-journey visualizer

A zero-dependency static frontend (layered SVG stage + event-packet animation)
plus `serve.py` (stdlib static server with same-origin `/proxy/controller|mission`
reverse proxies). It only consumes existing HTTP APIs; the built-in demo events
mirror `core/domain::EventPayload` serde shapes but are **never** real evidence.

## Implementation status

- evaluation self-describes as a "first-version skeleton": process management/
  canonical metrics/manifest/CLI implemented; the official EMOS command mapping
  is carried by the local.yaml template; subgoal metric integration deferred.
- The B1 event archive confirms only `terminal_durable_prefix`, never a global
  snapshot.
- ADR-0045/0046/0047 are currently "Proposed for review" (not accepted).
- console and the robonix adapter are marked experimental.

## Related ADRs

[0006 Heterogeneous EAIOS integration contract](../docs/decisions/0006-heterogeneous-eaios-integration-contract.md) ·
[0016 Distributed spatial memory](../docs/decisions/0016-distributed-spatial-memory.md) ·
[0019 Capability readiness & localization evidence](../docs/decisions/0019-capability-readiness-and-localization-evidence.md) ·
[0021 Device extension conformance](../docs/decisions/0021-device-extension-boundary-conformance.md) ·
[0045 Shared-world execution session](../docs/decisions/0045-shared-world-execution-session.md) ·
[0046 Reset-state spatial admission](../docs/decisions/0046-reset-state-spatial-admission.md) ·
[0047 Reset-state deployment candidates](../docs/decisions/0047-reset-state-deployment-candidates.md)

---

Module maps: [Domain & ports](domain-ports.md) · [Control](control.md) ·
[Orchestration](orchestration.md) · [Runtime](runtime.md) ·
[State & Memory](state.md) · [Integration & Node](integration-node-service.md) ·
[Mission Intelligence](mission.md) · [Eval & integrations](evaluation.md)
