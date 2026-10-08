# ADR-0070: Deployment registration and reset-bound relocation evidence

- Status: Accepted
- Scope: Habitat Local EAIOS deployment and Mission grounding evidence; no Core or MissionPlan contract change

## Context

The generic `object.relocate@v1` path already preserves the canonical object, source,
and destination through Control and the original EMOS Stage2 skills. The remaining
admission gap was that a checked-in capability declaration could be treated as
readiness without proving that the loaded Habitat child had the declared robot
class and the original nav/pick/place/wait skills. The planner also had no neutral,
reset-bound reference for the actual object source location.

## Decision

A deployment-owned relocation profile is frozen from the exact Node v0.7 TOML files
before a shared-world child is admitted. It binds the operation to one capacity-one
`space` resource, one local lock, one readiness route, and an explicit loaded robot
type. The child rechecks the Node file digests and the constructed Habitat robot
class; mismatches fail before readiness. The profile contains no live Node inventory
and creates no Control reservation.

After the existing single Habitat reset, the child reads the PDDL problem's exact
movable and destination entities, both agent poses, and read-only grasp state. It
writes a digest-bound `roboguide.habitat-relocation-start/v0.1` artifact. Each object
source is an `initial-location:<sha256>` reference derived from the observed reset
identity and position. It does not claim receptacle containment, route feasibility,
physical ownership, or a future predicate truth. A missing read, non-empty/unknown
gripper, changed world identity, stale source, or changed destination fails closed
and preserves the raw incomplete artifact.

The Mission-facing planning evidence uses
`roboguide.authoritative-planning-world-evidence/v0.3`. Its `object_sources` are
neutral source references only; MI cannot select a Node, ResourceId, or physical
executor. v0.1 and v0.2 artifacts remain readable without implicit upgrading.

## Consequences

- Registration, reset evidence, canonical invocation, local skill completion, Task
  satisfaction, and Habitat's official metric remain separate authorities.
- Existing navigation-only deployments stay unchanged because relocation remains
  default-off and requires the profile explicitly.
- The current code is ready for a fixed, controlled physical preflight only after
  the runner is wired to generate and pass the profile; no experiment result is
  implied by this ADR.
