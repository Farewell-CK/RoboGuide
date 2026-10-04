# Initial Operation Assessment v0.2

The read-only `POST /v1/missions/assess-initial-support` reply preserves v0.1's
exact request-body, source, scope and receive-relative lifetime binding. v0.2 adds
closed logical-slot `candidate_diagnostics` and `placement_failure`. Readers
accept archived v0.1 without inventing missing diagnostics; the Controller emits
v0.2. Neither reply is an admission, reservation or feasibility certificate.

Control counts registrations in the query's supplied State view (at most 128).
Each candidate contributes either to `eligible_count` or to exactly one first
failed predicate. Counts do not expose Node, entity, resource or lease identities.
`role_contract_unavailable` deliberately combines capability readiness/attributes
and declared resource capacity; it does not identify a missing physical ability.
`actor_contract_unavailable` covers whole-Actor capability requirements. These
reasons use the same predicates as Matching, with no changed admission policy.

`endpoint_cardinality` means the initial paired deployment has no combination
of eligible endpoints. This is deployment topology evidence, not a requirement
that terminal goal predicates need distinct Physical Entities.
`deployment_contract_unavailable` leaves the exact placement subcause unknown.
Unsupported or non-initial scopes do not invent candidate diagnostics.

Parsers also check counter partitions, unique slots, exact plan-slot attribution,
and agreement between eligibility and static-support counts. JSON Schema alone
cannot express those arithmetic or time relationships. Unknowns and bounded
route misses still cannot independently block execution. Query-time facts do not
become fresher because a later checkpoint shows healthy Nodes.
