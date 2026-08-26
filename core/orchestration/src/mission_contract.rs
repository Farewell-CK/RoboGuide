//! MissionPlan v0.2 JSON boundary owned by Mission orchestration.

use crate::OrchestrationError;
use domain::{
    ActorId, CapabilityContractRef, CapabilityKind, ContextRole, ContextRoleId,
    CoordinationContext, CoordinationContextId, ExecutionIntent, ExecutionValue,
    MISSION_PLAN_SCHEMA_V0_2, MissionGoal, MissionId, MissionPlan, PlannedTask,
    ResourceBindingScope, ResourceKind, RoleId, RoleRequirement, TaskContinuity, TaskGraph, TaskId,
    TaskRequirement,
};
use serde::Deserialize;
use std::collections::BTreeMap;

/// Wire MissionPlan accepted by the Phase 1 HTTP boundary.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlanDocument {
    /// Exact cross-language contract marker.
    schema_version: String,
    /// Mission identity and user-visible objective.
    mission: MissionDocument,
    /// Mission Intelligence semantic contexts.
    contexts: Vec<ContextDocument>,
    /// Complete Task DAG.
    tasks: Vec<TaskDocument>,
}

/// Wire Mission identity and objective.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MissionDocument {
    /// Stable Mission identity.
    id: String,
    /// User-visible outcome.
    objective: String,
}

/// Wire semantic context.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextDocument {
    /// Stable Context identity.
    id: String,
    /// Semantic actor roles.
    roles: Vec<ContextRoleDocument>,
}

/// Wire ContextRole-to-Actor declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContextRoleDocument {
    /// Stable ContextRole identity.
    id: String,
    /// Mission actor identity.
    actor: String,
}

/// Wire Task DAG node.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskDocument {
    /// Task identity inside the Mission.
    id: String,
    /// Human-readable outcome.
    description: String,
    /// Context containing this Task.
    context_id: String,
    /// Prerequisite Task identities.
    depends_on: Vec<String>,
    /// Role execution requirements.
    roles: Vec<RoleDocument>,
}

/// Wire Task role requirement and continuity declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RoleDocument {
    /// Task-local role identity.
    id: String,
    /// Mission actor identity.
    actor: String,
    /// Canonical coarse capability kind.
    capability: CapabilityDocument,
    /// Exact capability contract.
    contract: ContractDocument,
    /// Optional exclusive resource category.
    resource_kind: Option<ResourceDocument>,
    /// Canonical execution operation.
    execution: IntentDocument,
    /// Optional ContextRole identity.
    context_role: Option<String>,
    /// Resource lifetime for this role.
    resource_scope: ScopeDocument,
}

/// Wire exact capability contract reference.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ContractDocument {
    /// Contract namespace.
    namespace: String,
    /// Contract operation name.
    name: String,
    /// Contract version.
    version: String,
}

/// Wire canonical execution intent.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IntentDocument {
    /// Exact capability contract invoked by this intent.
    capability_contract: ContractDocument,
    /// Transport-neutral scalar parameters.
    parameters: BTreeMap<String, serde_json::Value>,
}

/// Supported capability kinds in MissionPlan v0.2.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum CapabilityDocument {
    /// Navigation or locomotion.
    Mobility,
    /// Payload transport.
    Transport,
    /// General computation.
    Compute,
    /// Sensing or verification.
    Observation,
}

/// Supported resource categories in MissionPlan v0.2.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum ResourceDocument {
    /// Exclusive physical space.
    Space,
    /// Exclusive compute capacity.
    Compute,
    /// Exclusive time interval.
    Time,
}

/// Supported role resource lifetimes.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum ScopeDocument {
    /// Release after Task terminal handling.
    Task,
    /// Retain until the containing Context ends.
    Context,
}

/// Decodes and validates one complete MissionPlan v0.2 JSON document.
pub fn decode_mission_plan(json: &str) -> Result<MissionPlan, OrchestrationError> {
    let document: PlanDocument = serde_json::from_str(json).map_err(|error| {
        OrchestrationError::Mission(format!("invalid MissionPlan JSON: {error}"))
    })?;
    if document.schema_version != MISSION_PLAN_SCHEMA_V0_2 {
        return Err(OrchestrationError::Mission(format!(
            "unsupported MissionPlan schema {}",
            document.schema_version
        )));
    }
    let mission_id = MissionId::new(document.mission.id)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let goal = MissionGoal::new(mission_id.clone(), document.mission.objective)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let contexts = document
        .contexts
        .into_iter()
        .map(context_from_document)
        .collect::<Result<Vec<_>, _>>()?;
    let tasks = document
        .tasks
        .into_iter()
        .map(|task| task_from_document(&mission_id, task))
        .collect::<Result<Vec<_>, _>>()?;
    let graph = TaskGraph::new(mission_id, tasks)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    MissionPlan::new(goal, graph, contexts)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one wire Context into validated Mission Intelligence domain values.
fn context_from_document(
    context: ContextDocument,
) -> Result<CoordinationContext, OrchestrationError> {
    let context_id = CoordinationContextId::new(context.id)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let roles = context
        .roles
        .into_iter()
        .map(|role| {
            Ok(ContextRole::new(
                ContextRoleId::new(role.id)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
                ActorId::new(role.actor)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
            ))
        })
        .collect::<Result<Vec<_>, OrchestrationError>>()?;
    CoordinationContext::new(context_id, roles)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one wire Task and its role declarations into validated domain values.
fn task_from_document(
    mission_id: &MissionId,
    task: TaskDocument,
) -> Result<PlannedTask, OrchestrationError> {
    let task_id =
        TaskId::new(task.id).map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let context_id = CoordinationContextId::new(task.context_id)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let mut roles = Vec::with_capacity(task.roles.len());
    let mut intents = BTreeMap::new();
    let mut context_roles = BTreeMap::new();
    let mut scopes = BTreeMap::new();
    for role in task.roles {
        let role_id =
            RoleId::new(role.id).map_err(|error| OrchestrationError::Mission(error.to_string()))?;
        let contract = contract_from_document(role.contract)?;
        let intent_contract = contract_from_document(role.execution.capability_contract)?;
        if contract != intent_contract {
            return Err(OrchestrationError::Mission(format!(
                "Task {task_id} role {role_id} contract differs from execution intent"
            )));
        }
        let parameters = role
            .execution
            .parameters
            .into_iter()
            .map(|(key, value)| Ok((key, execution_value(value)?)))
            .collect::<Result<BTreeMap<_, _>, OrchestrationError>>()?;
        intents.insert(
            role_id.clone(),
            ExecutionIntent::new(intent_contract, parameters)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
        );
        roles.push(RoleRequirement::new_with_actor_and_contract(
            role_id.clone(),
            ActorId::new(role.actor)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
            capability_from_document(role.capability),
            contract,
            role.resource_kind.map(resource_from_document),
        ));
        if let Some(context_role) = role.context_role {
            context_roles.insert(
                role_id.clone(),
                ContextRoleId::new(context_role)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
            );
        }
        scopes.insert(role_id, scope_from_document(role.resource_scope));
    }
    let requirement = TaskRequirement::new(mission_id.clone(), task_id, roles)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let dependencies = task
        .depends_on
        .into_iter()
        .map(|dependency| {
            TaskId::new(dependency).map_err(|error| OrchestrationError::Mission(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    PlannedTask::new(
        task.description,
        requirement,
        intents,
        dependencies,
        TaskContinuity::new(context_id, context_roles, scopes),
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts a wire contract value into its canonical domain reference.
fn contract_from_document(
    contract: ContractDocument,
) -> Result<CapabilityContractRef, OrchestrationError> {
    CapabilityContractRef::new(contract.namespace, contract.name, contract.version)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one JSON scalar into a canonical ExecutionValue.
fn execution_value(value: serde_json::Value) -> Result<ExecutionValue, OrchestrationError> {
    match value {
        serde_json::Value::Bool(value) => Ok(ExecutionValue::Bool(value)),
        serde_json::Value::Number(value) if value.is_i64() => Ok(ExecutionValue::Integer(
            value
                .as_i64()
                .expect("integer JSON number validated by is_i64"),
        )),
        serde_json::Value::Number(value) => value
            .as_f64()
            .filter(|value| value.is_finite())
            .map(ExecutionValue::Float)
            .ok_or_else(|| OrchestrationError::Mission("non-finite execution number".to_string())),
        serde_json::Value::String(value) => Ok(ExecutionValue::String(value)),
        _ => Err(OrchestrationError::Mission(
            "execution parameters must be scalar".to_string(),
        )),
    }
}

/// Maps the contract capability enumeration into Domain.
const fn capability_from_document(capability: CapabilityDocument) -> CapabilityKind {
    match capability {
        CapabilityDocument::Mobility => CapabilityKind::Mobility,
        CapabilityDocument::Transport => CapabilityKind::Transport,
        CapabilityDocument::Compute => CapabilityKind::Compute,
        CapabilityDocument::Observation => CapabilityKind::Observation,
    }
}

/// Maps the contract resource enumeration into Domain.
const fn resource_from_document(resource: ResourceDocument) -> ResourceKind {
    match resource {
        ResourceDocument::Space => ResourceKind::Space,
        ResourceDocument::Compute => ResourceKind::Compute,
        ResourceDocument::Time => ResourceKind::Time,
    }
}

/// Maps the contract lifetime enumeration into Domain.
const fn scope_from_document(scope: ScopeDocument) -> ResourceBindingScope {
    match scope {
        ScopeDocument::Task => ResourceBindingScope::Task,
        ScopeDocument::Context => ResourceBindingScope::Context,
    }
}
