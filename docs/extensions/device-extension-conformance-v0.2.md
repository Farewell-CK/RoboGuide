# Device Extension Conformance v0.2

This slice extends [v0.1](device-extension-conformance-v0.1.md) without changing its historical
node-config/v0.6 contract. The responsibility table, fixed-route safety boundary, durable execution
lifecycle, and offline-versus-hardware evidence distinction remain unchanged.

## Current contract

Extension Conformance v0.2 requires `roboguide.node-config/v0.7`. The configuration separates:

- `capability_profiles`: exact canonical capability evidence, one LocalSystem owner, typed scalar
  attributes, and one fixed readiness observation;
- `operations`: canonical Operation identity, one LocalSystem owner, required resources and locks,
  plus a fixed execute/status/cancel workflow.

An operation mapping does not create capability evidence. A capability profile does not prove that
the Local Integration Engine has an executable workflow. Node Contract v0.6 preserves independent
Capability Profile and canonical Operation Support declarations in Registration and carries the
complete scalar-profile `ExecutionIntent`, including objective, into the configured workflow
context. Operation Support exposes no workflow details or other Local How.

Node configs v0.2-v0.6 remain compatibility inputs with their original combined `capabilities`
shape. They are not silently normalized into v0.7 authored semantics. The current contract and
version matrix are documented in [`contracts/node/v0.9`](../../contracts/node/v0.9/README.md) and
[`contracts/node/README.md`](../../contracts/node/README.md).

## Offline evidence

For node-config/v0.7, the current report identity is
`roboguide.extension-conformance/v0.2`. It proves that:

- profile attribute values are scalar, deterministic, and bound to a unique LocalSystem owner;
- every exact profile has a fixed readiness observation;
- every operation has a complete fixed-route workflow and valid resource ownership;
- request mappings remain closed JSON Pointer/constant/whitelisted transformations;
- no network-supplied intent can select an endpoint, executable, service, method, or tool.

The report does not prove endpoint reachability, Local EAIOS field meaning, physical safety, or
hardware behavior. Those remain deployment-owned runtime and hardware tests.

Validate the current generic configuration without contacting a Controller or Local EAIOS:

```bash
cargo run -p roboguide-node -- --validate config/node.toml
```

The v0.6 three-driver fixture remains available as compatibility evidence and still emits its
original `roboguide.extension-conformance/v0.1` report shape:

```bash
cargo run -p roboguide-node -- --validate \
  scenarios/extension-conformance-v0.1/node.toml
```

Failure diagnostics use `capability_profiles.<contract>`, `operations.<operation>`, connection, and
workflow-step paths. The report retains legacy `capabilities` only when compiling a v0.6 source.

## Deferred evidence ingress

Verifier evidence does not enter Node Contract v0.6. A future generic evidence-ingress slice may
allow Node Protocol to transport one evidence source, but transport acceptance or Node-local
execution completion must not declare Task satisfaction. Orchestration remains responsible for
applying the Mission-declared satisfaction basis.
