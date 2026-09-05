# MissionPlan v0.4

MissionPlan v0.4 keeps the v0.3 logical `(task_id, role_id)` relation endpoints and adds
execution coupling modes, typed relation descriptors, selective Group shared views, and a
transport-neutral direct peer channel descriptor. v0.2 and v0.3 remain accepted compatibility
inputs and are normalized to this canonical domain shape.

Coupling mode is scoped to a `CoordinationContext` with an optional Task override. It declares
which coordination mechanisms are needed; it does not select a control algorithm. The Mission
DAG remains the authority for phase readiness, while Runtime observes relation state and fences
ambiguous progression. Relative pose/distance and high-frequency peer control remain Local EAIOS
responsibilities.

Group shared views bind logical pose/velocity fields to an exact registered State export id and
payload schema. Execution fields are Runtime-backed by logical Task/Role and cannot select a State
export. Freshness comes from the selected State record's existing receive-time/TTL evidence;
RoboGuide never infers field semantics from channel names. Shared spatial references use
the existing typed map/revision identity and carry no Artifact bytes.
Runtime-backed execution status does not reuse State TTL freshness; Runtime `Unknown` retains its
existing recovery-pending meaning.

`SequentialHandoff` reuses DAG readiness and Task lifecycle evidence. Concurrent modes require a
declared shared view and relation evidence; tightly coupled mode additionally requires a
deployment-confirmed peer channel. These are mechanism requirements, not control algorithms.

Contract validity is intentionally broader than implementation support. The current Controller
profile executes `requires-active` and `shared-spatial-reference`; all other reserved relation kinds
are rejected by preflight before Group creation. Strong localization evidence must prove both live
attempts use the declared immutable map revision and frame. The Mission schema does not encode
distance/angle thresholds or Local EAIOS control formulas.
