# ADR-0256: Explicit complete-source Node preflight receipts

Status: Accepted and implemented (software checks recorded below).

## Context

ADR-0255's plan-validation v0.3 digest identifies exact authored TOML bytes and Node IDs.
External gRPC descriptors and configuration-relative path resolution were deliberately outside
that receipt. Repeated descriptor reads could also mix different file contents in one compiler run.
The ordinary configuration loader, descriptor compiler/driver and CLI plan reader needed common
source bounds, including refusal of a FIFO with no writer.

## Decision

The default v0.3 receipt retains its exact original TOML-only identity recipe. An explicit
`ROBOGUIDE_NODE_PREFLIGHT_PROFILE=complete-sources/v0.1` requires the existing explicit
`ROBOGUIDE_NODE_PREFLIGHT_CONFIGS` JSON path array. Unknown, malformed or source-less profiles
refuse startup rather than downgrading. The Controller then returns plan-validation v0.4 with
`node_preflight.scope=declared-node-complete-sources`. Mission accepts both generations with
closed, generation-specific source scopes and preserves their exact receipts in draft rejection.
The CLI selects the same profile with `--source-profile complete-sources/v0.1` alongside `--plan`
and emits node-plan-preflight v0.2; its original extension conformance gates remain mandatory.

The complete source digest hashes the JSON serialization of the pair
`("complete-sources/v0.1", identity)`, where `identity` is a Node-ID-sorted map. Each entry contains
`toml` (SHA-256 of exact raw TOML), `resolution_base` (canonical absolute configuration parent),
and `descriptors` (connection-ID-sorted tuples of connection ID, resolved configured descriptor
path, exact acquired byte count and SHA-256). TOML plus this resolution base binds the compiler's
deterministic resolution of journal/cache/Memory paths; this is not an inode, permissions or
future symlink-target attestation. Non-UTF-8 resolution bases refuse. No paths, private identities
or descriptor bytes are returned in HTTP diagnostics.

`local_engine::CompilationConnections` retains decoded descriptor snapshots only during compilation.
Each exact resolved path is acquired once per source batch; all method checks and receipt hashes
consume those same bytes. It never changes `CompiledLocalCatalog`'s representation/Debug output,
original workflow/Memory digest recipes, public Node protocol, persistent journal or Control gates.
A running Controller retains its startup catalog. Changed files require explicit reload/restart;
a query does not silently read a newer source or create an execution worker.

`core/node-service/src/source_io.rs` owns ordinary-file acquisition for authored configs,
compiled descriptors, the descriptor-backed runtime driver and CLI plans. Configs/plans are at
most 1 MiB each, descriptors at most 16 MiB each, and one compiled source batch admits at most
256 MiB under one original 30-second budget. Actual bytes, file type and original deadline are
checked, and detected length/mtime changes refuse. Unix opens use nonblocking flags before
`fstat`, so a FIFO without a writer cannot block awaiting its peer. Config and descriptor bytes
share one budget through compilation; no source or step renews it. Descriptor driver blocking
file work stays off the async worker.

The deadline is a **result admission budget**, not hard kernel-I/O cancellation: regular files
on a stalled filesystem may keep a synchronous kernel operation blocked. Late results refuse;
there is no claim of a hard 30-second process-return bound, coherent filesystem-wide snapshot,
or exclusion of adversarial metadata-preserving writes. Hashes describe exactly acquired bytes.

## Authority and remaining evidence boundaries

This is declared-source compatibility, not attestation of any currently connected Node. Runtime
Node registration, actual selected resources, current permissions, opaque backend responses and
actual execution still use their original gates. The normal runtime descriptor driver retains
bounded current-file reads; a preflight receipt does not pin its later runtime filesystem or
prove deployment equality. A future live-Node attestation needs its own reviewed protocol and
explicit old/new refusal; v0.4 must never be advertised as that proof.

No hardware, models, simulator, cross-host or physical success is established. Available local
software processes can validate source/HTTP behavior; unavailable device or deployment endpoints
remain unproven rather than being replaced by fabricated observations.

## Validation

`tools/quality/check_node_preflight_processes.py` owns the actual built CLI and pinned-mTLS
Controller gate. It records binary/source hashes, original process IDs, new/legacy receipts,
startup snapshot retention, changed descriptor recognition after restart, exact event/command
row preservation, and actual oversized-plan/descriptor, unavailable-descriptor, unknown-profile
and writerless-FIFO refusals. It creates zero execution Nodes/workers/devices/models/simulators.
Run with a fresh short output directory after building both binaries:

```bash
cargo build -p roboguide-node -p integration-server --offline
uv run python tools/quality/check_node_preflight_processes.py --output /tmp/rg-preflight-unique
```

Unit/contract regressions cover complete digest changes for descriptor bytes and resolution bases,
legacy digest compatibility, original aggregate/deadline gates and strict Mission source generations.

### Actual local software acceptance (2026-11-17)

- Affected Rust packages: integration-server 307, node-service 178, CLI 2 tests passed (487 total).
- Affected Mission assessment/rejection/workbench feedback checks: 138 passed.
- Workspace compile, affected all-target/all-feature Clippy with warnings denied, Rustfmt,
  targeted Ruff/format and strict mypy passed. Full repository test suites were not run.
- The maintained actual process gate passed with three independent Controller starts, v0.4/v0.3
  responses, two startup profile refusals and five actual CLI refusals. Original checkpoint,
  event and command rows stayed exact. The retained local receipt is
  `/tmp/rg-preflight-20261117-0256-c/result.json` (private temporary test PKI beside logs).
- The local process gate registers zero Nodes and creates no execution workers. No local serial
  or video device was discovered; no explicit device or cross-host deployment endpoint was
  available for this task. Those facts remain unproven, not passing software proxies.
