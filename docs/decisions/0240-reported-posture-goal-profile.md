# ADR-0240: Reported posture as a deployment Task predicate

Status: Accepted for isolated software validation; not installed in the field.

The dog2 down trial on 2026-11-09 completed its Pilot stop/report branch while a
subsequent fresh aggregate still reported standing. The user confirmed no lie-down.
Its admitted Task used execution-report, so its historical Completed lifecycle is
preserved. Local execution completion and the declared physical goal are distinct.

Reuse the Task satisfaction authority from ADR-0033/0104. An explicitly admitted
verifier-evidence Task waits at AwaitingSatisfaction after local completion. A trusted
deployment verifier binds its verdict to the frozen source revision, tenant, accepted
plan digest and complete current Role/attempt set through /v1/verifier-evidence.
Final positive evidence may satisfy the Task and advance dependencies; final negative
evidence rejects satisfaction. Unknown/provisional evidence does neither. Existing
Controller identity, freshness, persistence, deduplication and recovery remain owners.
Do not add a parallel task lifecycle or special stand/down action catalog entries.

The optional observation.posture.verify-reported@v1 profile assesses only the narrower
predicate that a newer bounded-age Robonix aggregate reports standing or down. A
deployment captures an attributed RG posture anchor after verifying the exact local
attempt is Completed and the Task is AwaitingSatisfaction. A subsequent observation
must preserve source, channel, epoch and provider registration while advancing RG
sequence, native receive identity and Controller receive ordering beyond that anchor.
Re-exporting one cached sample cannot pass. Source changes require a newly captured
post-completion anchor; neither reconnection nor an old terminal execution is replayed.

Readers require reachable inventory, exact State identity and original receive/TTL,
then the shortest of Controller retention, RG's 5-second aggregate ceiling and
Robonix's own stale_ms. A read never renews these budgets. The assessment's remaining
budget is only a snapshot budget; a publisher must subtract its own queue/transport
budget before publishing. The pure assessor neither authenticates input files nor
publishes evidence. Its caller owns authenticated reads, exact Task/attempt binding,
source enrollment, finite publication and original receipt retention.

Robonix remains responsible for local task execution, chassis control and immediate
safety. Aggregate posture has no individual measurement timestamp; publisher-to-
Controller transport age, damping and remote-controller connectivity remain unknown.
The profile proves an attributed reported-state predicate, not physical effect,
safe stop, movement permission or permission to retry. A contradictory posture can
reject a declared goal without proving hardware has stopped. Moving/continuous work
retains the existing stop/revoke/Unknown requirements; the isolated noncontinuous
fixture is not a navigation deployment profile.

The maintained posture_goal_process_gate.py uses private production Controller/Node
processes with synthetic loopback reports, no model, robot or Pilot. It proves the
ordinary generic ingress and recovery path. Production producer enrollment and real
physical trials remain separate work; existing field Missions and services are untouched.
