# Repository Guidelines

## Project Structure & Module Organization

RoboGuide has a current V2 architecture baseline plus proposed development and MVP
documents. Runtime has not started.

- `docs/architecture/v2/` contains the current V2 source of truth and summary.
- `README.md` and `docs/project-goals-and-mvp.md` define project scope.
- `docs/implementation-backlog.md` tracks deferred decisions.
- `docs/mvp-definition.md` records the Draft MVP and its freeze gate.
- `docs/development/` proposes module layout and defines engineering rules.
- `docs/decisions/` stores numbered Architecture Decision Records (ADRs).
- `docs/images/` stores diagrams.

Keep `AGENTS.md` at the root. Do not create implementation paths until the baseline
is accepted. Then create each approved path with its first maintained implementation.
Never add empty placeholders or a top-level `src/`.

## Build, Test, and Development Commands

For documentation changes, run:

```bash
git status --short --branch
git diff --check
file docs/images/roboguide-v2-overall-architecture.png
```

The first scaffold starts only after baseline acceptance and MVP-slice freeze. It
must pin toolchains, activate commands in `docs/development/coding-standards.md`, and
update this file and `README.md`.

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
produces an inspectable event trace.

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
requires an ADR; architecture-semantic changes update V2 first.
