# Getting started

The shortest path to build, test, and run RoboGuide locally. All commands assume
the repository root as the working directory.

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Rust toolchain | pinned by `rust-toolchain.toml` | `cargo` picks it automatically |
| Python | 3.13 | managed by `uv` (`pyproject.toml` pins `>=3.13,<3.14`) |
| [uv](https://docs.astral.sh/uv/) | any recent version | Python environment & dependency management |
| git | — | clone the repository |

??? note "Optional: model access for Mission Intelligence"

    Mission Intelligence uses the OpenAI Responses API by default. The API key is
    read only from the `OPENAI_API_KEY` environment variable and never stored in
    config files; remote plaintext HTTP is rejected by the security boundary —
    use HTTPS or a localhost tunnel for production integration. Without a key,
    the Rust demo paths below are unaffected.

## Build and test

```bash
# Rust core (11 crates + 5 application roots)
cargo build --workspace
cargo test --workspace          # all offline deterministic tests, no external services

# Python (mission / evaluation / tools)
uv sync --dev
uv run pytest -q                # also fully offline
```

Quality gates (identical to CI):

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
uv run ruff format --check mission apps/mission-service tools/quality evaluation
uv run ruff check mission apps/mission-service tools/quality evaluation
uv run mypy --strict mission/src mission/tests apps/mission-service tools/quality
uv run python tools/quality/check_python_function_docs.py mission apps/mission-service
```

## Demo 1: control-plane vertical slice (no network)

`apps/controller` is the minimal executable proof: registers 3 nodes, creates a
Mission Group, runs match → schedule → propose → commit → bind, drives a FakeNode
(including failure injection and recovery rebind), and finishes when the Group
reaches `Completed → Released`.

```bash
cargo run -p controller
```

Expected output includes `DEAIOS control slice produced N events`. Everything
uses `VirtualClock` and in-memory state — no network or external services.

## Demo 2: formal Node Protocol v0.4 handshake

Start the Controller composition root (gRPC `:50051`, Control HTTP `:8080`,
Artifact HTTP `:8090`):

```bash
cargo run -p integration-server
```

In another terminal, run the smoke probe for a real protocol handshake,
registration, and heartbeat:

```bash
cargo run -p real-node-smoke -- --endpoint 127.0.0.1:50051
```

Expected output: `registered node=... session=... lease=... protocol=v0.4 ...`.

The `--simulate-execute` mode additionally submits a synthetic single-role
Mission through the full Controller dispatch loop (session-unique capability
contract, never selects an existing Node, performs no hardware I/O):

```bash
cargo run -p real-node-smoke -- --endpoint 127.0.0.1:50051 \
  --control-endpoint http://127.0.0.1:8080 --simulate-execute
```

## Demo 3: Mission Intelligence service

```bash
export OPENAI_API_KEY=sk-...        # optional, see note above
uv run mission-service              # listens on 127.0.0.1:8070 by default
```

Submit a text instruction and follow the clarification/draft/approval loop:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8070/v1/mission-requests \
  -H 'Content-Type: application/json' \
  -d '{"instruction": "Move the cup from the living room to the kitchen"}'
```

Use the returned request id with `GET /v1/mission-requests/{id}` to poll,
`POST .../{id}/messages` to answer blocking clarifications, and `.../approve`
to approve a draft. Configuration lives in `config/mission.toml` and
`config/mission-service.toml`.

## Eval Harness

The evaluation framework is independent of the core code; run the environment
self-check first:

```bash
uv run roboguide-eval doctor --spec evaluation/specs/e1/<spec>.yaml --config evaluation/local.yaml
```

Machine-specific Conda environments, working directories, and credentials stay
in the Git-ignored `evaluation/local.yaml` (template: `local.yaml.example`) or
`ROBOGUIDE_EVAL_*` environment variables.

## Local preview of this documentation site

```bash
cd website
uv run python sync_docs.py
uvx --from mkdocs --with-requirements requirements.txt mkdocs serve
```

Open <http://localhost:8000>. The build is strict: any broken internal link
fails the build.

## Next steps

- Read the [architecture tour](architecture-tour.md) for layer responsibilities
  and key semantic boundaries
- Go crate by crate through the [module maps](modules/domain-ports.md) (each page
  lists key types, main-flow APIs, and test themes)
- When you need to know *why* a semantic is the way it is, check the
  [ADR index](docs/decisions/index.md)
