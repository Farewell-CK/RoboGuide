#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Mission execution authority over the complete MissionPlan and its long-lived Group.

use control::{ControlError, ControlPlane, DeterministicBootstrapScheduler, GroupLifecycle};
use domain::{
    CorrelationId, ExecutionGroupId, MissionId, MissionPlan, ResourceBindingScope, ResourceId,
    TaskExecutionLifecycle, TaskId, TaskRef, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod integration_bridge;
mod mechanism_profile;
mod mission_contract;

pub use integration_bridge::{
    CONTROLLER_CHECKPOINT_SCHEMA, GroupSharedViewEntry, GroupSharedViewSnapshot,
    GroupSpatialVerification, GroupViewFreshness, IntegrationRuntimeBridge,
    IntegrationRuntimeError, ObservedTaskOutcome, ObservedTaskResult, RemoteExecutionStatus,
};
pub use mechanism_profile::SupportedMechanismProfile;
pub use mission_contract::decode_mission_plan;

/// Mission execution lifecycle owned by orchestration rather than Runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionExecutionLifecycle {
    /// The complete plan is accepted and its Group has been created.
    Accepted,
    /// At least one Task is Ready, Active, Blocked, or completed while later Tasks remain.
    Running,
    /// Every Task in the accepted plan completed and the Group was released.
    Completed,
    /// Mission policy declared a final failure and released the Group.
    Failed,
    /// An explicit cancellation terminated the Mission and released the Group.
    Cancelled,
}

/// One accepted MissionPlan and its single default Phase 1 Execution Group.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MissionExecution {
    /// Complete immutable plan used for DAG and completion decisions.
    plan: MissionPlan,
    /// Mission-level runtime coordination context.
    group_id: ExecutionGroupId,
    /// Current orchestration-owned lifecycle.
    lifecycle: MissionExecutionLifecycle,
}

impl MissionExecution {
    /// Returns the complete accepted MissionPlan.
    pub const fn plan(&self) -> &MissionPlan {
        &self.plan
    }

    /// Returns the Mission-level Execution Group identity.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the orchestration-owned Mission lifecycle.
    pub const fn lifecycle(&self) -> MissionExecutionLifecycle {
        self.lifecycle
    }
}

/// Errors raised when Mission orchestration invariants are violated.
#[derive(Debug)]
pub enum OrchestrationError {
    /// Control rejected a lifecycle or ownership transition.
    Control(ControlError),
    /// A Mission identity was absent or reused.
    Mission(String),
}

impl Display for OrchestrationError {
    /// Formats a stable orchestration diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Control(error) => write!(formatter, "control rejected orchestration: {error}"),
            Self::Mission(reason) => formatter.write_str(reason),
        }
    }
}

impl std::error::Error for OrchestrationError {}

impl From<ControlError> for OrchestrationError {
    /// Preserves Control diagnostics across the orchestration boundary.
    fn from(value: ControlError) -> Self {
        Self::Control(value)
    }
}

/// Deterministic Phase 1 Mission execution authority.
#[derive(Debug, Default, Clone)]
pub struct MissionOrchestrator {
    /// Accepted Missions keyed independently from Task-local identity.
    executions: BTreeMap<MissionId, MissionExecution>,
}

impl MissionOrchestrator {
    /// Creates an empty Mission execution authority.
    pub const fn new() -> Self {
        Self {
            executions: BTreeMap::new(),
        }
    }

    /// Serializes accepted Mission plans and orchestration lifecycle for process recovery.
    pub fn checkpoint_json(&self) -> Result<String, OrchestrationError> {
        let executions = self
            .executions
            .values()
            .map(|execution| {
                serde_json::json!({
                    "plan": mission_plan_json(execution.plan()),
                    "group_id": execution.group_id().as_str(),
                    "lifecycle": execution.lifecycle(),
                })
            })
            .collect::<Vec<_>>();
        serde_json::to_string(&executions).map_err(|error| {
            OrchestrationError::Mission(format!(
                "cannot serialize orchestration checkpoint: {error}"
            ))
        })
    }

    /// Restores accepted Mission plans and rejects malformed orchestration evidence.
    pub fn restore_json(json: &str) -> Result<Self, OrchestrationError> {
        let executions: Vec<serde_json::Value> = serde_json::from_str(json).map_err(|error| {
            OrchestrationError::Mission(format!("cannot restore orchestration checkpoint: {error}"))
        })?;
        let mut restored = Self::new();
        for value in executions {
            let plan_value = value.get("plan").ok_or_else(|| {
                OrchestrationError::Mission("checkpoint misses MissionPlan".to_string())
            })?;
            let plan =
                decode_mission_plan(&serde_json::to_string(plan_value).map_err(|error| {
                    OrchestrationError::Mission(format!("invalid MissionPlan checkpoint: {error}"))
                })?)?;
            SupportedMechanismProfile::current().validate(&plan)?;
            let group_id = ExecutionGroupId::new(
                value
                    .get("group_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        OrchestrationError::Mission("checkpoint misses Group identity".to_string())
                    })?,
            )
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
            let lifecycle: MissionExecutionLifecycle =
                serde_json::from_value(value.get("lifecycle").cloned().ok_or_else(|| {
                    OrchestrationError::Mission("checkpoint misses Mission lifecycle".to_string())
                })?)
                .map_err(|error| {
                    OrchestrationError::Mission(format!("invalid Mission lifecycle: {error}"))
                })?;
            let execution = MissionExecution {
                plan,
                group_id,
                lifecycle,
            };
            let mission_id = execution.plan.goal().mission_id().clone();
            if restored.executions.insert(mission_id, execution).is_some() {
                return Err(OrchestrationError::Mission(
                    "orchestration checkpoint contains duplicate Mission".to_string(),
                ));
            }
        }
        Ok(restored)
    }

    /// Accepts a complete plan, creates its one default Group, and exposes initial Ready Tasks.
    pub fn submit<E: EventSink>(
        &mut self,
        plan: MissionPlan,
        group_id: ExecutionGroupId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<&MissionExecution, OrchestrationError> {
        SupportedMechanismProfile::current().validate(&plan)?;
        let mission_id = plan.goal().mission_id().clone();
        if self.executions.contains_key(&mission_id) {
            let existing = self
                .executions
                .get(&mission_id)
                .expect("contains_key proves existing Mission authority");
            if existing.plan() == &plan && existing.group_id() == &group_id {
                return Ok(existing);
            }
            return Err(OrchestrationError::Mission(format!(
                "Mission {mission_id} already exists with a different plan or Group"
            )));
        }
        control.create_mission_group(group_id.clone(), &plan, timestamp, correlation_id, events)?;
        self.executions.insert(
            mission_id.clone(),
            MissionExecution {
                plan,
                group_id,
                lifecycle: MissionExecutionLifecycle::Accepted,
            },
        );
        self.refresh_ready(&mission_id, control, timestamp, correlation_id, events)?;
        self.executions
            .get(&mission_id)
            .ok_or_else(|| OrchestrationError::Mission("accepted Mission disappeared".to_string()))
    }

    /// Returns one accepted Mission execution.
    pub fn execution(&self, mission_id: &MissionId) -> Option<&MissionExecution> {
        self.executions.get(mission_id)
    }

    /// Returns all accepted Mission identities in deterministic order.
    pub fn mission_ids(&self) -> Vec<MissionId> {
        self.executions.keys().cloned().collect()
    }

    /// Validates restored Mission authority against Control before execution traffic is accepted.
    pub fn validate_control_authority(
        &self,
        control: &ControlPlane,
    ) -> Result<(), OrchestrationError> {
        let orchestration_groups = self
            .executions
            .values()
            .map(MissionExecution::group_id)
            .collect::<BTreeSet<_>>();
        for (mission_id, execution) in &self.executions {
            control.validate_mission_group_plan(execution.group_id(), execution.plan())?;
            let group = control
                .group(execution.group_id())
                .expect("Control plan validation requires the Group");
            let lifecycle_is_aligned = match execution.lifecycle() {
                MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running => {
                    matches!(
                        group.lifecycle(),
                        GroupLifecycle::Bound
                            | GroupLifecycle::Active
                            | GroupLifecycle::Adapted
                            | GroupLifecycle::Blocked
                    )
                }
                MissionExecutionLifecycle::Completed => {
                    group.lifecycle() == GroupLifecycle::Released
                        && group
                            .task_executions()
                            .all(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
                }
                MissionExecutionLifecycle::Failed | MissionExecutionLifecycle::Cancelled => {
                    group.lifecycle() == GroupLifecycle::Released
                }
            };
            if !lifecycle_is_aligned {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission {mission_id} lifecycle disagrees with its Execution Group"
                )));
            }
        }
        for group_id in control.group_ids() {
            let group = control
                .group(&group_id)
                .expect("Control returned its own Group identity");
            if group.task_executions().next().is_some() && !orchestration_groups.contains(&group_id)
            {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission-level Group {group_id} has no orchestration authority"
                )));
            }
        }
        Ok(())
    }

    /// Returns all unbound Ready Task identities in deterministic plan order.
    pub fn ready_tasks(&self, mission_id: &MissionId, control: &ControlPlane) -> Vec<TaskRef> {
        let Some(execution) = self.executions.get(mission_id) else {
            return Vec::new();
        };
        let Some(group) = control.group(execution.group_id()) else {
            return Vec::new();
        };
        self.dispatchable_tasks(mission_id, control)
            .into_iter()
            .filter(|task_ref| {
                group
                    .task_execution(task_ref)
                    .is_some_and(|task| task.assignments().is_empty())
            })
            .collect()
    }

    /// Returns Ready Tasks including committed bindings waiting for Runtime coordination.
    pub fn dispatchable_tasks(
        &self,
        mission_id: &MissionId,
        control: &ControlPlane,
    ) -> Vec<TaskRef> {
        let Some(execution) = self.executions.get(mission_id) else {
            return Vec::new();
        };
        let Some(group) = control.group(execution.group_id()) else {
            return Vec::new();
        };
        execution
            .plan()
            .task_graph()
            .tasks()
            .iter()
            .filter_map(|task| {
                let task_ref = task.requirement().task_ref();
                group
                    .task_execution(task_ref)
                    .filter(|task_execution| {
                        task_execution.lifecycle() == TaskExecutionLifecycle::Ready
                    })
                    .map(|_| task_ref.clone())
            })
            .collect()
    }

    /// Drives one unbound Ready Task through Match, Schedule, Propose, Commit, and Bind.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_task<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        state: &S,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<domain::TaskExecution, OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        let planned = execution
            .plan()
            .task_graph()
            .tasks()
            .iter()
            .find(|task| task.requirement().task_ref() == task_ref)
            .ok_or_else(|| {
                OrchestrationError::Mission(
                    "Task is absent from the accepted MissionPlan".to_string(),
                )
            })?;
        let requirement = planned.requirement();
        let group_id = execution.group_id().clone();
        let candidates = control.match_capabilities_for_mission(
            state,
            execution.plan(),
            requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        let scheduler = DeterministicBootstrapScheduler::new();
        let decision = scheduler
            .schedule_task(
                state,
                requirement,
                &candidates,
                timestamp,
                correlation_id,
                events,
            )
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
        let proposal = control.propose(
            state,
            requirement,
            &candidates,
            decision.proposed_assignments(),
            timestamp,
            correlation_id,
            events,
        )?;
        let committed =
            control.commit_for_group(&group_id, &proposal, timestamp, correlation_id, events)?;
        control.bind_task_execution_with_requirement(
            &group_id,
            &committed,
            requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        control
            .group(&group_id)
            .and_then(|group| group.task_execution(task_ref))
            .cloned()
            .ok_or_else(|| OrchestrationError::Mission("TaskExecution disappeared".to_string()))
    }

    /// Applies a successful Runtime Task outcome and explicitly evaluates the complete DAG.
    pub fn task_succeeded<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let group_id = self.group_for_task(mission_id, task_ref)?.clone();
        let task_resources = control
            .group(&group_id)
            .and_then(|group| group.task_execution(task_ref))
            .ok_or_else(|| OrchestrationError::Mission("TaskExecution is absent".to_string()))?
            .assignments()
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .filter(|resource_id| {
                control
                    .group(&group_id)
                    .and_then(|group| group.task_execution(task_ref))
                    .is_some_and(|task| {
                        task.binding_scope(resource_id) == ResourceBindingScope::Task
                    })
            })
            .cloned()
            .collect::<Vec<ResourceId>>();
        control.complete_task_execution(&group_id, task_ref, timestamp, correlation_id, events)?;
        control.release_task_bindings(
            &group_id,
            task_ref,
            &task_resources,
            timestamp,
            correlation_id,
            events,
        )?;
        self.refresh_ready(mission_id, control, timestamp, correlation_id, events)?;
        if self.plan_is_complete(mission_id, control)? {
            self.complete_mission(mission_id, control, timestamp, correlation_id, events)?;
        }
        Ok(())
    }

    /// Applies a final Task failure selected by Mission policy and releases the Group.
    #[allow(clippy::too_many_arguments)]
    pub fn task_failed<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        reason: impl Into<String>,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let group_id = self.group_for_task(mission_id, task_ref)?.clone();
        control.fail_task_execution(&group_id, task_ref, timestamp, correlation_id, events)?;
        if control.group(&group_id).is_some_and(|group| {
            matches!(
                group.lifecycle(),
                GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
            )
        }) {
            control.block_group(&group_id, reason.into(), timestamp, correlation_id, events)?;
        }
        control.fail_group(
            &group_id,
            "Mission execution policy declared final failure",
            timestamp,
            correlation_id,
            events,
        )?;
        control.release_group(&group_id, timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Failed;
        Ok(())
    }

    /// Applies an explicit Mission cancellation and releases its long-lived Group.
    pub fn cancel<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if matches!(
            execution.lifecycle(),
            MissionExecutionLifecycle::Completed
                | MissionExecutionLifecycle::Failed
                | MissionExecutionLifecycle::Cancelled
        ) {
            return Err(OrchestrationError::Mission(
                "Mission is already terminal".to_string(),
            ));
        }
        let group_id = execution.group_id().clone();
        let group_is_blocked = control
            .group(&group_id)
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?
            .lifecycle()
            == GroupLifecycle::Blocked;
        if !group_is_blocked {
            control.block_group(
                &group_id,
                "Mission cancellation requested",
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.fail_group(
            &group_id,
            "Mission cancelled",
            timestamp,
            correlation_id,
            events,
        )?;
        for context in execution.plan().contexts() {
            control.release_context_bindings(
                &group_id,
                context.context_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.release_group(&group_id, timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Cancelled;
        Ok(())
    }

    /// Marks newly dependency-satisfied Tasks Ready using the complete immutable plan.
    fn refresh_ready<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        let group_id = execution.group_id().clone();
        let completed = control
            .group(&group_id)
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect::<BTreeSet<TaskId>>();
        let ready = execution
            .plan()
            .task_graph()
            .ready_tasks(&completed)
            .into_iter()
            .map(|task| task.requirement().task_ref().clone())
            .collect::<Vec<_>>();
        for task_ref in ready {
            let is_pending = control
                .group(&group_id)
                .and_then(|group| group.task_execution(&task_ref))
                .is_some_and(|task| task.lifecycle() == TaskExecutionLifecycle::Pending);
            if is_pending {
                control.ready_task_execution(
                    &group_id,
                    &task_ref,
                    timestamp,
                    correlation_id,
                    events,
                )?;
            }
        }
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Running;
        Ok(())
    }

    /// Returns the Group only when the Task belongs to the accepted complete plan.
    fn group_for_task(
        &self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
    ) -> Result<&ExecutionGroupId, OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if task_ref.mission_id() != mission_id
            || !execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .any(|task| task.requirement().task_ref() == task_ref)
        {
            return Err(OrchestrationError::Mission(
                "Task is absent from the accepted MissionPlan".to_string(),
            ));
        }
        Ok(execution.group_id())
    }

    /// Evaluates Mission completion against every Task in the accepted plan.
    fn plan_is_complete(
        &self,
        mission_id: &MissionId,
        control: &ControlPlane,
    ) -> Result<bool, OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        let group = control
            .group(execution.group_id())
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?;
        Ok(execution.plan().task_graph().tasks().iter().all(|planned| {
            group
                .task_execution(planned.requirement().task_ref())
                .is_some_and(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
        }))
    }

    /// Ends Context bindings and releases the Group after explicit full-plan completion.
    fn complete_mission<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?
            .clone();
        for context in execution.plan().contexts() {
            control.release_context_bindings(
                execution.group_id(),
                context.context_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.complete_group(execution.group_id(), timestamp, correlation_id, events)?;
        control.release_group(execution.group_id(), timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Completed;
        Ok(())
    }
}

/// Serializes a validated domain MissionPlan into the v0.4 wire shape without typed JSON map keys.
fn mission_plan_json(plan: &MissionPlan) -> serde_json::Value {
    let contexts = plan
        .contexts()
        .iter()
        .map(|context| {
            let mut value = serde_json::json!({
                "id": context.context_id().as_str(),
                "roles": context.roles().iter().map(|role| serde_json::json!({
                    "id": role.context_role_id().as_str(),
                    "actor": role.actor_id().as_str(),
                })).collect::<Vec<_>>(),
                "coupling_mode": coupling_mode_name(context.coupling_mode()),
                "relations": context.relations().iter().map(relation_json).collect::<Vec<_>>(),
            });
            let object = value
                .as_object_mut()
                .expect("coordination Context JSON is an object");
            if let Some(view) = context.shared_view() {
                object.insert("shared_view".to_string(), shared_view_json(view));
            }
            if let Some(channel) = context.peer_channel() {
                object.insert("peer_channel".to_string(), peer_channel_json(channel));
            }
            value
        })
        .collect::<Vec<_>>();
    let tasks = plan
        .task_graph()
        .tasks()
        .iter()
        .map(|task| {
            let roles = task
                .requirement()
                .roles()
                .iter()
                .map(|role| {
                    let intent = task
                        .execution_intent(role.role_id())
                        .expect("validated MissionPlan role intent");
                    let contract = role
                        .required_contract()
                        .unwrap_or_else(|| intent.capability_contract());
                    let scope = match task.continuity().resource_scope(role.role_id()) {
                        domain::ResourceBindingScope::Task => "task",
                        domain::ResourceBindingScope::Context => "context",
                    };
                    serde_json::json!({
                        "id": role.role_id().as_str(),
                        "actor": role.actor_id().map(|actor| actor.as_str()).unwrap_or("anonymous"),
                        "capability": format!("{:?}", role.capability()).to_lowercase(),
                        "contract": contract_json(contract),
                        "resource_kind": role.resource_kind().map(|kind| format!("{:?}", kind).to_lowercase()),
                        "context_role": task.continuity().context_role(role.role_id()).map(|id| id.as_str()),
                        "resource_scope": scope,
                        "execution": {
                            "capability_contract": contract_json(intent.capability_contract()),
                            "parameters": intent.parameters().iter().map(|(key, value)| (key.clone(), execution_value_json(value))).collect::<serde_json::Map<_,_>>(),
                        }
                    })
                })
                .collect::<Vec<_>>();
            let mut value = serde_json::json!({
                "id": task.task_id().as_str(),
                "description": task.description(),
                "context_id": task.continuity().context_id().as_str(),
                "depends_on": task.dependencies().iter().map(|id| id.as_str()).collect::<Vec<_>>(),
                "roles": roles,
            });
            if let Some(mode) = task.continuity().coupling_mode_override() {
                value
                    .as_object_mut()
                    .expect("Task JSON is an object")
                    .insert(
                        "coupling_mode".to_string(),
                        serde_json::json!(coupling_mode_name(mode)),
                    );
            }
            value
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema_version": domain::MISSION_PLAN_SCHEMA_V0_4,
        "mission": {"id": plan.goal().mission_id().as_str(), "objective": plan.goal().objective()},
        "contexts": contexts,
        "tasks": tasks,
    })
}

/// Serializes one typed relation while retaining its closed family and reserved fields.
fn relation_json(relation: &domain::ExecutionRelationSpec) -> serde_json::Value {
    let mut value = serde_json::json!({
        "id": relation.relation_id().as_str(),
        "kind": relation_kind_name(relation.kind()),
        "source": {"task_id": relation.source().task_id().as_str(), "role_id": relation.source().role_id().as_str()},
        "target": {"task_id": relation.target().task_id().as_str(), "role_id": relation.target().role_id().as_str()},
    });
    let object = value.as_object_mut().expect("relation JSON is an object");
    match relation.relation_type() {
        domain::ExecutionRelationType::GroupMemberState { state_key }
        | domain::ExecutionRelationType::StateRequirement { state_key, .. }
        | domain::ExecutionRelationType::FreshnessRequirement { state_key, .. } => {
            object.insert("state_key".to_string(), serde_json::json!(state_key));
        }
        domain::ExecutionRelationType::SharedSpatialReference { reference } => {
            object.insert("reference".to_string(), spatial_reference_json(reference));
        }
        domain::ExecutionRelationType::RelativePose { frame_id }
        | domain::ExecutionRelationType::RelativeDistance { frame_id } => {
            object.insert("frame_id".to_string(), serde_json::json!(frame_id));
        }
        domain::ExecutionRelationType::RequiresActive => {}
    }
    if let domain::ExecutionRelationType::StateRequirement { requirement, .. } =
        relation.relation_type()
    {
        object.insert(
            "requirement".to_string(),
            serde_json::json!(match requirement {
                domain::RelationStateRequirement::Available => "available",
                domain::RelationStateRequirement::Unavailable => "unavailable",
            }),
        );
    }
    if let domain::ExecutionRelationType::FreshnessRequirement { policy, .. } =
        relation.relation_type()
    {
        object.insert("policy_id".to_string(), serde_json::json!(policy.policy_id));
    }
    value
}

/// Serializes one Context coupling mode with its stable wire spelling.
fn coupling_mode_name(mode: domain::ExecutionCouplingMode) -> &'static str {
    match mode {
        domain::ExecutionCouplingMode::Independent => "independent",
        domain::ExecutionCouplingMode::SequentialHandoff => "sequential-handoff",
        domain::ExecutionCouplingMode::ConcurrentCooperation => "concurrent-cooperation",
        domain::ExecutionCouplingMode::TightlyCoupledCooperation => "tightly-coupled-cooperation",
    }
}

/// Serializes a selective Group shared view declaration.
fn shared_view_json(view: &domain::GroupSharedViewSpec) -> serde_json::Value {
    let mut value = serde_json::json!({
        "bindings": view.bindings().iter().map(group_view_binding_json).collect::<Vec<_>>(),
        "include_freshness": view.include_freshness(),
    });
    if let Some(reference) = view.spatial_reference() {
        value
            .as_object_mut()
            .expect("Group shared view JSON is an object")
            .insert(
                "spatial_reference".to_string(),
                spatial_reference_json(reference),
            );
    }
    value
}

/// Serializes one State-backed or Runtime-backed Group view binding.
fn group_view_binding_json(binding: &domain::GroupViewBinding) -> serde_json::Value {
    let mut value = serde_json::json!({
        "context_role_id": binding.context_role_id().as_str(),
        "field": group_view_field_name(binding.field()),
    });
    let object = value
        .as_object_mut()
        .expect("Group view binding JSON is an object");
    if let Some(state_export_id) = binding.state_export_id() {
        object.insert(
            "state_export_id".to_string(),
            serde_json::json!(state_export_id),
        );
    }
    if let Some(payload_schema) = binding.payload_schema() {
        object.insert(
            "payload_schema".to_string(),
            serde_json::json!(payload_schema),
        );
    }
    value
}

/// Returns the stable wire spelling for a Group view field.
fn group_view_field_name(field: domain::GroupViewField) -> &'static str {
    match field {
        domain::GroupViewField::Pose => "pose",
        domain::GroupViewField::Velocity => "velocity",
        domain::GroupViewField::Execution => "execution",
    }
}

/// Serializes a shared map/frame reference.
fn spatial_reference_json(reference: &domain::SharedSpatialReference) -> serde_json::Value {
    serde_json::json!({
        "map_id": reference.selector().map_id().as_str(),
        "revision_id": reference.selector().revision_id().as_str(),
        "frame_id": reference.frame_id(),
    })
}

/// Serializes a transport-neutral peer channel descriptor.
fn peer_channel_json(channel: &domain::PeerChannelSpec) -> serde_json::Value {
    serde_json::json!({"profile_id": channel.profile_id, "message_schema": channel.message_schema})
}

/// Returns the stable wire spelling for one relation family.
fn relation_kind_name(kind: domain::ExecutionRelationKind) -> &'static str {
    match kind {
        domain::ExecutionRelationKind::RequiresActive => "requires-active",
        domain::ExecutionRelationKind::GroupMemberState => "group-member-state",
        domain::ExecutionRelationKind::SharedSpatialReference => "shared-spatial-reference",
        domain::ExecutionRelationKind::RelativePose => "relative-pose",
        domain::ExecutionRelationKind::RelativeDistance => "relative-distance",
        domain::ExecutionRelationKind::StateRequirement => "state-requirement",
        domain::ExecutionRelationKind::FreshnessRequirement => "freshness-requirement",
    }
}

/// Serializes one canonical capability contract into contract JSON.
fn contract_json(contract: &domain::CapabilityContractRef) -> serde_json::Value {
    serde_json::json!({"namespace": contract.namespace(), "name": contract.name(), "version": contract.version()})
}

/// Serializes one scalar execution value without introducing adapter-specific types.
fn execution_value_json(value: &domain::ExecutionValue) -> serde_json::Value {
    match value {
        domain::ExecutionValue::Bool(value) => serde_json::Value::Bool(*value),
        domain::ExecutionValue::Integer(value) => serde_json::json!(value),
        domain::ExecutionValue::Float(value) => serde_json::json!(value),
        domain::ExecutionValue::String(value) => serde_json::Value::String(value.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityContractRef, CapabilityKind, CoordinationContextId, LocalRuntime,
        LocalSystemDescriptor, LocalSystemId, NodeContractVersion, NodeHealth, NodeId,
        NodeRegistration, NodeStatus, Resource, ResourceId, ResourceKind,
    };
    use state::InMemorySharedNodeState;
    use testkit::InMemoryEventLog;

    /// Legacy MissionPlan v0.3 decodes one Node-independent relation and normalizes to v0.4.
    #[test]
    fn execution_relation_fixture_decodes_logical_endpoints() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let plan = decode_mission_plan(source).expect("relation fixture should validate");
        let relation = &plan.contexts()[0].relations()[0];
        assert_eq!(relation.relation_id().as_str(), "safety-guards-navigation");
        assert_eq!(relation.source().task_id().as_str(), "observe-safety");
        assert_eq!(relation.target().role_id().as_str(), "navigator");
        assert_eq!(
            relation.kind(),
            domain::ExecutionRelationKind::RequiresActive
        );
    }

    /// Relation endpoints must exist in one Context and remain concurrently runnable in the DAG.
    #[test]
    fn execution_relation_rejects_unknown_or_dag_ordered_endpoints() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut unknown: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
        unknown["contexts"][0]["relations"][0]["source"]["role_id"] =
            serde_json::json!("missing-role");
        assert!(
            decode_mission_plan(&unknown.to_string())
                .expect_err("unknown relation role must fail")
                .to_string()
                .contains("unknown Role")
        );

        let mut ordered: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
        ordered["tasks"][1]["depends_on"] = serde_json::json!(["observe-safety"]);
        assert!(
            decode_mission_plan(&ordered.to_string())
                .expect_err("DAG-ordered relation must fail")
                .to_string()
                .contains("ordered by the DAG")
        );
    }

    /// v0.4 preserves coupling declarations and typed relation metadata at the JSON boundary.
    #[test]
    fn v0_4_decodes_coupling_and_typed_relation() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "bindings": [
                {
                    "context_role_id": "safety",
                    "field": "pose",
                    "state_export_id": "safety-pose",
                    "payload_schema": "roboguide.pose/v1"
                },
                {"context_role_id": "guide", "field": "execution"}
            ],
            "include_freshness": true,
            "spatial_reference": {"map_id": "campus", "revision_id": "r1", "frame_id": "map"}
        });
        document["contexts"][0]["relations"][0] = serde_json::json!({
            "id": "safety-guards-navigation",
            "kind": "state-requirement",
            "state_key": "hazard",
            "requirement": "available",
            "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
            "target": {"task_id": "navigate", "role_id": "navigator"}
        });
        let plan = decode_mission_plan(&document.to_string()).expect("v0.4 plan should validate");
        assert_eq!(
            plan.contexts()[0].coupling_mode(),
            domain::ExecutionCouplingMode::ConcurrentCooperation
        );
        assert!(plan.contexts()[0].shared_view().is_some());
        assert!(matches!(
            plan.contexts()[0].relations()[0].relation_type(),
            domain::ExecutionRelationType::StateRequirement { .. }
        ));

        let encoded = mission_plan_json(&plan);
        let execution_binding = &encoded["contexts"][0]["shared_view"]["bindings"][1];
        assert!(execution_binding.get("state_export_id").is_none());
        assert!(execution_binding.get("payload_schema").is_none());
        let decoded = decode_mission_plan(&encoded.to_string())
            .expect("canonical v0.4 MissionPlan should round trip");
        assert_eq!(decoded, plan);
    }

    /// Implementation preflight rejects future typed syntax before Control creates a Group.
    #[test]
    fn unsupported_relation_never_reaches_control_authority() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "bindings": [{"context_role_id": "guide", "field": "execution"}],
            "include_freshness": false
        });
        document["contexts"][0]["relations"][0] = serde_json::json!({
            "id": "relative-guidance",
            "kind": "relative-pose",
            "frame_id": "map",
            "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
            "target": {"task_id": "navigate", "role_id": "navigator"}
        });
        let plan = decode_mission_plan(&document.to_string()).expect("contract syntax is valid");
        let mut orchestrator = MissionOrchestrator::new();
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        let result = orchestrator.submit(
            plan,
            ExecutionGroupId::new("group-unsupported").expect("group id is valid"),
            &mut control,
            TimestampMs::new(1),
            &CorrelationId::new("unsupported-profile").expect("correlation is valid"),
            &mut events,
        );

        assert!(matches!(
            result,
            Err(OrchestrationError::Mission(reason))
                if reason.contains("valid contract syntax but is not executable")
        ));
        assert!(control.group_ids().is_empty());
        assert!(events.records().is_empty());
    }

    /// v0.4 rejects a Task mode override whose Context lacks its static mechanisms.
    #[test]
    fn v0_4_rejects_unbacked_task_coupling_mode() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["tasks"][1]["coupling_mode"] = serde_json::json!("tightly-coupled-cooperation");

        assert!(
            decode_mission_plan(&document.to_string())
                .expect_err("unbacked Task mode must fail acceptance")
                .to_string()
                .contains("requires a Group shared view")
        );
    }

    /// The Phase 1 fixture decodes into a four-Task DAG and preserves Context continuity metadata.
    #[test]
    fn phase1_fixture_contains_complete_dag_and_context() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_4);
        assert_eq!(plan.contexts().len(), 1);
        assert_eq!(plan.task_graph().tasks().len(), 4);
        assert_eq!(
            plan.task_graph().tasks()[1]
                .continuity()
                .resource_scope(plan.task_graph().tasks()[1].requirement().roles()[0].role_id()),
            domain::ResourceBindingScope::Context
        );
    }

    /// An exact submission retry returns existing authority without creating a second Group.
    #[test]
    fn exact_mission_submission_retry_is_idempotent() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        let group_id = ExecutionGroupId::new("group-idempotent").expect("group id valid");
        let correlation = CorrelationId::new("idempotent-submit").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut orchestrator = MissionOrchestrator::new();
        let mut events = InMemoryEventLog::new();
        orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("first submission creates authority");
        let event_count = events.records().len();

        let repeated = orchestrator
            .submit(
                plan,
                group_id,
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("exact retry returns existing authority");

        assert_eq!(repeated.lifecycle(), MissionExecutionLifecycle::Running);
        assert_eq!(orchestrator.mission_ids().len(), 1);
        assert_eq!(events.records().len(), event_count);
    }

    /// Restored orchestration rejects missing Groups, truncated DAGs, and lifecycle disagreement.
    #[test]
    fn restored_orchestration_cross_checks_control_authority() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-restore-authority").expect("group id valid");
        let correlation = CorrelationId::new("restore-authority-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        control
            .create_mission_group(
                group_id.clone(),
                &plan,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("orphan fixture Group is created");
        assert!(
            MissionOrchestrator::new()
                .validate_control_authority(&control)
                .expect_err("orphan Mission Group must fail closed")
                .to_string()
                .contains("no orchestration authority")
        );
        let mut control = ControlPlane::new();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id,
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("Mission authority is created");
        orchestrator
            .validate_control_authority(&control)
            .expect("matching projections validate");

        let checkpoint = orchestrator
            .checkpoint_json()
            .expect("orchestration checkpoint serializes");
        let mut missing_group: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        missing_group[0]["group_id"] = serde_json::json!("group-other");
        let restored = MissionOrchestrator::restore_json(&missing_group.to_string())
            .expect("syntactically valid checkpoint restores before authority validation");
        assert!(matches!(
            restored.validate_control_authority(&control),
            Err(OrchestrationError::Control(ControlError::UnknownGroup(_)))
        ));

        let mut truncated_dag: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        truncated_dag[0]["plan"]["tasks"]
            .as_array_mut()
            .expect("tasks remain an array")
            .pop();
        let restored = MissionOrchestrator::restore_json(&truncated_dag.to_string())
            .expect("shorter valid DAG restores before authority validation");
        assert!(
            restored
                .validate_control_authority(&control)
                .expect_err("Control must reject a truncated restored DAG")
                .to_string()
                .contains("Task DAG differs")
        );

        let mut false_terminal: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        false_terminal[0]["lifecycle"] = serde_json::json!("Completed");
        let restored = MissionOrchestrator::restore_json(&false_terminal.to_string())
            .expect("known lifecycle restores before authority validation");
        assert!(
            restored
                .validate_control_authority(&control)
                .expect_err("unreleased Group must reject a completed Mission projection")
                .to_string()
                .contains(&mission_id.to_string())
        );
    }

    /// Both directions of the Spatial Memory experiment remain valid legacy plans.
    #[test]
    fn distributed_spatial_memory_fixtures_decode_in_both_directions() {
        let fixtures = [
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
            ),
        ];
        for fixture in fixtures {
            let plan =
                decode_mission_plan(fixture).expect("Spatial Memory fixture should validate");
            assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_4);
            assert_eq!(plan.contexts().len(), 1);
            assert_eq!(plan.task_graph().tasks().len(), 2);
        }
    }

    /// The four Spatial Memory fixtures bind to two distinct physical nodes under Control policy.
    #[test]
    fn distributed_spatial_memory_actor_placement_drives_two_node_assignments() {
        let fixtures = [
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
                ),
                "robot-dog-a",
                "dog-a",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
                ),
                "robot-dog-b",
                "dog-b",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
                ),
                "robot-dog-b",
                "dog-b",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
                ),
                "robot-dog-a",
                "dog-a",
            ),
        ];
        let contracts = [
            (
                CapabilityContractRef::new("spatial.map", "build", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.map", "publish", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.map", "import", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.localization", "verify", "v0")
                    .expect("contract valid"),
                CapabilityKind::Observation,
            ),
        ];
        for (fixture, actor, expected_node) in fixtures {
            let plan = decode_mission_plan(fixture).expect("Spatial Memory fixture validates");
            let mission_id = plan.goal().mission_id().clone();
            let expected_node = NodeId::new(expected_node).expect("node id valid");
            let timestamp = TimestampMs::new(1);
            let correlation =
                CorrelationId::new(format!("placement-{mission_id}")).expect("correlation valid");
            let mut control = ControlPlane::new();
            let mut state = InMemorySharedNodeState::new();
            let mut events = InMemoryEventLog::new();
            for (node_id, resource_id) in [("dog-a", "compute-a"), ("dog-b", "compute-b")] {
                control
                    .register_node(
                        &mut state,
                        registration(
                            node_id,
                            vec![
                                Capability::new(CapabilityKind::Compute, true),
                                Capability::new(CapabilityKind::Observation, true),
                            ],
                            contracts.to_vec(),
                            vec![(
                                ResourceId::new(resource_id).expect("resource id valid"),
                                ResourceKind::Compute,
                            )],
                        ),
                        NodeStatus::new(NodeHealth::Online, timestamp),
                        timestamp,
                        &correlation,
                        &mut events,
                    )
                    .expect("symmetric Spatial node registers");
            }
            control
                .set_actor_node_constraint(
                    mission_id.clone(),
                    domain::ActorId::new(actor).expect("actor id valid"),
                    expected_node.clone(),
                )
                .expect("fixture placement constraint accepted");
            let group_id =
                ExecutionGroupId::new(format!("group-{mission_id}")).expect("group id valid");
            let mut orchestrator = MissionOrchestrator::new();
            orchestrator
                .submit(
                    plan,
                    group_id,
                    &mut control,
                    timestamp,
                    &correlation,
                    &mut events,
                )
                .expect("fixture Mission accepted");
            let ready = orchestrator.ready_tasks(&mission_id, &control);
            assert_eq!(ready.len(), 1);
            let task = orchestrator
                .prepare_task(
                    &mission_id,
                    &ready[0],
                    &state,
                    &mut control,
                    TimestampMs::new(2),
                    &correlation,
                    &mut events,
                )
                .expect("first Spatial Task binds");
            assert_eq!(task.assignments().len(), 1);
            assert_eq!(task.assignments()[0].node_id(), &expected_node);
        }
    }

    /// Malformed or legacy MissionPlan documents are rejected before Control receives them.
    #[test]
    fn legacy_plan_schema_is_rejected() {
        let error = decode_mission_plan(
            r#"{"schema_version":"roboguide.mission-plan/v0.1","mission":{"id":"m","objective":"x"},"contexts":[],"tasks":[]}"#,
        )
        .expect_err("legacy plan must be rejected");
        assert!(error.to_string().contains("unsupported MissionPlan schema"));
    }

    /// MissionPlan v0.2 remains a relation-free compatibility input during v0.4 migration.
    #[test]
    fn v0_2_plan_decodes_without_execution_relations() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("v0.2 compatibility input should decode");
        assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_4);
        assert!(
            plan.contexts()
                .iter()
                .all(|context| context.relations().is_empty())
        );
    }

    /// Builds a registration with the exact contracts and resources used by the Phase 1 fixture.
    fn registration(
        node_id: &str,
        capabilities: Vec<Capability>,
        contracts: Vec<(CapabilityContractRef, CapabilityKind)>,
        resources: Vec<(ResourceId, ResourceKind)>,
    ) -> NodeRegistration {
        let local_system_id = LocalSystemId::new("test-system").expect("system id valid");
        let capability_owners = contracts
            .iter()
            .map(|(contract, _)| (contract.clone(), local_system_id.clone()))
            .collect();
        let capability_kinds = contracts.iter().cloned().collect();
        let capability_readiness = contracts
            .iter()
            .map(|(contract, _)| (contract.clone(), true))
            .collect();
        let resources = resources
            .into_iter()
            .map(|(resource_id, kind)| Resource::new(resource_id, kind, 1).expect("resource valid"))
            .collect::<Vec<_>>();
        let resource_owners = resources
            .iter()
            .map(|resource| (resource.id().clone(), local_system_id.clone()))
            .collect();
        NodeRegistration::new_with_local_systems_and_readiness(
            NodeId::new(node_id).expect("node id valid"),
            vec![LocalSystemDescriptor::new(
                local_system_id,
                LocalRuntime::new("phase1-test", "0.1.0").expect("runtime valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::v0_1(),
            capabilities,
            capability_owners,
            capability_kinds,
            capability_readiness,
            Vec::new(),
            resources,
            resource_owners,
        )
        .expect("exact test registration is valid")
    }

    /// Verifies the complete Phase 1 DAG, Context continuity, and explicit Group completion.
    #[test]
    fn phase1_execution_reuses_context_binding_until_mission_completion() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("fixture should decode");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-phase1-test").expect("group id valid");
        let timestamp = TimestampMs::new(1);
        let correlation = CorrelationId::new("phase1-orchestration-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = InMemoryEventLog::new();
        let compute_prepare =
            CapabilityContractRef::new("compute", "prepare", "v1").expect("contract valid");
        let compute_verify =
            CapabilityContractRef::new("observation", "verify", "v1").expect("contract valid");
        let move_contract =
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
        for node in [
            registration(
                "edge",
                vec![
                    Capability::new(CapabilityKind::Compute, true),
                    Capability::new(CapabilityKind::Observation, true),
                ],
                vec![
                    (compute_prepare.clone(), CapabilityKind::Compute),
                    (compute_verify.clone(), CapabilityKind::Observation),
                ],
                vec![(
                    ResourceId::new("edge-compute").expect("resource id valid"),
                    ResourceKind::Compute,
                )],
            ),
            registration(
                "carrier",
                vec![Capability::new(CapabilityKind::Transport, true)],
                vec![(move_contract.clone(), CapabilityKind::Transport)],
                vec![(
                    ResourceId::new("carrier-space").expect("resource id valid"),
                    ResourceKind::Space,
                )],
            ),
        ] {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation,
                    &mut events,
                )
                .expect("test node registers");
        }
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut control,
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("Mission should be accepted");
        assert_eq!(orchestrator.ready_tasks(&mission_id, &control).len(), 1);
        let task = |index: usize| {
            plan.task_graph().tasks()[index]
                .requirement()
                .task_ref()
                .clone()
        };
        for index in [0_usize, 1, 2, 3] {
            let task_ref = task(index);
            orchestrator
                .prepare_task(
                    &mission_id,
                    &task_ref,
                    &state,
                    &mut control,
                    TimestampMs::new(2 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("ready Task should bind");
            control
                .activate_task_execution(
                    &group_id,
                    &task_ref,
                    TimestampMs::new(3 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("test Runtime transition should activate the bound Task");
            orchestrator
                .task_succeeded(
                    &mission_id,
                    &task_ref,
                    &mut control,
                    TimestampMs::new(10 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("Task outcome should advance the DAG");
            if index < 2 {
                assert_eq!(
                    orchestrator
                        .execution(&mission_id)
                        .expect("Mission exists")
                        .lifecycle(),
                    MissionExecutionLifecycle::Running
                );
            }
            if index == 1 {
                assert!(
                    control
                        .group(&group_id)
                        .expect("Group retained between Tasks")
                        .context_binding(
                            &CoordinationContextId::new("delivery-context")
                                .expect("context id valid"),
                            &domain::ContextRoleId::new("carrier").expect("role id valid"),
                        )
                        .is_some()
                );
            }
        }
        assert_eq!(
            orchestrator
                .execution(&mission_id)
                .expect("Mission exists")
                .lifecycle(),
            MissionExecutionLifecycle::Completed
        );
        assert_eq!(
            control
                .group(&group_id)
                .expect("released Group retained as history")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(
            control
                .allocation_snapshot(TimestampMs::new(20))
                .expect("projection valid")
                .allocations()
                .is_empty()
        );
    }

    /// Cancellation remains valid after Control has already blocked the Mission Group.
    #[test]
    fn blocked_mission_can_be_cancelled_and_released() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("fixture should decode");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-cancel-blocked").expect("group id valid");
        let correlation = CorrelationId::new("cancel-blocked-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("Mission should be accepted");
        control
            .block_group(
                &group_id,
                "node recovery required",
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("Group should become blocked");
        orchestrator
            .cancel(
                &mission_id,
                &mut control,
                TimestampMs::new(3),
                &correlation,
                &mut events,
            )
            .expect("Blocked Mission should cancel");
        assert_eq!(
            orchestrator
                .execution(&mission_id)
                .expect("Mission retained")
                .lifecycle(),
            MissionExecutionLifecycle::Cancelled
        );
        assert_eq!(
            control
                .group(&group_id)
                .expect("Group retained")
                .lifecycle(),
            GroupLifecycle::Released
        );
    }
}
