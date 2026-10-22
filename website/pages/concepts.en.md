# Concepts and terminology

These objects define continuity, resource ownership and recovery scope. Distinguish them before following the [lifecycle](lifecycle.md).

| Term | Meaning | Distinct from |
| --- | --- | --- |
| Mission Request | Dialogue, grounding, drafts and submission | A Controller-accepted Mission |
| MissionPlan | Objective, Actors, Contexts, Task DAG, constraints and satisfaction basis | One model response |
| Actor | A logical participant continuous across Tasks | Node, robot or Host |
| ContextRole | An Actor's role within a Context | One attempt |
| TaskRole | A slot referencing a ContextRole with requirements/intent | Node assignment |
| Execution Group | Persistent members, bindings, reservations and recovery context | A Task or process |
| Execution slot | Logical `(Group, Task, Role)` position | One physical attempt |
| Attempt / generation | An execution bound to an exact Node/session | Logical Task identity |
| Node | Execution, capability and liveness admission boundary | PhysicalEntity or Host |
| PhysicalEntity | Deployment-owned execution entity | Mission Actor |
| Capability Requirement | Evidence and capability required for a Role | ExecutionIntent |
| Operation Support | Exact supported canonical operation | Private Node workflow |
| ExecutionIntent | Semantic objective requested after selection | Vendor API parameters |

## An example across Tasks

One Actor produces a file in Task A and processes it in Task B. Both TaskRoles reference the same ContextRole. Control preserves Actor continuity after first Bind; ordinary recovery cannot silently switch physical entities by selecting another Node.

The Group retains cross-Task context. Each Task has its own attempts and temporary resources. B becomes ready only after A satisfies its policy and release gates. A's Imported history cannot authorize B's Memory use.

## Plan relations and runtime relations

A DAG expresses completion prerequisites; relations constrain ongoing execution. Current reducers support `requires-active` and `shared-spatial-reference`. Other typed syntax is rejected by the implementation profile.

Coupling modes declare required mechanisms without moving motion algorithms into the Controller. See the [handbook](docs/architecture/v2/architecture-handbook.md) and [Runtime](modules/runtime.md).
