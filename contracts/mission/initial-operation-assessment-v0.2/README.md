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

`fixtures/regression-cases.json` freezes synthetic source matrices and expected
neutral replies for both the actual Rust loopback HTTP assessment and Python
Request Engine tests. Matrix order is destination then endpoint; count order is
explicit in the fixture. The endpoint identities belong only to the producer's
synthetic input; MI receives logical slots and counts. Neither these authored
plans nor their synthetic submission receipts are B1 experiment results.
Frozen HTTP bodies use the production Python encoding; both languages verify
their exact plan meaning, and the Rust test emits each actual request/reply pair
under `--nocapture` for independent consumer replay without rebinding its digest.

The fixed cases distinguish witnesses, complete scoped misses, bounded search
misses, unknowns, initial serial scope, expiry and current endpoint shortage.
Identical per-Role counts can yield different joint decisions: complementary
witnesses permit a combination, while one supporting endpoint for both parallel
Actors leaves every deployment-compatible combination with a scoped miss.
Unknowns do not independently block; they also do not erase a known miss in
every combination. A serial assessment says nothing about a later Task's route.

The producer checks exact body/source identity and no checkpoint, event or
binding mutation. The consumer checks frozen goals, fresh Review attribution,
bounded reconsideration and at most one submission. Existing recovery tests
separately cover revised drafts, Review vetoes, restart and ambiguous submission.
These checks prove protocol behavior, not real-model adherence, physical arrival
or official benchmark success.
