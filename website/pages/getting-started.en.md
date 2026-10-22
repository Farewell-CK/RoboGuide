# Getting started

Begin with the control-plane demo requiring no network, model or device. Then verify the formal protocol and optionally connect MI.

## Prerequisites

Run commands from the repository root. `rust-toolchain.toml` pins Rust; `pyproject.toml` pins Python to the 3.13 series. Use uv for Python environments. Repository files define exact supported versions.

```bash
git clone https://github.com/Farewell-CK/RoboGuide.git
cd RoboGuide
cargo build --locked -p controller -p integration-server -p real-node-smoke
```

## Demo 1: control-plane vertical slice (no network)

```bash
cargo run --locked -p controller
```

At this content baseline, output is `DEAIOS control slice produced 21 events`. FakeNodes, VirtualClock and in-memory State exercise registration, commitment, execution and recovery. This demonstrates control flow, not devices.

## Demo 2: formal Node Protocol v0.4 handshake

Choose free loopback ports and a new data directory. These example ports are local only; update both sides if occupied.

```bash
mkdir -p /tmp/roboguide-quickstart
cargo run --locked -p integration-server --   127.0.0.1:39151 /tmp/roboguide-quickstart/controller.sqlite3   127.0.0.1:39152 127.0.0.1:39153 /tmp/roboguide-quickstart/artifacts
```

In another terminal:

```bash
cargo run --locked -p real-node-smoke -- --endpoint http://127.0.0.1:39151   --node-id docs-protocol-smoke
```

Successful output contains `registered node=docs-protocol-smoke`, a session and `protocol=v0.4`. The isolated test Controller registers an explicitly synthetic software Node; no Local EAIOS, model or device runs. Stop the server with Ctrl+C. Keep its data for inspection rather than reusing it in production.

**Common failures:** change occupied ports; for writer-busy databases check your existing process or choose a new directory; for admission failures inspect exact contract/TLS/config instead of deleting SQL history.

## Demo 3: Mission Intelligence service

This optional path needs valid model configuration and your own Controller. Documentation validation runs only the model-free examples and does not claim live-model acceptance.

```bash
uv sync --dev --frozen
uv run mission-service
```

Inspect `config/mission.toml` and `config/mission-service.toml`, supplying credentials through the configured environment variables. In your isolated deployment:

```bash
curl --fail-with-body http://127.0.0.1:8070/v1/mission-requests   -H 'Content-Type: application/json'   -d '{"instruction":"Produce a file and independently verify its contents"}'
```

Results depend on published Catalog, grounding, model and deployment. Clarification/approval may be required; the example is not guaranteed executable. Use the Request ID to observe original progress and answer questions. Uncertain outcomes do not authorize duplicate submission.

## Checks and next steps

Follow repository AGENTS for maintained fmt, Clippy, pytest, Ruff, mypy and function-documentation scopes. Continue with [integration](integration-guide.md) or [Eval Harness](modules/evaluation.md).

## Local preview of this documentation site

```bash
cd website
python sync_docs.py
uvx --from mkdocs --with-requirements requirements.txt mkdocs serve -a 127.0.0.1:8001
```

See [website maintenance](https://github.com/Farewell-CK/RoboGuide/blob/main/website/README.md) for the complete build and verification procedure.
