# Repository Guidelines

## Project Structure & Module Organization

RoboGuide is an architecture-only repository for a general Distributed Embodied AI OS.

- `docs/architecture/v2/RoboGuide_Architecture_Baseline_V2.docx` is the current source of truth.
- `README.md` and `docs/architecture/v2/README.md` provide V2 summaries.
- V1.1 documents and diagrams are retained as historical artifacts.
- `docs/project-goals-and-mvp.md` defines the general OS objective and MVP scope.
- `docs/implementation-backlog.md` records intentionally deferred decisions.
- `docs/images/` stores versioned documentation assets.

Keep `AGENTS.md` at the root and imported sources under `docs/architecture/<version>/`. Add `src/` and `tests/` only with implementation work.

## Build, Test, and Development Commands

No build system or test runner is configured. Before submitting documentation changes, run:

```bash
git status --short --branch
git diff --check
file docs/images/roboguide-v2-overall-architecture.png
```

When code is introduced, document its canonical build and test commands here and in `README.md`.

## Coding Style & Naming Conventions

Use clear Markdown headings, short paragraphs, and relative links. Preserve UTF-8 Chinese design content. Name documentation assets with lowercase, descriptive, hyphen-separated words and include versions where relevant, for example `architecture-v1.1.png`. No formatter or linter is configured; keep diffs focused and make `git diff --check` pass.

## Testing Guidelines

There are no automated tests or coverage requirements yet. Check links, headings, renamed assets, and cross-document consistency. Add future tests with the implementation they validate.

## Commit & Pull Request Guidelines

Use concise Conventional Commit-style subjects such as `docs: clarify MVP scope` or `chore: organize assets`. Pull requests should explain the affected architecture or behavior, identify deferred decisions, link relevant documents or issues, and include updated images when diagrams change.

AI tools may commit, push, and manage pull requests when authorized. GitHub must show the responsible human's verified identity as commit author/committer and PR author, never `Codex`, `Claude`, `AI`, or an anonymous bot. The human owner reviews the complete diff and checks; optional AI disclosure does not replace human attribution.

## Architecture and Scope

The objective is a general Distributed Embodied AI OS. The MVP uses a heterogeneous multi-node task; blind guidance comes later. Preserve V2 semantics: Scheduler emits a Proposal, resource coordination performs Commit, the Group Manager stays in Control while the Group runs across Runtime and Nodes, and State & Memory remains horizontal. Global coordination owns `What / Who / When / Shared Where`; Local Systems retain `Immediate How` and final safety. Do not freeze schemas, transports, databases, algorithms, or hardware APIs without an explicit decision and evidence.
