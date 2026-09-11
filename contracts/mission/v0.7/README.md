# MissionPlan v0.7

MissionPlan v0.7 normalizes the Mission semantic boundary:

- Mission Actors are declared once; ContextRoles reference Actors and TaskRoles
  reference ContextRoles.
- each TaskRole has one or more extensible canonical capability requirements;
- canonical OperationRef and semantic objective are separate from capability evidence;
- timing contains Mission constraints only and no Planner-authored duration estimate;
- satisfaction declares an expected effect and either execution-report compatibility
  or a separate verifier-evidence policy.

`resources` retain the existing exclusive ResourceId capacity contract. Task timing is the
temporal scheduling constraint. A deployment-defined `time` resource, when used, is an exclusive
named token and does not represent elapsed time or a duration estimate.

Live Node inventory is intentionally absent. A known plan with zero current providers
remains semantically valid and may wait in Control.
