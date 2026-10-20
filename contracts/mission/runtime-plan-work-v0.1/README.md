# Runtime MI work discovery v0.1

`GET /v1/runtime-plan-work?limit=1..32&after=<last-mission-id>` requires actual mTLS and one
explicit tenant. The Controller derives the stable principal from the certificate; extra or
duplicate query keys fail. It returns only committed, unexpired Generating tickets for that
proposer/tenant and current source Group/revision. `next_after: null` ends the bounded page.
Consumers recheck the complete original authority before claiming or spending any provider nonce.

`GET /v1/mission-requests/by-mission/<percent-encoded-mission-id>` requires Mission viewer and
one explicit tenant. The existing unique Mission index and persisted tenant JOIN return the exact
original Request. Missing, other-tenant and unmigrated rows return the same 404. The reply is
`{"schema_version":"roboguide.mission-request-lookup/v0.1","mission_id":"...","request_id":"..."}`.

Worker v0.2 explicitly configures `[discovery] page_size = 32, max_pages = 4` and has no static
`request_ids`. The in-memory scan cursor grants no authority and resets after the final page;
bounded scans continue at their cursor on the next poll. v0.1 keeps its original static semantics.
These reads create no Mission, ticket, provider call, Review or execution. See ADR-0123.
