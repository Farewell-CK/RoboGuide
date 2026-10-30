# ADR-0230: Runtime source coverage before paired execution

Status: Implemented; offline software and actual runtime identity are separate evidence.

## Context

A frozen paired workload required the original action, reset action and reset
skill sources. The controlled producer recorded them conditionally, while the
native producer's separate list omitted them. The original runtime check properly
refused missing observations after the first arm ended. Files existing in the
private checkout do not repair the original missing runtime evidence.

## Decision and ownership

`habitat_local_eaios.source_provenance.runtime_source_modules` owns the shared
module list. Both producers record the original reset sources; native EMOS does
not install the controlled reset or other Local How policies. Controlled optional
source observations retain their original binding conditions. No action, model,
environment reset, simulator step or official success computation is added.

The same source file exposes a stdlib-only `--describe` process entrypoint with
`roboguide.local-eaios-source-coverage/v0.1`. It declares the names each producer
always emits, not actual loaded modules. It never resolves vendor imports.

Evaluation requires that exact producer file in the frozen `source_sha256` set.
After normal source validation and before any arm starts, it invokes the frozen
file with the actual selected worker Python, isolated startup and no site imports
(`-I -S -B`). Provider credentials and experiment switches are not inherited.
The owned description child has a separate five-second limit and at most 64 KiB
of captured stdout; expiry or overflow kills and waits only that child. Original
Provider, skill, world and arm budgets are unchanged.

Closed version/basis/arm fields, bounded distinct module names and complete
coverage of every configured requirement are mandatory. Unknown versions, empty
old helpers, failed children and missing coverage refuse before a pair directory
or SUT process. The passed declaration and exact producer digest are retained in
the pair's input manifest. Existing worker fields and runtime manifest v0.1 retain
their meanings; this adds source prerequisites and observations, not authority.

An older frozen source view without this entrypoint must use its original driver
or be explicitly migrated to a new reviewed version. The new driver does not
silently trust today's helper in place of the frozen producer.

## Actual observation remains mandatory

The independent runtime check still requires each exact child interpreter,
owned private module path and unchanged source bytes. Preflight only proves
declared emission coverage. It cannot establish that initialization or archival
will succeed, nor which code a future process will load. A missing actual module
continues to fail closed even after a successful declaration query.

Historical receipts and failed outcomes remain unchanged; no missing module is
inserted into old manifests. Native/controlled policies, MI, Core/Node, official
PDDL truth, Formal admission and paired physical initialization are unchanged.

## Validation

Deterministic tests exercise the real native finalizer on original exceptions,
the controlled initializer, common/conditional list separation, false/missing
coverage, malformed versions/types/duplicates, old empty helpers, child failure,
output overflow, time limits, frozen-source prerequisites, credential isolation
and refusal of missing actual reset evidence. Isolated actual Habitat Python 3.9
checks only the description path, without models, vendor imports or a simulator.
No new physical result or throughput guarantee follows from these checks.
