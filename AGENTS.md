# Repository Guidelines

## Project Structure & Module Organization

RoboGuide is currently an architecture-only repository for the Distributed Embodied AI OS baseline.

- `README.md` is the contributor-facing architecture overview and embeds the V1.1 diagram.
- `Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx` is the source design document.
- `docs/architecture-baseline-v1.1.md` contains the structured architecture summary.
- `docs/implementation-backlog.md` records decisions intentionally deferred until implementation evidence exists.
- `docs/images/` stores diagrams and their naming guidance.

There is intentionally no `src/`, test suite, or runtime implementation yet. When implementation begins, preserve the four logical boundaries: Mission / Intelligence, Embodied Control, State & Memory, and Distributed Runtime.

## Build, Test, and Development Commands

No build system or test runner is configured at this stage. Use these checks before submitting documentation changes:

```bash
git status --short --branch
git diff --check
file docs/images/distributed-embodied-ai-os-architecture-v1.1.png
```

When code is introduced, add the canonical build and test commands to this file and `README.md` at the same time. Do not introduce a dependency or framework only to support a documentation change.

## Coding Style & Naming Conventions

Write Markdown with clear `##` sections, short paragraphs, and relative links. Preserve UTF-8 Chinese content in the design materials. Use lowercase, descriptive, hyphen-separated names for new documentation assets, with versions included where relevant, for example `architecture-v1.1.png`. No formatter or linter is currently configured; keep diffs focused and ensure `git diff --check` passes.

## Testing Guidelines

There are no automated tests or coverage requirements yet. Documentation changes should be checked for valid paths, readable headings, and synchronized references to renamed assets. Future tests should live under `tests/` and be added together with the implementation they validate.

## Commit & Pull Request Guidelines

Existing commits use concise Conventional Commit-style prefixes such as `docs:` and `chore:`. Use an imperative, scoped subject, for example `docs: clarify execution group boundary`. Pull requests should explain the architecture or behavior affected, identify deferred decisions, link relevant design files, and include updated screenshots or image references when diagrams change. Keep unrelated refactors out of documentation-only changes.

AI tools may commit code, push changes, and create or update pull requests when authorized by a human contributor. Responsibility must remain attributable to the actual human owner: GitHub must show that person's verified account as the commit author/committer and PR author, never a tool identity such as `Codex`, `Claude`, `AI`, or an anonymous bot. Configure Git identity and PR credentials accordingly, and have the responsible human review the complete diff and relevant checks. A PR may disclose AI assistance in its description when useful, but that disclosure must not replace the human attribution.

## Architecture and Scope

Do not freeze schemas, transport protocols, databases, scheduling algorithms, or hardware-control APIs without an explicit design decision and supporting evidence. Keep Global Autonomy responsible for `What / Who / Where / When`; Local Runtime retains `Immediate How` and final safety authority. Treat `Execution Group` as the bridge between scheduling and coordinated execution, and route failures back through State / Memory and Reconciliation.
