# ADR-0204: Default sampling headroom for current Node status

- Status: Accepted
- Date: 2026-10-25

The actual typed-map process gate exposed a default timing conflict. Node Service samples health
and reports management facts on the negotiated heartbeat interval. The original default interval
and Control's default maximum reported-status age were both 5,000 ms. Normal timer/network jitter
could make an otherwise reachable Node's actual status stale at a continuous preparation/use check.
The refusal was correct, but the defaults unnecessarily forced the original import into retained
Unknown or stop/hold. A fifteen-second transport lease does not make an old health observation fresh.

Use 1,000 ms as the default advertised heartbeat/sample interval. Preserve the fifteen-second lease,
Control's independent five-second status freshness requirement, original timestamps and explicit
deployment overrides. Existing Welcome fields convey the actual selected interval; Node wire/storage
contracts do not change. A configured interval is never a claim that a source sampled or arrived on
time, and heavy delays still correctly fence current authority.

Do not recover the failed historical imports by replay, changing their source sessions or extending
their original budgets. The maintained full map/process path runs on fresh stores to establish the
new default behavior; the original refusals and Unknown histories stay available independently.
This changes software defaults only, creating no Node/device identity, physical safety or timing claim.
