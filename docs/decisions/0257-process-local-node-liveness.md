# ADR-0257 Process-local Node liveness

Current-session renewal advances the Controller's lease and live receive
projection under the original writer. It uses the original admitted receive
time. Unchanged reported health updates the live status and its freshness.
Registration, route loss, expiry, and accepted health transitions retain
durable evidence. The first accepted interval records its real Node session for
existing session provenance consumers. Health transitions retain the existing
heartbeat event type.

Checkpoints retain Node registration and the latest health or reachability
transition. Restore clears leases and starts every known Node as Unreachable.
Fresh registration establishes current-process authority. The original
heartbeat and continuous-liveness events remain readable in old history.

A heartbeat-only batch may publish a new live snapshot without a SQL commit
after verifying the original committed checkpoint, authority journal, and
absence of SQL and event changes. A batch that changes durable authority
retains the normal transaction and acknowledgment boundary.
