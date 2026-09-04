# Repository Guidelines

## Project Structure & Module Organization

RoboGuide has a current V2 architecture baseline plus development and MVP documents.
The first core bootstrap has started; the full runtime and MVP are not complete.

- `docs/architecture/v2/` contains the current V2 source of truth and summary.
- `README.md` and `docs/project-goals-and-mvp.md` define project scope.
- `docs/implementation-backlog.md` tracks deferred decisions.
- `docs/mvp-definition.md` records the Draft MVP and its freeze gate.
- `docs/development/` proposes module layout and defines engineering rules.
- `docs/decisions/` stores numbered Architecture Decision Records (ADRs).
- `docs/extensions/` contains executable developer paths and offline extension-conformance evidence.
- `docs/images/` stores diagrams.
- `core/` contains maintained Rust responsibility modules; `apps/` contains runnable
  composition roots.
- `core/state/` implements deterministic Shared Node State, Allocation State v0.1,
  source-aware State records, and generic/Spatial Memory catalog projections behind
  transport-neutral ports. Catalog metadata is rebuildable evidence; Memory blobs never live in
  State, Runtime checkpoints, or the Node Protocol.
- State is not a Global Truth store. Records distinguish Node/World/RoboGuide objects and
  Desired/Committed/Reported/Observed/Derived/Belief semantics, retain source/channel/time/TTL,
  and never collapse independent sources implicitly. The Controller State API is a read-only
  federation over existing authorities.
- Memory is separate from realtime State. Execution, Spatial, Semantic, Experience, and Artifact
  manifests retain local ownership and use shared discovery plus selective CAS-backed exchange;
  discoverable metadata may remain content-local and no full replication is implied.
- Memory Scope, Visibility, and Placement are independent: Scope limits semantic consumers,
  Visibility governs catalog discovery/content exchange, and Placement is provider-qualified replica
  evidence. `Local + Discoverable` is valid; visibility never broadens scope, and an Artifact
  reference never proves node-local placement.
- Configured Memory workflows are the Local Memory Provider integration boundary; a real EAIOS
  retains semantic and backend-storage authority. `FilesystemMemoryLedger` is only the RoboGuide
  Node immutable-manifest ledger, rebuildable JSONL index, and workflow-free reference fallback;
  configured EAIOS imports do not copy payload bytes into that ledger.
- Generic replica durable identity is exact `(MemorySelector, NodeId, ConsumerProviderId)`; admission,
  event replay, projections, and APIs preserve that provider dimension, and accepted evidence remains
  monotonic after Imported. Node Protocol v0.3 has no durable selective-import command; discovery
  never authorizes automatic replication.
- ExecutionGroup-scoped Memory currently has a domain model and Node-local invocation validation
  only. Do not claim or implement complete distributed Group authorization/handoff until its
  Control, Runtime, Commit, Recovery, and Node Protocol authority is designed explicitly.
- `core/orchestration/` owns complete MissionPlan acceptance, Mission-level Group
  creation, DAG-driven TaskExecution readiness, and explicit Mission completion.
- Control reservations remain the sole commitment authority; Allocation State is
  a whole-view observable projection that may lag and never grants or revokes ownership.
- The current Mission boundary uses `roboguide.mission-plan/v0.4`: Context/ContextRole
  continuity, coupling mode, selective Group view, peer descriptor, and Execution Relation
  specifications are Mission Intelligence metadata, while Task/Context resource ownership is
  recorded independently in Control and its Group projection. v0.2/v0.3 remain compatibility
  inputs.
- Execution Relation endpoints are exact logical `(TaskId, RoleId)` slots inside one Context,
  never NodeId or adapter handles. Runtime resolves them to current attempts, persists live
  relation state/fences, and emits evidence; Control retains commitment and recovery decisions.
- Mission Actor is logical metadata, not a physical-node selector. Deployment-owned
  actor placement constraints may narrow first-use Control Matching, but create no
  reservation or ActorBinding before Proposal -> Commit -> Group Bind succeeds.
- Runtime observation ingestion normalizes `NodeGateway.status()` into reported
  health and separately records RoboGuide-observed liveness; it does not trigger
  reconciliation or automatic recovery.
- `core/runtime` owns the transport-neutral live execution registry, stable execution
  identity, ordered fact reduction, recovery-required fencing, and its checkpoint.
  `core/orchestration::IntegrationRuntimeBridge` is the Controller composition facade
  and must not keep a second authoritative execution map or directly mutate Task lifecycle.
- Runtime `Unknown` means recovery-pending physical ambiguity, never terminal Task or
  Mission failure. Integration persists the evidence; application orchestration applies
  Runtime lifecycle transitions, while Control retains recovery decisions.
- Observation time semantics preserve source-local `NodeStatus.observed_at` while
  State ordering and Control freshness use RoboGuide-local `received_at`; do not
  compare independent source clocks or claim distributed clock synchronization.
- Assigned-node reconciliation lives inside `core/control`, reuses the shared
  eligibility predicate and existing Group lifecycle APIs, and consumes an
  externally supplied recovery proposal; it never selects a replacement node.
- Recovery reassignment follows role-scoped Match -> Propose -> Commit -> Rebind:
  candidate matching may be empty, proposal creates no reservation, commit uses
  the single Control reservation authority, and rebind requires commitment.
- Committed-but-not-bound recovery assignments are Control-owned pending
  commitments keyed by Group/Role; Rebind consumes, Abort releases replacement
  resources, and terminal Group release removes all related ownership.
- `core/control/src/scheduler.rs` implements a stateless deterministic bootstrap
  `Who should` policy over supplied Candidate Sets; it does not re-run eligibility,
  inspect reservations, validate proposals, commit resources, or mutate State/Groups.
- `core/artifact-store/` contains the filesystem implementation of the transport-neutral
  ArtifactBlobStore port; it stores opaque bytes and does not own map, task, or execution policy.
- `core/integration/` owns the formal gRPC Node Protocol v0.3 transport;
  `core/node-service/` owns node-side lifecycle, configuration, durable execution
  continuity, and the declarative Local Integration Engine.
- `core/integration/` contains only formal Node Protocol wire/session/router code;
  Controller composition and the Runtime bridge belong to `core/orchestration/`.
- Node Protocol `Registered` and sequence `Ack` mean Controller application authority plus durable
  checkpoint acceptance, not transport receipt. Integration waits on an application completion
  envelope but never makes the Control/State/Runtime decision itself.
- Node config v0.6 requires one fixed readiness observation per exact canonical
  capability. Node Service observes before Register and emits complete RegistrationUpdate
  snapshots on change; Integration preserves exact readiness, State stores it, and Control
  consumes it only for later eligibility decisions. It also declares fixed State exports and
  Memory providers and optional provider-local discover/export/import workflows; v0.2-v0.4
  configs normalize those declarations to empty and v0.5 providers remain metadata-only.
- Node Protocol v0.3 carries complete State/Memory provider snapshots and bounded periodic State
  observation batches. Sampling failure only causes staleness and never changes health,
  readiness, execution lifecycle, or recovery. The v0.2 endpoint is rejection-only.
- `integrations/` contains deployment-owned Local EAIOS adapters (for example the
  Robonix map adapter); these adapters own vendor calls and local file layout only,
  and must not become a second Control, Runtime, State, or Node Protocol authority.
- The Robonix map adapter exposes process health separately from exact capability
  readiness. Its startup-fixed ROS service discovery command is read-only and
  deployment-owned; execution requests must never supply commands or service names.
- `apps/integration-server/` is the formal gRPC server composition root and
  `apps/roboguide-node/` is the configured node-side daemon composition root.
- `apps/mobile-navigation/` is an experimental Android Local System for phone-side
  perception, positioning, semantic mapping, route planning, and local navigation.
  It retains Immediate How and final safety, is not a Control/Runtime/State authority,
  and currently has no Node Protocol integration. Large models, generated VINS
  dependencies, local toolchains, and RealSense native binaries remain outside Git.
- Each node machine runs only `roboguide-node`; new Local EAIOS integrations use
  startup-validated HTTP, dynamic gRPC, or MCP workflow configuration and never
  add an EAIOS-specific code branch or RoboGuide-side service.
- `apps/real-node-smoke/` probes the formal Node Protocol v0.3 handshake by default; its explicit
  `--simulate-execute` mode submits a synthetic Mission through the Controller HTTP API, emits only
  synthetic lifecycle facts after formal dispatch, uses a session-unique capability contract so it
  cannot select an existing Node, and never performs hardware I/O.
- Execution commands carry canonical `ExecutionIntent`; Matching and Scheduler do
  not interpret it, Runtime only routes it, and the configured Node Service workflow maps it to Local How.
- Spatial map bytes use the independent Artifact data plane. `MapId`/`MapRevisionId` references
  may be carried as opaque intent parameters, while digest verification and local staging belong
  to Node Service and the independent Artifact data plane. Node Protocol never carries map bytes.
- Strong localization verification uses
  `roboguide.localization-verification-evidence/v0.1`. Node journal persistence precedes remote
  delivery, Artifact HTTP records the evidence transition, and State distinguishes strong
  evidence from legacy `has_map=true` smoke verification. Real facade field mapping remains a
  hardware-validated deployment responsibility.
- `mission/` contains the Python Mission Intelligence package and its tests.
- `apps/mission-service/` is the Python Mission Request composition root. It owns text instruction
  ingress and durable deliberation state, then submits accepted complete plans to the existing
  Controller API; it must not mirror execution lifecycle or choose physical nodes.
- `contracts/mission/` stores versioned cross-language contracts; `config/` stores
  non-secret runtime configuration; `scenarios/` stores deterministic artifacts.

Keep `AGENTS.md` at the root. The bootstrap may create only the maintained paths
listed above. Create future paths only with their first implementation; never add
empty placeholders or a top-level `src/`.

## Build, Test, and Development Commands

For documentation changes, run:

```bash
git status --short --branch
git diff --check
file docs/images/roboguide-v2-overall-architecture.png
```

The bootstrap pins Rust and Python toolchains. Python commands use the repository's
uv-managed environment. New implementation paths must update this file and
`README.md` when their ownership is established.

For Mission changes, run:

```bash
uv run ruff format --check mission apps/mission-service tools/quality integrations/robonix-map-service
uv run ruff check mission apps/mission-service tools/quality integrations/robonix-map-service
uv run mypy --strict mission/src mission/tests apps/mission-service tools/quality \
  integrations/robonix-map-service/robonix-map-service.py \
  integrations/robonix-map-service/tests
uv run python tools/quality/check_python_function_docs.py \
  mission apps/mission-service integrations/robonix-map-service
uv run pytest -q
```

## Coding Style & Naming Conventions

Use Markdown, relative links, UTF-8 Chinese, and lowercase-hyphenated asset names.

Every handwritten Rust `fn` and Python `def` or `async def`, including private and
test helpers, requires a useful documentation comment or docstring. Document
responsibility, effects, invariants, and failure behavior. Rust uses `rustfmt` and
Clippy with warnings denied. Python uses Ruff, strict typing, and Google-style
docstrings.

## Testing Guidelines

Core tests are deterministic and offline, use fake nodes and a virtual clock, and
cover normal, rejection, timeout, conflict, and recovery paths. Mark Isaac Sim,
network, model, and hardware checks as adapter/system tests. Cross-module behavior
produces an inspectable event trace. State tests keep observed facts separate from
Control eligibility policy and cover registration, freshness, health updates, and
cross-mission reads. Recovery tests must preserve partial versus whole-group release.
Runtime-State tests distinguish local reported health from system-observed liveness
and prove that new observations affect later Control decisions. Time tests must
cover source-clock divergence, receive ordering, heartbeat receive time, and lease
liveness without weakening Recovery lifecycle tests.
Reconciliation tests separate read-only assessment from mutation, preserve
unaffected bindings and Multi-Mission isolation, and keep missing replacements
Blocked/Pending rather than Failed.
Recovery pipeline tests prove candidate membership, proposal/commit separation,
atomic conflicts, committed-only rebind, and stable unaffected bindings.
Commitment lifecycle tests cover one-pending-per-role, stale handles, atomic Abort,
terminal cleanup, zero-resource roles, and Multi-Mission ownership isolation.
Scheduler tests require stable node/resource choices, CandidateSet confinement,
normal/recovery policy consistency, decision-local exclusive-resource avoidance,
NoSelection/NoFeasible outcomes, and Decision/Proposal/Commit separation.
Allocation projection tests cover Committed/Bound/RecoveryPending, partial release,
Abort/Rebind/Release, orphan rejection, stable ordering, projection lag, and
Control-to-State one-way authority. Scheduler v0.1 must not read Allocation State.
Adapter tests cover contract-version rejection, wire/domain conversion, identity
matching, transport errors, intent round trips, and heterogeneous local mappings.

## Commit & Pull Request Guidelines

Use concise Conventional Commit subjects, for example `docs: define development
baseline`. Pull requests identify the V2 responsibility and module, explain failure
behavior, link ADRs/issues, and list checks.

Authorized AI tools may manage Git operations. GitHub must show the responsible
human as commit author/committer and PR author, never a tool or anonymous bot. The
human owner reviews the full diff and checks.

## Architecture and Scope

Preserve V2 semantics: Proposal differs from Commit; the Group Manager stays in
Control while the Group runs across Runtime and Nodes; State & Memory is horizontal;
Local Systems retain Immediate How and final safety. Do not freeze schemas,
transports, databases, algorithms, or hardware APIs without evidence and a decision.
Changing ownership, dependency direction, authority, lifecycle, or public contracts
requires an ADR; architecture-semantic changes update V2 first. Do not describe
Shared Node State Slice v0.1 as complete State, Belief, Allocation, or Memory.
