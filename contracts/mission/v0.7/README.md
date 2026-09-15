# MissionPlan v0.7

MissionPlan v0.7 normalizes the Mission semantic boundary:

- Mission Actors are declared once; ContextRoles reference Actors and TaskRoles
  reference ContextRoles.
- each TaskRole has one or more extensible canonical capability requirements;
- canonical OperationRef and semantic objective are separate from capability evidence;
- timing contains Mission constraints only and no Planner-authored duration estimate;
- satisfaction declares an expected effect and either execution-report compatibility
  or a separate verifier-evidence policy.

`timing.earliest_start_offset_ms` is structurally required. Its value `0` is the canonical neutral
default: it adds no earliest-start lower bound, so the Task is immediately eligible subject to its
ordinary dependency, readiness, and Control admission rules. It does not claim that the user asked
for execution at exactly zero milliseconds. Positive values are Mission constraints and require
user or policy grounding.

`execution-report` is a valid satisfaction basis when Mission policy accepts successful completion
of the canonical Local EAIOS operation as Task completion. A physical expected effect does not by
itself require `verifier-evidence`; independent evidence is required only when the user, policy, or
Task semantics demand independent confirmation, or the execution report cannot establish the
requested effect.

`resources` retain the existing exclusive ResourceId capacity contract. Task timing is the
temporal scheduling constraint. A deployment-defined `time` resource, when used, is an exclusive
named token and does not represent elapsed time or a duration estimate.

Live Node inventory is intentionally absent. A known plan with zero current providers
remains semantically valid and may wait in Control.
