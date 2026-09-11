//! MissionPlan wire-to-domain decoding and validation.

use crate::OrchestrationError;
use domain::{
    ActorId, CapabilityConstraint, CapabilityConstraintOperator, CapabilityContractRef,
    CapabilityRequirement, ContextRole, ContextRoleId, CoordinationContext, CoordinationContextId,
    ExecutionCouplingMode, ExecutionIntent, ExecutionRelationId, ExecutionRelationSpec,
    ExecutionRelationType, FreshnessPolicyRef, GroupSharedViewSpec, GroupViewBinding,
    GroupViewField, MISSION_PLAN_SCHEMA_V0_2, MISSION_PLAN_SCHEMA_V0_3, MISSION_PLAN_SCHEMA_V0_4,
    MISSION_PLAN_SCHEMA_V0_5, MISSION_PLAN_SCHEMA_V0_6, MISSION_PLAN_SCHEMA_V0_7, MapId,
    MapRevisionId, MapRevisionSelector, MissionActor, MissionGoal, MissionId, MissionPlan,
    OperationRef, PeerChannelSpec, PlannedExecutionRef, PlannedTask, RelationStateRequirement,
    ResourceRequirement, RoleId, RoleRequirement, SharedSpatialReference, TaskContinuity,
    TaskGraph, TaskId, TaskRequirement, TaskSatisfactionBasis, TaskTiming,
    VerifierSatisfactionSpec,
};
use std::collections::BTreeMap;

use super::enum_conversion::{
    capability_from_document, resource_from_document, scope_from_document,
};
use super::execution_value::execution_value;
use super::nullable_millis::NullableMillisField;
use super::wire::*;

/// Decodes historical input or one current MissionPlan v0.7 document.
pub fn decode_mission_plan(json: &str) -> Result<MissionPlan, OrchestrationError> {
    let document: PlanDocument = serde_json::from_str(json).map_err(|error| {
        OrchestrationError::Mission(format!("invalid MissionPlan JSON: {error}"))
    })?;
    if !matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_2
            | MISSION_PLAN_SCHEMA_V0_3
            | MISSION_PLAN_SCHEMA_V0_4
            | MISSION_PLAN_SCHEMA_V0_5
            | MISSION_PLAN_SCHEMA_V0_6
            | MISSION_PLAN_SCHEMA_V0_7
    ) {
        return Err(OrchestrationError::Mission(format!(
            "unsupported MissionPlan schema {}",
            document.schema_version
        )));
    }
    let relation_contract = matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_3
            | MISSION_PLAN_SCHEMA_V0_4
            | MISSION_PLAN_SCHEMA_V0_5
            | MISSION_PLAN_SCHEMA_V0_6
            | MISSION_PLAN_SCHEMA_V0_7
    );
    let mode_contract = matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_4
            | MISSION_PLAN_SCHEMA_V0_5
            | MISSION_PLAN_SCHEMA_V0_6
            | MISSION_PLAN_SCHEMA_V0_7
    );
    let scheduling_contract = matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_5 | MISSION_PLAN_SCHEMA_V0_6 | MISSION_PLAN_SCHEMA_V0_7
    );
    let satisfaction_contract = matches!(
        document.schema_version.as_str(),
        MISSION_PLAN_SCHEMA_V0_6 | MISSION_PLAN_SCHEMA_V0_7
    );
    let normalized_contract = document.schema_version == MISSION_PLAN_SCHEMA_V0_7;
    let MissionDocument {
        id,
        objective,
        actors: actor_documents,
    } = document.mission;
    let mission_id =
        MissionId::new(id).map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let goal = MissionGoal::new(mission_id.clone(), objective)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let contexts = document
        .contexts
        .into_iter()
        .map(|context| context_from_document(context, relation_contract, mode_contract))
        .collect::<Result<Vec<_>, _>>()?;
    let actors = match (normalized_contract, actor_documents) {
        (true, Some(actors)) if !actors.is_empty() => actors
            .into_iter()
            .map(|actor| {
                ActorId::new(actor.id)
                    .map(MissionActor::new)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))
            })
            .collect::<Result<Vec<_>, _>>()?,
        (true, _) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan v0.7 must declare Mission actors".to_string(),
            ));
        }
        (false, None) => Vec::new(),
        (false, Some(_)) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan before v0.7 cannot declare Mission actors".to_string(),
            ));
        }
    };
    let tasks = document
        .tasks
        .into_iter()
        .map(|task| {
            task_from_document(
                &mission_id,
                task,
                mode_contract,
                scheduling_contract,
                satisfaction_contract,
                normalized_contract,
                &contexts,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let graph = TaskGraph::new(mission_id, tasks)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    if normalized_contract {
        MissionPlan::new_with_actors(goal, actors, graph, contexts)
            .map_err(|error| OrchestrationError::Mission(error.to_string()))
    } else {
        MissionPlan::new(goal, graph, contexts)
            .map_err(|error| OrchestrationError::Mission(error.to_string()))
    }
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
    scheduling_contract: bool,
    satisfaction_contract: bool,
    normalized_contract: bool,
    contexts: &[CoordinationContext],
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
        let role_id = RoleId::new(role.id.clone())
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
        let context_role_id = role
            .context_role
            .clone()
            .map(ContextRoleId::new)
            .transpose()
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
        let resource_scope = scope_from_document(role.resource_scope);
        let (requirement, intent) = if normalized_contract {
            normalized_role_from_document(
                role,
                &role_id,
                &context_id,
                context_role_id.as_ref(),
                contexts,
            )?
        } else {
            legacy_role_from_document(role, &role_id, scheduling_contract)?
        };
        roles.push(requirement);
        intents.insert(role_id.clone(), intent);
        if let Some(context_role_id) = context_role_id {
            context_roles.insert(role_id.clone(), context_role_id);
        }
        scopes.insert(role_id, resource_scope);
    }
    let requirement = if scheduling_contract {
        let timing = task.timing.ok_or_else(|| {
            OrchestrationError::Mission("MissionPlan v0.5+ Task must declare timing".to_string())
        })?;
        let timing = if normalized_contract {
            if !matches!(timing.estimated_duration_ms, NullableMillisField::Missing) {
                return Err(OrchestrationError::Mission(
                    "MissionPlan v0.7 timing cannot contain a Planner duration estimate"
                        .to_string(),
                ));
            }
            TaskTiming::new_constraints(
                timing.earliest_start_offset_ms,
                timing.latest_start_offset_ms.0,
                timing.completion_deadline_offset_ms.0,
            )
        } else {
            let estimated_duration_ms = match timing.estimated_duration_ms {
                NullableMillisField::Present(value) => value,
                NullableMillisField::Missing => {
                    return Err(OrchestrationError::Mission(
                        "MissionPlan v0.5-v0.6 timing must declare estimated_duration_ms"
                            .to_string(),
                    ));
                }
            };
            TaskTiming::new(
                timing.earliest_start_offset_ms,
                timing.latest_start_offset_ms.0,
                timing.completion_deadline_offset_ms.0,
                estimated_duration_ms,
            )
        }
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
        TaskRequirement::new_scheduled(mission_id.clone(), task_id, roles, timing)
    } else {
        if task.timing.is_some() {
            return Err(OrchestrationError::Mission(
                "MissionPlan before v0.5 cannot declare task timing".to_string(),
            ));
        }
        TaskRequirement::new(mission_id.clone(), task_id, roles)
    }
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
    let (expected_effect, satisfaction_basis) = match (
        satisfaction_contract,
        normalized_contract,
        task.satisfaction,
    ) {
        (true, true, Some(satisfaction)) => {
            let expected_effect = satisfaction.expected_effect.ok_or_else(|| {
                OrchestrationError::Mission(
                    "MissionPlan v0.7 Task satisfaction must declare expected_effect".to_string(),
                )
            })?;
            let basis = match satisfaction.basis {
                TaskSatisfactionBasisDocument::ExecutionReport => {
                    if satisfaction.verifier.is_some() {
                        return Err(OrchestrationError::Mission(
                            "execution-report satisfaction cannot declare a verifier".to_string(),
                        ));
                    }
                    TaskSatisfactionBasis::ExecutionReport
                }
                TaskSatisfactionBasisDocument::VerifierEvidence => {
                    let verifier = satisfaction.verifier.ok_or_else(|| {
                        OrchestrationError::Mission(
                            "verifier-evidence satisfaction requires verifier policy".to_string(),
                        )
                    })?;
                    TaskSatisfactionBasis::VerifierEvidence(
                        VerifierSatisfactionSpec::new(
                            contract_from_document(verifier.contract)?,
                            verifier.predicate,
                            verifier.max_evidence_age_ms,
                        )
                        .map_err(|error| OrchestrationError::Mission(error.to_string()))?,
                    )
                }
            };
            (expected_effect, basis)
        }
        (true, false, Some(satisfaction)) => {
            if satisfaction.expected_effect.is_some()
                || satisfaction.verifier.is_some()
                || !matches!(
                    satisfaction.basis,
                    TaskSatisfactionBasisDocument::ExecutionReport
                )
            {
                return Err(OrchestrationError::Mission(
                    "MissionPlan v0.6 supports only execution-report satisfaction".to_string(),
                ));
            }
            (
                task.description.clone(),
                TaskSatisfactionBasis::ExecutionReport,
            )
        }
        (true, _, None) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan v0.6 Task must declare satisfaction".to_string(),
            ));
        }
        (false, _, Some(_)) => {
            return Err(OrchestrationError::Mission(
                "MissionPlan before v0.6 cannot declare Task satisfaction".to_string(),
            ));
        }
        (false, _, None) => (
            task.description.clone(),
            TaskSatisfactionBasis::ExecutionReport,
        ),
    };
    PlannedTask::new_with_completion(
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
        expected_effect,
        satisfaction_basis,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))
}

/// Converts one normalized v0.7 Role without duplicating Actor or executable contract fields.
fn normalized_role_from_document(
    role: RoleDocument,
    role_id: &RoleId,
    context_id: &CoordinationContextId,
    context_role_id: Option<&ContextRoleId>,
    contexts: &[CoordinationContext],
) -> Result<(RoleRequirement, ExecutionIntent), OrchestrationError> {
    if role.actor.is_some()
        || role.capability.is_some()
        || role.contract.is_some()
        || role.execution.is_some()
        || role.resources.is_some()
        || !matches!(role.resource_kind, ResourceKindField::Missing)
    {
        return Err(OrchestrationError::Mission(
            "MissionPlan v0.7 Role contains a legacy duplicated field".to_string(),
        ));
    }
    let context_role_id = context_role_id.ok_or_else(|| {
        OrchestrationError::Mission(
            "MissionPlan v0.7 Role must reference a ContextRole".to_string(),
        )
    })?;
    let actor_id = contexts
        .iter()
        .find(|context| context.context_id() == context_id)
        .and_then(|context| context.role(context_role_id))
        .map(ContextRole::actor_id)
        .cloned()
        .ok_or_else(|| {
            OrchestrationError::Mission(format!(
                "Role {role_id} references an unknown ContextRole {context_role_id}"
            ))
        })?;
    let requirements = role.requirements.ok_or_else(|| {
        OrchestrationError::Mission("MissionPlan v0.7 Role lacks requirements".to_string())
    })?;
    let capabilities = requirements
        .capabilities
        .into_iter()
        .map(capability_requirement_from_document)
        .collect::<Result<Vec<_>, _>>()?;
    let resources = requirements
        .resources
        .into_iter()
        .map(|resource| {
            ResourceRequirement::new(resource_from_document(resource.kind), resource.units)
                .map_err(|error| OrchestrationError::Mission(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let requirement =
        RoleRequirement::new_normalized(role_id.clone(), Some(actor_id), capabilities, resources)
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let intent = role.execution_intent.ok_or_else(|| {
        OrchestrationError::Mission("MissionPlan v0.7 Role lacks execution_intent".to_string())
    })?;
    if intent.capability_contract.is_some() {
        return Err(OrchestrationError::Mission(
            "MissionPlan v0.7 ExecutionIntent cannot use capability_contract".to_string(),
        ));
    }
    let operation = intent.operation.ok_or_else(|| {
        OrchestrationError::Mission("MissionPlan v0.7 ExecutionIntent lacks operation".to_string())
    })?;
    let operation = OperationRef::new(operation.namespace, operation.name, operation.version)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let objective = intent.objective.ok_or_else(|| {
        OrchestrationError::Mission("MissionPlan v0.7 ExecutionIntent lacks objective".to_string())
    })?;
    let parameters = intent
        .parameters
        .into_iter()
        .map(|(key, value)| Ok((key, execution_value(value)?)))
        .collect::<Result<BTreeMap<_, _>, OrchestrationError>>()?;
    let intent = ExecutionIntent::new_semantic(operation, objective, parameters)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    Ok((requirement, intent))
}

/// Converts one pre-v0.7 Role into normalized domain values without weakening compatibility.
fn legacy_role_from_document(
    role: RoleDocument,
    role_id: &RoleId,
    scheduling_contract: bool,
) -> Result<(RoleRequirement, ExecutionIntent), OrchestrationError> {
    if role.requirements.is_some() || role.execution_intent.is_some() {
        return Err(OrchestrationError::Mission(
            "MissionPlan before v0.7 cannot declare normalized Role fields".to_string(),
        ));
    }
    let contract =
        contract_from_document(role.contract.ok_or_else(|| {
            OrchestrationError::Mission("legacy Role lacks contract".to_string())
        })?)?;
    let intent = role
        .execution
        .ok_or_else(|| OrchestrationError::Mission("legacy Role lacks execution".to_string()))?;
    if intent.operation.is_some() || intent.objective.is_some() {
        return Err(OrchestrationError::Mission(
            "MissionPlan before v0.7 cannot declare semantic operation fields".to_string(),
        ));
    }
    let intent_contract = contract_from_document(intent.capability_contract.ok_or_else(|| {
        OrchestrationError::Mission("legacy ExecutionIntent lacks capability_contract".to_string())
    })?)?;
    if contract != intent_contract {
        return Err(OrchestrationError::Mission(format!(
            "Role {role_id} contract differs from execution intent"
        )));
    }
    let parameters = intent
        .parameters
        .into_iter()
        .map(|(key, value)| Ok((key, execution_value(value)?)))
        .collect::<Result<BTreeMap<_, _>, OrchestrationError>>()?;
    let intent = ExecutionIntent::new(intent_contract, parameters)
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let actor_id = ActorId::new(
        role.actor
            .ok_or_else(|| OrchestrationError::Mission("legacy Role lacks actor".to_string()))?,
    )
    .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
    let capability =
        capability_from_document(role.capability.ok_or_else(|| {
            OrchestrationError::Mission("legacy Role lacks capability".to_string())
        })?);
    let requirement = if scheduling_contract {
        if !matches!(role.resource_kind, ResourceKindField::Missing) {
            return Err(OrchestrationError::Mission(
                "MissionPlan v0.5+ Role must use resources instead of resource_kind".to_string(),
            ));
        }
        let resources = role
            .resources
            .ok_or_else(|| {
                OrchestrationError::Mission(
                    "MissionPlan v0.5+ Role must declare resources".to_string(),
                )
            })?
            .into_iter()
            .map(|resource| {
                ResourceRequirement::new(resource_from_document(resource.kind), resource.units)
                    .map_err(|error| OrchestrationError::Mission(error.to_string()))
            })
            .collect::<Result<Vec<_>, _>>()?;
        RoleRequirement::new_scheduled(
            role_id.clone(),
            Some(actor_id),
            capability,
            Some(contract),
            resources,
        )
        .map_err(|error| OrchestrationError::Mission(error.to_string()))?
    } else {
        if role.resources.is_some() {
            return Err(OrchestrationError::Mission(
                "MissionPlan before v0.5 cannot declare quantitative resources".to_string(),
            ));
        }
        let resource_kind = match role.resource_kind {
            ResourceKindField::Present(resource) => resource.map(resource_from_document),
            ResourceKindField::Missing => {
                return Err(OrchestrationError::Mission(
                    "MissionPlan before v0.5 Role must declare resource_kind".to_string(),
                ));
            }
        };
        RoleRequirement::new_with_actor_and_contract(
            role_id.clone(),
            actor_id,
            capability,
            contract,
            resource_kind,
        )
    };
    Ok((requirement, intent))
}

/// Converts one wire capability and its predicates into validated domain values.
fn capability_requirement_from_document(
    requirement: CapabilityRequirementDocument,
) -> Result<CapabilityRequirement, OrchestrationError> {
    let contract = contract_from_document(requirement.contract)?;
    let constraints = requirement
        .constraints
        .into_iter()
        .map(|constraint| {
            let operator = match constraint.operator {
                CapabilityConstraintOperatorDocument::Equals => {
                    CapabilityConstraintOperator::Equals
                }
                CapabilityConstraintOperatorDocument::AtLeast => {
                    CapabilityConstraintOperator::AtLeast
                }
                CapabilityConstraintOperatorDocument::AtMost => {
                    CapabilityConstraintOperator::AtMost
                }
            };
            CapabilityConstraint::new(
                constraint.attribute,
                operator,
                execution_value(constraint.value)?,
            )
            .map_err(|error| OrchestrationError::Mission(error.to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    CapabilityRequirement::new(contract, constraints)
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
