//! MissionPlan v0.2/v0.3/v0.4 JSON boundary owned by Mission orchestration.

use crate::OrchestrationError;
use domain::{
    ActorId, CapabilityContractRef, CapabilityKind, ContextRole, ContextRoleId,
    CoordinationContext, CoordinationContextId, ExecutionCouplingMode, ExecutionIntent,
    ExecutionRelationId, ExecutionRelationSpec, ExecutionRelationType, ExecutionValue,
    FreshnessPolicyRef, GroupSharedViewSpec, GroupViewBinding, GroupViewField,
    MISSION_PLAN_SCHEMA_V0_2, MISSION_PLAN_SCHEMA_V0_3, MISSION_PLAN_SCHEMA_V0_4, MapId,
    MapRevisionId, MapRevisionSelector, MissionGoal, MissionId, MissionPlan, PeerChannelSpec,
    PlannedExecutionRef, PlannedTask, RelationStateRequirement, ResourceBindingScope, ResourceKind,
    RoleId, RoleRequirement, SharedSpatialReference, TaskContinuity, TaskGraph, TaskId,
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
    /// Execution-time constraints retained from MissionPlan v0.3.
    #[serde(default)]
    relations: Option<Vec<RelationDocument>>,
    /// Optional Context default coupling mode introduced by v0.4.
    #[serde(default)]
    coupling_mode: Option<CouplingModeDocument>,
    /// Optional selective Group shared view introduced by v0.4.
    #[serde(default)]
    shared_view: Option<SharedViewDocument>,
    /// Optional direct peer channel profile introduced by v0.4.
    #[serde(default)]
    peer_channel: Option<PeerChannelDocument>,
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

/// Wire execution-time coordination relation.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RelationDocument {
    /// Stable relation identity within the Mission.
    id: String,
    /// Closed relation behavior.
    kind: String,
    /// Logical condition-provider endpoint.
    source: RelationEndpointDocument,
    /// Logical constrained endpoint.
    target: RelationEndpointDocument,
    /// Optional typed state key.
    #[serde(default)]
    state_key: Option<String>,
    /// Optional typed spatial reference.
    #[serde(default)]
    reference: Option<SpatialReferenceDocument>,
    /// Optional typed coordinate frame.
    #[serde(default)]
    frame_id: Option<String>,
    /// Optional state requirement token.
    #[serde(default)]
    requirement: Option<RequirementDocument>,
    /// Optional provider-defined freshness policy identity.
    #[serde(default)]
    policy_id: Option<String>,
}

/// Wire coupling mode inherited by Task executions.
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
enum CouplingModeDocument {
    /// Independent execution.
    Independent,
    /// Sequential handoff execution.
    SequentialHandoff,
    /// Concurrent cooperative execution.
    ConcurrentCooperation,
    /// Tightly coupled cooperative execution.
    TightlyCoupledCooperation,
}

/// Wire selective Group shared view declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SharedViewDocument {
    /// Optional shared map/frame reference.
    #[serde(default)]
    spatial_reference: Option<SpatialReferenceDocument>,
    /// Selectively exposed member field/schema bindings.
    #[serde(default)]
    bindings: Vec<ViewBindingDocument>,
    /// Whether the view returns Fresh/Stale/Unknown metadata.
    #[serde(default)]
    include_freshness: bool,
}

/// Wire typed State binding for a Group member field.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ViewBindingDocument {
    /// Logical Context member owning the export.
    context_role_id: String,
    /// Closed semantic field.
    field: String,
    /// Exact node-wide State export identity.
    #[serde(default)]
    state_export_id: Option<String>,
    /// Exact State payload schema.
    #[serde(default)]
    payload_schema: Option<String>,
}

/// Wire map/frame identity.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SpatialReferenceDocument {
    /// Logical map identity.
    map_id: String,
    /// Immutable map revision identity.
    revision_id: String,
    /// Common coordinate frame.
    frame_id: String,
}

/// Wire direct peer channel profile.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PeerChannelDocument {
    /// Deployment-resolved channel profile.
    profile_id: String,
    /// Versioned peer message schema.
    message_schema: String,
}

/// Wire state requirement token.
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
enum RequirementDocument {
    /// State must be available.
    Available,
    /// State must be unavailable.
    Unavailable,
}

/// Wire logical Task/Role relation endpoint.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RelationEndpointDocument {
    /// Task containing the endpoint role.
    task_id: String,
    /// Role occupying the logical execution slot.
    role_id: String,
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
    /// Optional Task-level coupling mode override.
    #[serde(default)]
    coupling_mode: Option<CouplingModeDocument>,
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

/// Decodes v0.2/v0.3 compatibility input or one complete MissionPlan v0.4 document.
pub fn decode_mission_plan(json: &str) -> Result<MissionPlan, OrchestrationError> {
    let document: PlanDocument = serde_json::from_str(json).map_err(|error| {
        OrchestrationError::Mission(format!("invalid MissionPlan JSON: {error}"))
    })?;
    if !matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_2 | MISSION_PLAN_SCHEMA_V0_3 | MISSION_PLAN_SCHEMA_V0_4
    ) {
        return Err(OrchestrationError::Mission(format!(
            "unsupported MissionPlan schema {}",
            document.schema_version
        )));
    }
    let relation_contract = matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_3 | MISSION_PLAN_SCHEMA_V0_4
    );
    let mode_contract = document.schema_version == MISSION_PLAN_SCHEMA_V0_4;
    let mission_id = MissionId::new(document.mission.id)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let goal = MissionGoal::new(mission_id.clone(), document.mission.objective)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let contexts = document
        .contexts
        .into_iter()
        .map(|context| context_from_document(context, relation_contract, mode_contract))
        .collect::<Result<Vec<_>, _>>()?;
    let tasks = document
        .tasks
        .into_iter()
        .map(|task| task_from_document(&mission_id, task, mode_contract))
        .collect::<Result<Vec<_>, _>>()?;
    let graph = TaskGraph::new(mission_id, tasks)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    MissionPlan::new(goal, graph, contexts)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one wire Context into validated Mission Intelligence domain values.
fn context_from_document(
    context: ContextDocument,
    relation_contract: bool,
    mode_contract: bool,
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
    let relations = match (relation_contract, context.relations) {
        (true, Some(relations)) => relations
            .into_iter()
            .map(relation_from_document)
            .collect::<Result<Vec<_>, _>>()?,
        (true, None) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan v0.3+ Context must declare relations".to_string(),
            ));
        }
        (false, None) => Vec::new(),
        (false, Some(_)) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan v0.2 cannot declare execution relations".to_string(),
            ));
        }
    };
    let has_coupling_mode = context.coupling_mode.is_some();
    let coupling_mode = context
        .coupling_mode
        .map(coupling_mode_from_document)
        .transpose()?
        .unwrap_or_default();
    if !mode_contract
        && (has_coupling_mode || context.shared_view.is_some() || context.peer_channel.is_some())
    {
        return Err(OrchestrationError::Mission(
            "MissionPlan before v0.4 cannot declare shared view or peer channel".to_string(),
        ));
    }
    let shared_view = context
        .shared_view
        .map(shared_view_from_document)
        .transpose()?;
    let peer_channel = context.peer_channel.map(peer_channel_from_document);
    CoordinationContext::new_with_coordination(
        context_id,
        roles,
        relations,
        coupling_mode,
        shared_view,
        peer_channel,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one wire relation into logical Task/Role endpoint identities.
fn relation_from_document(
    relation: RelationDocument,
) -> Result<ExecutionRelationSpec, OrchestrationError> {
    let endpoint =
        |value: RelationEndpointDocument| -> Result<PlannedExecutionRef, OrchestrationError> {
            Ok(PlannedExecutionRef::new(
                TaskId::new(value.task_id)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
                RoleId::new(value.role_id)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
            ))
        };
    let relation_type = match relation.kind.as_str() {
        "requires-active" => ExecutionRelationType::RequiresActive,
        "group-member-state" => ExecutionRelationType::GroupMemberState {
            state_key: required_field(relation.state_key, "state_key")?,
        },
        "shared-spatial-reference" => ExecutionRelationType::SharedSpatialReference {
            reference: spatial_reference_from_document(relation.reference.ok_or_else(|| {
                OrchestrationError::Mission(
                    "shared-spatial-reference requires reference".to_string(),
                )
            })?)?,
        },
        "relative-pose" => ExecutionRelationType::RelativePose {
            frame_id: required_field(relation.frame_id, "frame_id")?,
        },
        "relative-distance" => ExecutionRelationType::RelativeDistance {
            frame_id: required_field(relation.frame_id, "frame_id")?,
        },
        "state-requirement" => ExecutionRelationType::StateRequirement {
            state_key: required_field(relation.state_key, "state_key")?,
            requirement: requirement_from_document(relation.requirement.ok_or_else(|| {
                OrchestrationError::Mission("state-requirement requires requirement".to_string())
            })?),
        },
        "freshness-requirement" => ExecutionRelationType::FreshnessRequirement {
            state_key: required_field(relation.state_key, "state_key")?,
            policy: FreshnessPolicyRef {
                policy_id: required_field(relation.policy_id, "policy_id")?,
            },
        },
        unknown => {
            return Err(OrchestrationError::Mission(format!(
                "unsupported execution relation kind {unknown}"
            )));
        }
    };
    ExecutionRelationSpec::new_typed(
        ExecutionRelationId::new(relation.id)
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
        endpoint(relation.source)?,
        endpoint(relation.target)?,
        relation_type,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one wire Task and its role declarations into validated domain values.
fn task_from_document(
    mission_id: &MissionId,
    task: TaskDocument,
    mode_contract: bool,
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
    let coupling_mode_override = task
        .coupling_mode
        .map(coupling_mode_from_document)
        .transpose()?;
    if !mode_contract && coupling_mode_override.is_some() {
        return Err(OrchestrationError::Mission(
            "MissionPlan before v0.4 cannot declare Task coupling mode".to_string(),
        ));
    }
    PlannedTask::new(
        task.description,
        requirement,
        intents,
        dependencies,
        TaskContinuity::new_with_coupling_mode(
            context_id,
            context_roles,
            scopes,
            coupling_mode_override,
        ),
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts a wire coupling mode into its domain enum.
fn coupling_mode_from_document(
    mode: CouplingModeDocument,
) -> Result<ExecutionCouplingMode, OrchestrationError> {
    Ok(match mode {
        CouplingModeDocument::Independent => ExecutionCouplingMode::Independent,
        CouplingModeDocument::SequentialHandoff => ExecutionCouplingMode::SequentialHandoff,
        CouplingModeDocument::ConcurrentCooperation => ExecutionCouplingMode::ConcurrentCooperation,
        CouplingModeDocument::TightlyCoupledCooperation => {
            ExecutionCouplingMode::TightlyCoupledCooperation
        }
    })
}

/// Converts a wire shared view into its typed domain declaration.
fn shared_view_from_document(
    view: SharedViewDocument,
) -> Result<GroupSharedViewSpec, OrchestrationError> {
    let bindings = view
        .bindings
        .into_iter()
        .map(|binding| {
            let field = match binding.field.as_str() {
                "pose" => GroupViewField::Pose,
                "velocity" => GroupViewField::Velocity,
                "execution" => GroupViewField::Execution,
                unknown => {
                    return Err(OrchestrationError::Mission(format!(
                        "unsupported Group shared view field {unknown}"
                    )));
                }
            };
            let context_role_id = ContextRoleId::new(binding.context_role_id)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
            match field {
                GroupViewField::Execution => {
                    if binding.state_export_id.is_some() || binding.payload_schema.is_some() {
                        return Err(OrchestrationError::Mission(
                            "Execution Group view binding cannot declare State export fields"
                                .to_string(),
                        ));
                    }
                    Ok(GroupViewBinding::new_execution(context_role_id))
                }
                GroupViewField::Pose | GroupViewField::Velocity => GroupViewBinding::new(
                    context_role_id,
                    field,
                    binding.state_export_id.ok_or_else(|| {
                        OrchestrationError::Mission(
                            "spatial Group view binding requires state_export_id".to_string(),
                        )
                    })?,
                    binding.payload_schema.ok_or_else(|| {
                        OrchestrationError::Mission(
                            "spatial Group view binding requires payload_schema".to_string(),
                        )
                    })?,
                )
                .map_err(|error| OrchestrationError::Mission(error.to_string())),
            }
        })
        .collect::<Result<Vec<_>, _>>()?;
    GroupSharedViewSpec::new(
        view.spatial_reference
            .map(spatial_reference_from_document)
            .transpose()?,
        bindings,
        view.include_freshness,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts a wire spatial reference into a typed domain value.
fn spatial_reference_from_document(
    reference: SpatialReferenceDocument,
) -> Result<SharedSpatialReference, OrchestrationError> {
    if reference.map_id.trim().is_empty()
        || reference.revision_id.trim().is_empty()
        || reference.frame_id.trim().is_empty()
    {
        return Err(OrchestrationError::Mission(
            "spatial reference fields must not be empty".to_string(),
        ));
    }
    SharedSpatialReference::new(
        MapRevisionSelector::new(
            MapId::new(reference.map_id)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
            MapRevisionId::new(reference.revision_id)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
        ),
        reference.frame_id,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts a wire peer channel declaration into a typed domain value.
fn peer_channel_from_document(channel: PeerChannelDocument) -> PeerChannelSpec {
    PeerChannelSpec {
        profile_id: channel.profile_id,
        message_schema: channel.message_schema,
    }
}

/// Converts a wire state requirement into a typed domain token.
fn requirement_from_document(requirement: RequirementDocument) -> RelationStateRequirement {
    match requirement {
        RequirementDocument::Available => RelationStateRequirement::Available,
        RequirementDocument::Unavailable => RelationStateRequirement::Unavailable,
    }
}

/// Requires a non-empty typed relation field.
fn required_field(value: Option<String>, field: &str) -> Result<String, OrchestrationError> {
    let value = value
        .ok_or_else(|| OrchestrationError::Mission(format!("typed relation requires {field}")))?;
    if value.trim().is_empty() {
        return Err(OrchestrationError::Mission(format!(
            "typed relation field {field} must not be empty"
        )));
    }
    Ok(value)
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
