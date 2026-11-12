# ADR-0237: Host enrollment while robot hardware is unavailable

Status: Accepted for the explicit two-Thor field deployment.

A reachable compute host and its Node session are independent of robot sensor,
localization, Pilot and actuator availability. The combined Connector exposes an
opt-in fixed GET `/v1/host/status` with explicit deployment Node ID, host boot/model,
bounded kernel network observations and unchanged robot observations. Host inspection
completion never proves chassis power, robot readiness or permission to move.

`system.host.inspect@v1` is an ordinary read-only operation with a typed Compute
pool per Node. Existing Task/Control/Node dispatch handles it; no Local EAIOS task
or actuator command is issued. Existing Node State carries its reported snapshot.
The sole combined Connector process owns this route; there is no extra gateway.

Each host must use its own authenticated Node identity, leaf private key, resources
and clean Node/Connector storage. Copied dog2 credentials, journals, origin receipts
or resource names must not be reused as dog1. Current static Node mTLS enrollment
may add the unique dog1 leaf to the same explicit trusted deployment; this does not
claim dynamic identity lifecycle, physical attestation or device equivalence.

First acceptance requires both authenticated Nodes in one Controller with distinct
fresh State sources, a normal two-participant host inspection Task and exact resource
release. Both role claims consume one unit from capacity-one pools, so joint Control
allocation must select separate hosts. The test does not claim physical two-robot
coordination, shared maps, navigation, chassis power detection or automatic recovery.
