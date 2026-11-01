Provider output profile: host-objective-v1.

Return provider_profile="host-objective-v1" as required by the output schema.
Omit mission.objective entirely, including in any replacement_plan. The host assembles that field
from the unchanged grounded_intent.objective. This profile-specific output rule replaces the legacy
objective echo rule. Never emit an objective field even when copying an input canonical plan.

You must still satisfy the complete original goal, constraints and assumptions in all generated
Tasks, parameters and satisfaction conditions. Retain the exact requested mission.id. The Reviewer
receives the full assembled plan and the unchanged original grounded intent. This assembly proves
no semantic correctness and grants no execution, resource or goal-satisfaction authority.
