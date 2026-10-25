# ADR-0213: Explicit listener capacity and transport observations

Status: Accepted for implementation, 2026-10-26.

Integration owns Node, Operator and Artifact transport capacity; Mission Service owns its HTTP
workers. Each listener must acquire a process-local permit before TLS or a request worker. The
permit follows the actual connection until close, failure or timeout. Explicit integer deployment
limits are bounded from 1 to 1024 and retain the default 128; Node HTTP/2 streams have an independent
bounded per-connection limit. No implicit Node/device inventory supplies these values.

Node TCP admission must preserve tonic's actual TcpConnectInfo/TlsConnectInfo and certificate
witnesses. Initial protocol traffic and TLS handshake have finite deadlines; HTTP/2 stream and
keepalive limits cannot replace the original Node session, sequence, acceptance or lease rules.
Slow or excess connections fail before application admission. Committed work, original Unknown,
outbox and current resource ownership never roll back because a response connection closes.

Fixed-label connection active/limit/rejection observations are best effort, bounded process facts.
They confer no identity, authorization, current Node health or device continuity. Existing metrics
roles and current revocation still gate reads. Prometheus/Alertmanager consume explicit endpoint,
TLS and time-budget profiles; short actual firing/resolved receipts do not prove long-term SLO or
human acknowledgement. Operational runbooks retain original clocks, evidence and recovery order.

These are deployment and transport boundaries, not new application authority. Controller wrapper,
Node/Plan/Memory contracts and recovery semantics remain unchanged. Replica child environment
allowlists must validate the same explicit ranges and retain them in original policy identity.
Acceptance uses actual owned TCP/TLS/HTTP services, incomplete handshakes, capacity refusal,
permit recovery, current metrics and an independently responsive peer listener, with zero devices
or model calls. Full non-release composition and the original software completion matrix remain
mandatory before overall acceptance.

The independently deployable reference Memory TCP/Unix, Spatial Unix and Robonix HTTP adapters
own the same bounded worker rule locally. Their explicit connection/time limits do not change
backend job/use authority. Header parsing starts under the original socket timeout, and a finite
whole-transport watchdog closes only I/O; committed jobs retain their original journals, process
budgets and outcomes. Original worker permits remain held until handlers actually finish. The
Robonix facade supports only literal loopback. Local `/v1/transport` observes only that process.

Node's actual TCP wrapper also enforces a finite 15-second read-idle deadline after initial traffic,
including authenticated TLS peers that never provide HTTP/2 setup. Real HTTP/2 keepalive and Node
traffic may keep a transport alive; neither supplies a Node heartbeat, health or Control lease.
Mission's default total transport limit is 35 seconds; local adapters use 30 seconds. Explicit
positive limits up to 900 seconds remain deployment inputs, independent of model/job budgets.
