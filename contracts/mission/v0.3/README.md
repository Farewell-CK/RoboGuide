# MissionPlan v0.3

MissionPlan v0.3 extends v0.2 Context/ContextRole continuity with explicit execution-time
coordination relations. A relation is declared inside one Context and references exact logical
`(task_id, role_id)` endpoints, never a Node, transport session, or adapter-local handle.

The first relation kind is `requires-active`: while the target execution is active, the source
execution must remain active. The Task DAG still controls readiness; relation endpoints in
different Tasks must not have a transitive DAG dependency in either direction.

See [`ADR-0020`](../../../docs/decisions/0020-execution-coordination-relations.md) for Runtime,
checkpoint, rebind, evidence, and safety authority semantics.
