# C1-S1 Post-fix Findings

The Node Service now treats a failure after a durable local handle exists as an
observation problem first, not as permission to redispatch physical work.

## Failure classification

- Transport failures, timeouts, malformed response envelopes, and response
  mapping failures on the fixed status route are bounded reacquisition cases.
  They do not change the physical execution phase and do not call Execute.
- A successful status response resets the consecutive-failure budget and is
  reduced normally, including a true terminal `COMPLETED`, `FAILED`, or
  `CANCELLED` fact.
- A non-reacquirable local/configuration error, or five consecutive
  reacquisition failures, records `Unknown` with
  `ReconciliationRequired`. This is an explicit physical ambiguity boundary,
  not a fabricated terminal fact.
- Execute, local-handle identity, journal identity, and local-lock errors are
  never retried by the status loop. Reconnect/reacquisition therefore cannot
  create a second physical attempt or local handle.

## Production fault matrix

The existing production fault runner was rerun after the fix against the
Controller, Node Service, and persistent Habitat Local EAIOS bridge. Each case
used one mission and one physical dispatch.

| Case | Injected fault | Final mission | Execution fact |
| --- | --- | --- | --- |
| F1 | first status HTTP 500 | `Completed` | `Completed` |
| F2 | three consecutive status HTTP 500 | `Completed` | `Completed` |
| F3 | first status timeout | `Completed` | `Completed` |
| F4 | first malformed status response | `Completed` | `Completed` |
| F8 | three status HTTP 500 responses plus mission cancel | `Cancelled` | `Cancelled` |

The post-fix artifacts are retained under `/tmp/roboguide-c1-s1-post-f1` through
`/tmp/roboguide-c1-s1-post-f8` on the validation host. In every case the
execution-attempt history contains one attempt and no second dispatch was
observed. The deterministic Node Service regression suite also covers true
`Completed`, `Failed`, and `Cancelled` terminal facts, duplicate Execute during
degraded observation, bounded exhaustion, and cancellation responsiveness.

This closes the previously observed observation-continuity failure for
reacquirable status faults. A genuinely unavailable or semantically
non-recoverable status authority remains explicitly fenced for reconciliation;
the service does not claim a physical outcome it did not observe.
