# Quantitative Compute acceptance — 2026-10-25

Source: ADR-0206 changes following `871cc78a`, on the single development branch
`codex/nonrelease-remediation-closure`. This closes the explicit quantitative Compute module;
the completion plan retains complete time/space, upgrades, coordination and Federation work.

## Original authority

MissionPlan v0.10 declares typed positive Compute units and explicit ContextRole envelopes.
Control commits complete parent/borrower/quota batches atomically. Exact Task release preserves
other borrowers; Context capacity survives borrower release until explicit Context/Group release.
Recovery Commit/Bind/Rebind and registration retain original ownership and current capacity checks.
Weighted half-open future intervals count one Context parent and its separate borrower limits.
Unknown and overrunning live attempts do not acquire a timer-based release.

Node config v0.14 / Contract v0.13 and wire v0.16 carry closed grants outside model parameters.
Node validates original hashes, complete scope and local declared capacity before durable dispatch,
serializing admission with its original v14 journal. Local How operation locks remain authoritative.
Controller wrapper v56 and recovery v0.24 fence unsupported interpretation by older writers.

## Actual process and recovery evidence

```bash
uv run python tools/quality/check_state_verifier_processes.py \
  --output /tmp/rg-quant-native-6 --skip-build --quantitative-resources \
  --previous-controller /tmp/rg-cost-old-controller-dbe7
```

`/tmp/rg-quant-native-6/result.json` passes with one actual software Node, three actual CPU children,
three independent file verifier processes and zero duplicate execution. The configured provider
supports two independent process slots over immutable inputs and separate outputs. Its explicit
new profile omits the old serial file-hasher lock; the generic engine and old profiles retain locks.
Two children wait at actual kernel pipe read barriers while the third Task remains undispatched.
Original child PIDs are `2084005` and `2084008`. After actual Controller/Node wait/restart, the same
children, original grants and quota bytes remain. Local completion alone retains capacity until
independent CAS-backed satisfaction; final SQL has generation 3, zero allocations and zero claims.

Frozen actual binary SHA-256:

| Binary | SHA-256 |
| --- | --- |
| Controller | `a5c85325f0a5437a5e3555ba4a5bbf790f87a59efa5e6afa9eeb2403cf1f6fc0` |
| Node | `ba4ea41e078f1d71f218dc6f110eed5e316a31adcff48b6934d04d334a0a0c1e` |

While both RoboGuide writers are stopped, the actual shipped recovery CLI backs up, verifies and
restores original Controller v1, Artifact v3 and Node v14 stores to new roots. Repeated original
commands return their original receipts. `/tmp/rg-quant-native-6-recovery` retains profile, SQL,
manifest, receipts and the refusal output. Manifest digest is
`sha256:a927d2fc9d80e4fba4fdd98def420dbec5096d284fa08d906126a0e2200299c0`.
Independent SQL reads verify active grants, original borrowers, claims and Node handles before any
restored service starts. External Local EAIOS/input storage is explicitly outside this bundle.

The genuine v55 Controller, SHA-256
`674a34b06c88da5dc1158e83df2dcaca5f3aafbcd8c93e40403df36aaab7ed86`, reports its original schema and
exits 1 on the restored v56 checkpoint. Original checkpoint bytes remain unchanged. No relabeling,
old-binary patching or fabricated migration makes this refusal pass.

## Batch checks and evidence limits

Workspace Rust regression passed 1,170 tests with two existing ignores, using default test stacks.
All-target/all-feature Clippy and the 282 integration-server tests passed. The final Control-only
batch adds independent Context prefix/joint-borrower calendar counterexamples. Mission contracts,
recovery-tool regression, strict typing, Ruff and function-documentation checks also pass.

The enlarged HTTP dispatch future exposed a real default-stack overflow. The shared poll wrapper
now pins the original future once on the heap while preserving every original synchronous poll
and writer boundary. The full suite passes without increasing stack environment settings.

This creates zero devices, external model calls or simulators. Software process slots are logical
allocations over actual CPU children, not CPU/GPU partitioning or hardware safety. Physical stopping,
cross-host continuity, full HA and `SoftwareComplete` do not follow from these results.
