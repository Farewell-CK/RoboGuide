# ADR-0044: Deployment planning evidence for Mission Intelligence

- Status: Proposed for review
- Date: 2026-09-24

## Context

The Canonical Capability Catalog defines the legal vocabulary, while live Node registration
supplies feasibility to Control. Neither tells Mission Intelligence which abstract mobility
classes this deployment supports or which scene facts are known before a Mission is planned.
The Episode51 review found that EMOS Stage1 receives robot mobility descriptions and scene
context, but MI's frozen input lacked equivalent attributed information. Reading live Node
Inventory in MI or inferring floor requirements from goal names would cross existing authority
boundaries. Habitat samples agent start state at reset, after Control assignments have arrived.

## Decision

1. A deployment may configure a startup-frozen, digest-bound planning profile with abstract
   capability classes. Each fact must use an attribute defined by the Canonical Capability
   Catalog. The profile has no Node, ResourceId, PhysicalEntityId, health, lease, reservation,
   or current availability fields. Its identity and digest are retained with provider identity
   evidence; a class id never becomes a Mission Actor or placement request.
2. A Local EAIOS adapter may publish versioned environment-authoritative planning-world
   evidence before Mission deliberation. It binds run, episode, scene, dataset revision/digest,
   and source revision to static entity-region/floor facts, versioned same/different-floor
   relations, and explicit unavailable gaps.
   Reading this source must not reset or step the simulator, query live Control/Node inventory,
   or claim sampled start poses. Missing exact entity mappings remain unknown.
3. Mission Service loads the fixed artifact, validates its complete shape and digest, and
   freezes it in Grounding Context v0.3. When semantic evidence is also supplied, the two
   sources must agree on run, episode, scene, and dataset identity. A missing or malformed
   optional source is an acquisition gap; it does not become a guessed floor or a user
   clarification by itself.
   Existing v0.2 contexts remain readable and unchanged.
4. Interpreter sees the frozen context; Planner, Reviewer, and Repairer receive the same
   planning-world facts and deployment profile. A typed Role constraint requires evidence
   from the actual task and world, not merely the existence of a capability class. Control
   alone matches constraints against current Node registration and commits resources.
5. B1 provenance accepts v0.2 and v0.3 Grounding Context. For v0.3, it verifies the original
   adapter artifact against MI's frozen snapshot, the semantic evidence, and the frozen B1
   workload. This adds no Formal population exclusion based on semantic goal coverage or
   physical success.

## Consequences and limits

This separates task semantics, static environment knowledge, abstract deployment capability,
and live placement authority. It does not prove a robot can reach a particular target. The
current Habitat episode records a target transform but does not expose an exact pre-reset
object-instance transform under the same handle; start poses are sampled at reset. Such facts
remain explicit gaps. A later design may need an admitted pre-assignment start-state or
topology contract before automatically requiring cross-floor mobility. The current shared-world
adapter still requires two distinct endpoint assignments to begin one joint episode; this is a
deployment start condition, not a benchmark requirement for distinct physical executors.
