# RoboGuide Node Service Contract v0.4

Node configuration v0.4 keeps Node Protocol v0.2 and the v0.3 artifact data plane unchanged. It
adds a required, fixed readiness observation to every canonical capability binding.

Readiness answers whether one exact canonical contract can execute now. It is separate from the
local-system health check: a node may truthfully remain Online while one capability is unavailable
because a required service, mode, sensor, or local dependency is missing.

Each `readiness` block fixes one HTTP, dynamic gRPC, or MCP operation, a response JSON Pointer,
nonempty and disjoint `ready` / `unavailable` state mappings, and optional descriptive detail.
Readiness request mappings may use deployment constants only. Mission input and execution intent
cannot select the probe, endpoint, method, service, or tool.

The Node Service observes readiness before its initial Register. Later changes produce a complete
RegistrationUpdate using the existing Node Protocol v0.2 management stream; RegistrationUpdate and
Heartbeat share one monotonically increasing session sequence. Probe transport, mapping, or unknown
state failures fence only the affected contract as unavailable.

Configurations v0.2 and v0.3 remain loadable and preserve their legacy static-ready behavior. They
do not satisfy the Phase 1 hardware-readiness acceptance gate. A deployment must migrate to v0.4
and validate every probe against the actual Local EAIOS before it is treated as a stable hardware
baseline.

For the complete device-extension acceptance path, run the offline compiler and conformance report
from the repository root:

```bash
cargo run -p roboguide-node -- --validate scenarios/extension-conformance-v0.1/node.toml
cargo test -p node-service conformance --locked
```

The report proves only static configuration. Node Service lifecycle rules are reported separately
as implementation guarantees, while `runtime_probes_executed` and `hardware_probes_executed` remain
false. Endpoint reachability, reflection compatibility, vendor field semantics, safety interlocks,
and physical actuation still require a deployment-owned local-system or hardware test. The v0.1
report retains `lifecycle` as a compatibility alias for `implementation_guarantees`; neither field
is runtime-probe evidence.
