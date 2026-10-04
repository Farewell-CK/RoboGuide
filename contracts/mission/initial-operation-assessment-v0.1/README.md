# Initial operation assessment v0.1

This is neutral Controller-to-Request feedback, separate from MissionPlan,
model inputs, live inventory and admission. See
[ADR-0066](../../../docs/decisions/0066-initial-operation-support-feedback.md).

`POST /v1/missions/assess-initial-support` accepts the same exact MissionPlan
body as the production submission adapter. HTTP 200 means a query response;
`decision` is `blocked`, `not_blocked` or `unavailable`, never an admission receipt.
Invalid MissionPlan bodies return HTTP 400. Disabled sources return unavailable.
Request/response limits are the existing Controller HTTP limits.

The closed [schema](assessment.schema.json) records the exact transmitted-body
SHA256 separately from canonical draft digest. Task/Role/operation summaries
refer only to assessed initial slots. Counts distinguish static witnesses,
bounded misses, complete scoped disjoint geometry and unavailable/unknown data.
Node/Resource identifiers, poses and vendor PDDL data are excluded.

Usable replies require all source digests, exact initial slot coverage,
positive checked combination count and `received_at_ms <= assessed_at_ms <
expires_at_ms`, with lifetime at most 600000 milliseconds. Source times belong
to the Controller's receive-time domain. MI does not compare independent clocks.
`blocked` additionally requires actual scoped disjoint evidence; a count alone
does not validate the source geometry. Controller admission validates the
original digest-bound source before exposing a reply.

An unavailable response retains any admitted source identity/time but may have
null fields and empty roles. It cannot prove impossibility or successful execution.
The current profile inspects one/two initial slots and two deployment endpoints.
Unsupported scopes stay unavailable; no global snapshot or future resource
guarantee is promised. A reply is historical evidence, not a transferable permit.

Mission Request recovery v0.2 embeds this reply (or explicit null on interrupted
queries) at `controller_preflight`. Explicit retry queries once again with the
same reviewed plan. Only a fresh `not_blocked` reply proceeds to the existing
sole submit boundary. Default configuration remains off.
