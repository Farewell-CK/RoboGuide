# Coding Standards

These rules apply to handwritten production code, tests, examples, and internal
helpers. Generated code must be isolated and clearly marked.

## 1. Function Documentation

Every Rust `fn` and every Python `def` or `async def` must have a documentation
comment or docstring, including private functions, methods, test helpers, fixtures,
and constructors.

Documentation must state:

- the function's responsibility and meaningful inputs/outputs;
- state changes, external effects, or concurrency assumptions;
- important invariants and rejected conditions;
- errors, panic conditions, or safety requirements when applicable.

Do not write comments that merely restate the function name. Update documentation in
the same change as behavior. Modules also require a Rust `//!` comment or Python
module docstring explaining their ownership boundary.

## 2. Rust Rules

- Use the repository-pinned stable toolchain and edition.
- Format with `rustfmt`; run Clippy with warnings denied.
- Crate roots use `#![deny(missing_docs)]`,
  `#![deny(clippy::missing_docs_in_private_items)]`, and default to
  `#![forbid(unsafe_code)]`.
- Use typed IDs and value objects instead of passing unrelated `String` values.
- Model lifecycle changes as explicit transitions; invalid transitions return typed
  errors and never silently mutate state.
- Production code must not use `unwrap()` or `expect()` except for a documented,
  process-startup invariant. Tests may use them for setup clarity.
- Libraries expose domain-specific error enums. `anyhow`-style context belongs only
  at application and adapter boundaries.
- Blocking work must not run on an async executor thread. Cancellation, timeout, and
  shutdown behavior must be explicit.
- New dependencies require a written purpose and must not duplicate an existing
  capability.

Required Rust gates once the workspace exists:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps
```

## 3. Python Rules

- All functions, methods, parameters, and return values are type annotated.
- Use Google-style docstrings consistently.
- Format and lint with Ruff; type-check application packages in strict mode.
- Do not use bare `except`, mutable default arguments, wildcard imports, hidden
  module-level runtime state, or unbounded background tasks.
- Model and simulator SDK objects stay inside adapters. Core contract objects must
  remain serializable and independent of those SDKs.
- Network, model, clock, and simulator access must be injectable for tests.
- Dependency and tool versions are declared in `pyproject.toml` and locked by the
  bootstrap change; do not rely on globally installed packages.

Required Python gates once packages exist:

```bash
ruff format --check python tests
ruff check python tests
python tools/quality/check_python_function_docs.py python tests
mypy --strict python
pytest -q
```

Enable Ruff's pydocstyle `D` rules with the Google convention. These rules cover
public definitions but do not guarantee documentation on underscore-prefixed private
functions. The first Python scaffold must therefore add an AST-based check under
`tools/quality/` that rejects every undocumented `def` and `async def`, including
private functions and tests. A suppression needs an inline reason.

## 4. Naming and Data

- Rust crates use `roboguide-*`; Rust modules and Python packages use `snake_case`.
- Types and state names use domain language from V2; avoid generic `Manager`, `Util`,
  `Common`, or `Helper` unless the responsibility is further qualified.
- Boolean names read as predicates, for example `is_committed` or `can_execute`.
- Units appear in names or types: `timeout_ms`, `distance_m`, `TimestampUtc`.
- Configuration is explicit, validated at startup, and contains no credentials.
- Logs are structured and include operation, node, task, group, correlation, and
  error identifiers where available.

## 5. Testing Standard

- Unit tests cover every lifecycle transition, invariant, and error branch.
- Port implementations share contract tests.
- Integration tests use fake nodes and a virtual clock; never wait with arbitrary
  sleeps.
- System tests verify observable event traces, not private implementation details.
- Failure injection covers heartbeat expiry, stale evidence, capability degradation,
  reservation conflict, invocation failure, and recovery escalation.
- Critical state-machine transitions require complete transition coverage. The
  workspace line-coverage floor will be set after the first measurable scaffold and
  must never replace behavior-focused assertions.

A test that depends on Isaac Sim, a real robot, an external model, or the network is
an adapter/system test and must be separately marked. Core tests remain offline and
deterministic.

## 6. Review Standard

A pull request is not ready when it only compiles. It must explain the architecture
boundary, document every function, include failure-path tests, pass all configured
gates, and contain no unrelated refactor. `TODO` and `FIXME` require a tracked issue
or decision identifier.
