# MissionPlan v0.8: Physical Executor Constraints

Additive to v0.7 at the semantic boundary:

- `mission.actors[].physical_entity` optionally references one admitted deployment physical
  identity. MI must validate it against the exact frozen Grounding Context; it never names a
  Node, Host, or Local EAIOS. Omit it when the user has not selected a specific entity.
- `contexts[].executor_constraints` is required (use `[]` when unconstrained). Each
  `distinct-physical-entities` constraint lists at least two unique ContextRole IDs referring
  to different logical Actors. Control binds those Actors to distinct PhysicalEntities within
  that Context, not necessarily distinct Nodes in the general model.

The executable Node routing profile currently supports one independently addressable entity
per Node. This is a deployment limitation, not MissionPlan semantics. The application owns a
versioned physical-entity registry; Control checks its current routing at Commit and Bind.
v0.7 and earlier plans remain compatibility inputs and cannot carry these v0.8 fields.

See [ADR-0040](../../../docs/decisions/0040-mission-actor-binding-semantics.md).
