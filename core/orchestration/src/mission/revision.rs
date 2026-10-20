//! Accepted plan versions preserve original goals, facts, identities and timing anchors.

use super::super::*;
use super::serialization::mission_plan_json;
use super::timing::{release_mission_scheduling, validate_plan_timing_anchor};
use sha2::{Digest as _, Sha256};

/// Maximum accepted replacements per Mission; deployment policy may impose a smaller original budget.
pub const MAX_MISSION_PLAN_REVISIONS: usize = 16;

/// One immutable original configuration and the exact reviewed successor accepted by Orchestration.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptedPlanRevision {
    /// Monotonic revision, starting at one; original admission is revision zero.
    pub revision: u64,
    /// Actual application switch command, never reused for a different candidate.
    pub command_id: String,
    /// Original accepted published plan, retaining its schema and semantic definitions.
    pub source_plan: serde_json::Value,
    /// Original Group, whose Task/attempt history remains inspectable.
    pub source_group_id: ExecutionGroupId,
    /// New unbound Group; normal Control scheduling remains mandatory.
    pub target_group_id: ExecutionGroupId,
    /// Canonical reviewed complete candidate identity.
    pub target_plan_digest: String,
    /// Immutable application policy/Review receipt identity checked before switching.
    pub review_digest: String,
    /// Actual satisfied Tasks inherited from the original Group, never local completion alone.
    pub completed_tasks: BTreeSet<TaskId>,
    /// Original local Commit decision time; accepted_at remains the initial Mission time.
    pub committed_at: TimestampMs,
}

/// Hashes the complete published semantic plan without selecting resources or normalizing away identity.
pub fn mission_plan_digest(plan: &MissionPlan) -> String {
    format!(
        "sha256:{:x}",
        Sha256::digest(mission_plan_json(plan).to_string().as_bytes())
    )
}

/// Rejects malformed original evidence digests rather than treating labels as accepted Review authority.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

/// Decodes every historical plan through the maintained published constructor and mechanism checks.
fn historical_plan(value: &serde_json::Value) -> Result<MissionPlan, OrchestrationError> {
    let plan = decode_mission_plan(&value.to_string())?;
    SupportedMechanismProfile::current().validate(&plan)?;
    Ok(plan)
}

/// Holds Context roles, safety/occupancy and continuity contracts fixed while relations may be revised.
fn context_contracts(plan: &MissionPlan) -> Vec<serde_json::Value> {
    plan.contexts()
        .iter()
        .map(|context| {
            let mut value = serde_json::to_value(context).expect("typed Context serialization");
            value
                .as_object_mut()
                .expect("typed Context object")
                .remove("relations");
            value
        })
        .collect()
}

/// Validates a semantic replacement without changing original facts or permitting renamed repetition.
fn validate_delta(
    source: &MissionPlan,
    target: &MissionPlan,
    completed: &BTreeSet<TaskId>,
    used: &BTreeSet<TaskId>,
) -> Result<(), OrchestrationError> {
    if source.goal() != target.goal()
        || source.actors() != target.actors()
        || source.schema_version() != target.schema_version()
        || source.binding_semantics() != target.binding_semantics()
        || context_contracts(source) != context_contracts(target)
    {
        return Err(OrchestrationError::InvalidContract(
            "revision changed original goal, Actors, schema or Context safety contract".into(),
        ));
    }
    for id in completed {
        if source.task_graph().task(id).is_none()
            || source.task_graph().task(id) != target.task_graph().task(id)
        {
            return Err(OrchestrationError::InvalidContract(
                "revision removed or altered an actually satisfied Task".into(),
            ));
        }
    }
    for task in target
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| !completed.contains(task.task_id()))
    {
        if used.contains(task.task_id()) {
            return Err(OrchestrationError::InvalidContract(
                "unfinished replacement Task identity was already used by an original version"
                    .into(),
            ));
        }
        for satisfied in completed
            .iter()
            .filter_map(|id| source.task_graph().task(id))
        {
            if task.execution_intents().values().any(|intent| {
                satisfied.execution_intents().values().any(|original| {
                    intent.operation() == original.operation()
                        && intent.parameters() == original.parameters()
                })
            }) {
                return Err(OrchestrationError::InvalidContract("revision repeats an already satisfied operation/parameters under a new identity".into()));
            }
        }
    }
    Ok(())
}

impl MissionOrchestrator {
    /// Checks a reviewed alternative's declared completion precondition without asserting those facts.
    pub fn validate_plan_alternative(
        &self,
        mission: &MissionId,
        target: &MissionPlan,
        completion_precondition: &BTreeSet<TaskId>,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission)
            .ok_or_else(|| OrchestrationError::Mission("unknown alternative Mission".into()))?;
        SupportedMechanismProfile::current().validate(target)?;
        validate_plan_timing_anchor(target, execution.accepted_at)?;
        let mut admission = self.clone();
        admission.executions.remove(mission);
        admission.validate_admission_capacity(target)?;
        let mut used = BTreeSet::new();
        for version in &execution.revisions {
            used.extend(
                historical_plan(&version.source_plan)?
                    .task_graph()
                    .tasks()
                    .iter()
                    .map(|task| task.task_id().clone()),
            );
        }
        used.extend(
            execution
                .plan
                .task_graph()
                .tasks()
                .iter()
                .map(|task| task.task_id().clone()),
        );
        validate_delta(&execution.plan, target, completion_precondition, &used)
    }

    /// Reviews current accepted semantic/fact identity without holding resources or creating a Group.
    pub fn validate_plan_revision(
        &self,
        mission: &MissionId,
        target: &MissionPlan,
        control: &ControlPlane,
    ) -> Result<BTreeSet<TaskId>, OrchestrationError> {
        let execution = self
            .executions
            .get(mission)
            .ok_or_else(|| OrchestrationError::Mission("unknown revision Mission".into()))?;
        if execution.goal_gate.is_none()
            || execution.goal_gate.as_deref()
                != control
                    .mission_goal_contract(mission)
                    .map(|contract| contract.digest.as_str())
        {
            return Err(OrchestrationError::InvalidContract(
                "runtime revision requires the original independent Mission goal gate".into(),
            ));
        }
        if !matches!(
            execution.lifecycle,
            MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running
        ) || execution.revisions.len() >= MAX_MISSION_PLAN_REVISIONS
            || execution.is_crash_loop_quarantined()
        {
            return Err(OrchestrationError::Mission(
                "terminal/quarantined Mission or exhausted original revision bound".into(),
            ));
        }
        SupportedMechanismProfile::current().validate(target)?;
        validate_plan_timing_anchor(target, execution.accepted_at)?;
        // Recount the replacement together with every other live Mission; existing identity is no bypass.
        let mut admission = self.clone();
        admission.executions.remove(mission);
        admission.validate_admission_capacity(target)?;
        control.validate_mission_group_plan(&execution.group_id, &execution.plan)?;
        let completed = control
            .group(&execution.group_id)
            .expect("validated original Group")
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect();
        let mut used = BTreeSet::new();
        for version in &execution.revisions {
            used.extend(
                historical_plan(&version.source_plan)?
                    .task_graph()
                    .tasks()
                    .iter()
                    .map(|task| task.task_id().clone()),
            );
        }
        used.extend(
            execution
                .plan
                .task_graph()
                .tasks()
                .iter()
                .map(|task| task.task_id().clone()),
        );
        validate_delta(&execution.plan, target, &completed, &used)?;
        Ok(completed)
    }

    /// Commits a reviewed stopped/revoked replacement inside the caller's original application transaction.
    #[allow(clippy::too_many_arguments)]
    pub fn commit_plan_revision<E: EventSink>(
        &mut self,
        mission: &MissionId,
        target: MissionPlan,
        target_group: ExecutionGroupId,
        command_id: &str,
        review_digest: &str,
        control: &mut ControlPlane,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<AcceptedPlanRevision, OrchestrationError> {
        let completed = self.validate_plan_revision(mission, &target, control)?;
        let original = self.executions.get(mission).expect("validated Mission");
        if original.isolation.is_none()
            || command_id.trim().is_empty()
            || command_id.len() > 128
            || !valid_digest(review_digest)
            || now < original.accepted_at
            || original.revisions.iter().any(|version| {
                version.command_id == command_id || version.source_group_id == target_group
            })
        {
            return Err(OrchestrationError::Mission("revision requires its durable hold, original command, reviewed digest and new Group".into()));
        }
        let version = AcceptedPlanRevision {
            revision: original.plan_revision() + 1,
            command_id: command_id.into(),
            source_plan: mission_plan_json(&original.plan),
            source_group_id: original.group_id.clone(),
            target_group_id: target_group.clone(),
            target_plan_digest: mission_plan_digest(&target),
            review_digest: review_digest.into(),
            completed_tasks: completed.clone(),
            committed_at: now,
        };
        let mut candidate = self.clone();
        let mut authority = control.clone();
        let mut pending = ports::PendingEvents::default();
        release_mission_scheduling(
            &mut authority,
            mission,
            "runtime plan superseded",
            now,
            correlation,
            &mut pending,
        );
        authority.supersede_mission_group(
            &version.source_group_id,
            target_group.clone(),
            &original.plan,
            &target,
            now,
            correlation,
            &mut pending,
        )?;
        {
            let execution = candidate
                .executions
                .get_mut(mission)
                .expect("original Mission");
            execution.revisions.push(version.clone());
            execution.plan = target;
            execution.group_id = target_group;
            execution.isolation = None;
            execution.scheduling_deferrals.clear();
            execution.lifecycle = MissionExecutionLifecycle::Running;
            execution
                .dispatch_policy
                .retain_revision_waiting(&execution.plan, &completed);
        }
        candidate.refresh_ready(mission, &mut authority, now, correlation, &mut pending)?;
        candidate.validate_control_authority(&authority)?;
        pending.append(
            now,
            correlation,
            None,
            domain::EventPayload::MissionPlanRevisionCommitted {
                mission_id: mission.clone(),
                revision: version.revision,
                source_group_id: version.source_group_id.clone(),
                target_group_id: version.target_group_id.clone(),
                target_plan_digest: version.target_plan_digest.clone(),
                completed_tasks: completed.into_iter().collect(),
            },
        );
        *self = candidate;
        *control = authority;
        pending.publish(events);
        Ok(version)
    }
}

/// Checks the complete immutable chain without inventing a missing historical plan or resetting time.
pub(super) fn validate_version_chain(
    execution: &MissionExecution,
) -> Result<(), OrchestrationError> {
    if execution.revisions.len() > MAX_MISSION_PLAN_REVISIONS
        || !execution.revisions.is_empty() && execution.goal_gate.is_none()
    {
        return Err(OrchestrationError::Mission(
            "plan revision bound exceeded or original goal gate absent".into(),
        ));
    }
    let mut groups = BTreeSet::new();
    let mut commands = BTreeSet::new();
    let mut used = BTreeSet::new();
    let mut previous_time = execution.accepted_at;
    let mut previous_completed = BTreeSet::new();
    for (index, version) in execution.revisions.iter().enumerate() {
        let source = historical_plan(&version.source_plan)?;
        let (target, target_group) = if let Some(next) = execution.revisions.get(index + 1) {
            (historical_plan(&next.source_plan)?, &next.source_group_id)
        } else {
            (execution.plan.clone(), &execution.group_id)
        };
        if version.revision != index as u64 + 1
            || version.target_group_id != *target_group
            || !groups.insert(version.source_group_id.clone())
            || version.source_group_id == execution.group_id
            || !commands.insert(version.command_id.clone())
            || version.command_id.trim().is_empty()
            || version.command_id.len() > 128
            || version.committed_at < previous_time
            || !valid_digest(&version.review_digest)
            || version.target_plan_digest != mission_plan_digest(&target)
            || !previous_completed.is_subset(&version.completed_tasks)
        {
            return Err(OrchestrationError::Mission(
                "inconsistent original plan revision chain".into(),
            ));
        }
        validate_plan_timing_anchor(&source, execution.accepted_at)?;
        used.extend(
            source
                .task_graph()
                .tasks()
                .iter()
                .map(|task| task.task_id().clone()),
        );
        validate_delta(&source, &target, &version.completed_tasks, &used)?;
        previous_time = version.committed_at;
        previous_completed = version.completed_tasks.clone();
    }
    Ok(())
}

/// Rechecks actual Control sources and inherited completion facts on every authority validation.
pub(super) fn validate_version_authority(
    execution: &MissionExecution,
    control: &ControlPlane,
) -> Result<(), OrchestrationError> {
    validate_version_chain(execution)?;
    for version in &execution.revisions {
        let original_plan = historical_plan(&version.source_plan)?;
        control.validate_mission_group_plan(&version.source_group_id, &original_plan)?;
        let source = control
            .group(&version.source_group_id)
            .expect("validated original Group");
        let target = control
            .group(&version.target_group_id)
            .ok_or_else(|| OrchestrationError::Mission("revision target Group is absent".into()))?;
        let completed: BTreeSet<_> = source
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect();
        if source.lifecycle() != GroupLifecycle::Released
            || source.superseded_by() != Some(&version.target_group_id)
            || completed != version.completed_tasks
            || version.completed_tasks.iter().any(|id| {
                target
                    .task_execution(&TaskRef::new(
                        execution.plan.goal().mission_id().clone(),
                        id.clone(),
                    ))
                    .is_none_or(|task| task.lifecycle() != TaskExecutionLifecycle::Completed)
            })
        {
            return Err(OrchestrationError::Mission(
                "revision lacks its actual original released Group/completion lineage".into(),
            ));
        }
    }
    Ok(())
}
