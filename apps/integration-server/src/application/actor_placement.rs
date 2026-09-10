//! Deployment-owned actor placement loading and validation.

use crate::*;
/// Loads and validates deployment-owned actor placement constraints from JSON.
pub(crate) fn load_actor_placement_file(
    path: &Path,
) -> Result<Vec<control::ActorNodeConstraint>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    let file: ActorPlacementFile = serde_json::from_str(&content)?;
    if file.schema != ACTOR_PLACEMENT_SCHEMA {
        return Err(format!(
            "actor placement file {} uses unsupported schema {}",
            path.display(),
            file.schema
        )
        .into());
    }
    file.constraints
        .into_iter()
        .map(|entry| {
            Ok(control::ActorNodeConstraint::new(
                domain::MissionId::new(entry.mission_id)?,
                domain::ActorId::new(entry.actor_id)?,
                domain::NodeId::new(entry.node_id)?,
            ))
        })
        .collect()
}

/// Requires a configured deployment policy to cover exactly the submitted Mission actors.
///
/// An empty Control placement set preserves generic matching. Once a placement file has installed
/// any constraints, strict coverage prevents a misspelled Mission or Actor from silently falling
/// back to deterministic unconstrained matching.
pub(crate) fn validate_actor_placement_coverage(
    control: &control::ControlPlane,
    plan: &domain::MissionPlan,
) -> Result<(), String> {
    let configured = control.actor_node_constraints().collect::<Vec<_>>();
    if configured.is_empty() {
        return Ok(());
    }
    let mission_id = plan.goal().mission_id();
    let expected = plan
        .task_graph()
        .tasks()
        .iter()
        .flat_map(|task| task.requirement().roles())
        .filter_map(domain::RoleRequirement::actor_id)
        .cloned()
        .collect::<BTreeSet<_>>();
    let declared = configured
        .into_iter()
        .filter(|constraint| constraint.mission_id() == mission_id)
        .map(|constraint| constraint.actor_id().clone())
        .collect::<BTreeSet<_>>();
    if declared == expected {
        return Ok(());
    }
    let missing = expected
        .difference(&declared)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let unknown = declared
        .difference(&expected)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    Err(format!(
        "strict actor placement coverage failed for Mission {mission_id}; missing actors [{}], unknown actors [{}]",
        missing.join(", "),
        unknown.join(", ")
    ))
}

/// Revalidates every durable Mission after checkpoint recovery and placement replacement.
///
/// This runs before the server accepts traffic or persists a replacement placement policy, so a
/// typo or incomplete policy cannot silently change the Actor authority of an existing Mission.
pub(crate) fn validate_restored_actor_placement_coverage(
    control: &control::ControlPlane,
    orchestrator: &MissionOrchestrator,
) -> Result<(), String> {
    for mission_id in orchestrator.mission_ids() {
        let execution = orchestrator.execution(&mission_id).ok_or_else(|| {
            format!("restored Mission {mission_id} disappeared during placement validation")
        })?;
        validate_actor_placement_coverage(control, execution.plan())
            .map_err(|error| format!("restored Mission placement is invalid: {error}"))?;
    }
    Ok(())
}
