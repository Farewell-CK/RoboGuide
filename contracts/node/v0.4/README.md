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
