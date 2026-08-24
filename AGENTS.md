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
- `docs/images/` stores diagrams.
- `core/` contains maintained Rust responsibility modules; `apps/` contains runnable
  composition roots.
- `core/state/` implements only State & Memory Plane Slice v0.1: deterministic
  Shared Node State plus Allocation State v0.1 behind transport-neutral ports.
- Control reservations remain the sole commitment authority; Allocation State is
  a whole-view observable projection that may lag and never grants or revokes ownership.
- Runtime observation ingestion normalizes `NodeGateway.status()` into reported
  health and separately records RoboGuide-observed liveness; it does not trigger
  reconciliation or automatic recovery.
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
- `mission/` contains the Python Mission Intelligence package and its tests.
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
uv run ruff format --check mission tools/quality
uv run ruff check mission tools/quality
uv run mypy --strict mission/src mission/tests tools/quality
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
