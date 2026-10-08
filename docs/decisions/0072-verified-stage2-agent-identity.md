# ADR-0072: Verified deployment identity at the controlled Stage2 boundary

- Status: Accepted
- Scope: Default-off shared relocation adapter; no EMOS source, policy or capability change

## Context

The original Resume Sensor may omit an endpoint when an episode's robot variant
has no matching resume file. A loaded robot and its original skills can still be
present. Controlled RoboGuide assignments replace native Stage1, but previously
required that auxiliary resume to obtain the Stage2 `AgentArguments.robot_type`.
This could reject both committed assignments before the first model/action call.

## Decision

For explicitly configured shared relocation, use the frozen Node registration
profile's robot types only after checking exact endpoint coverage, source digests
and each actual constructed Habitat robot class. Keep those admitted identities
for both parallel assignments and single-Actor sequential reuse. Present native
resume identities must agree; foreign or contradictory identities fail closed.
The original navigation-only resume path stays unchanged.

Write `roboguide.stage2-agent-identity/v0.1` evidence identifying the registration
digest, actual admitted type mapping and missing vendor resume names. Never create
a capability resume or rewrite task context, committed targets or model actions.
Node capability/readiness checks remain separate, and this mapping grants no
physical assignment or Task-satisfaction authority.

This is an explicit controlled-arm input provenance difference from native EMOS.
It fixes the assignment boundary without editing vendor files, adopting another
robot variant, changing starts, or claiming native Stage1 would accept the same
incomplete resume. Offline tests cover original navigation rejection, paired and
sequential target binding, missing resumes, contradictions and initialization from
actual configured robot classes. Physical skill performance remains unproven.
