//! Stable MissionPlan checkpoint serialization.

use super::super::*;

/// Serializes a validated domain MissionPlan into the normalized v0.7 wire shape.
pub(crate) fn mission_plan_json(plan: &MissionPlan) -> serde_json::Value {
    if is_v0_6_compatible(plan) {
        return legacy_mission_plan_json(plan);
    }
    normalized_mission_plan_json(plan)
}

/// Serializes Mission semantics that require the normalized v0.7 wire contract.
fn normalized_mission_plan_json(plan: &MissionPlan) -> serde_json::Value {
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
                    let scope = match task.continuity().resource_scope(role.role_id()) {
                        domain::ResourceBindingScope::Task => "task",
                        domain::ResourceBindingScope::Context => "context",
                    };
                    serde_json::json!({
                        "id": role.role_id().as_str(),
                        "context_role": task.continuity().context_role(role.role_id()).expect("normalized MissionPlan Role has a ContextRole").as_str(),
                        "requirements": {
                            "capabilities": role.capability_requirements().iter().map(capability_requirement_json).collect::<Vec<_>>(),
                            "resources": role.resource_requirements().iter().map(|resource| serde_json::json!({
                                "kind": format!("{:?}", resource.kind()).to_lowercase(),
                                "units": resource.units(),
                            })).collect::<Vec<_>>(),
                        },
                        "resource_scope": scope,
                        "execution_intent": {
                            "operation": contract_json(intent.operation().as_legacy_contract()),
                            "objective": intent.objective(),
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
                "timing": {
                    "earliest_start_offset_ms": task.requirement().timing().earliest_start_offset_ms(),
                    "latest_start_offset_ms": task.requirement().timing().latest_start_offset_ms(),
                    "completion_deadline_offset_ms": task.requirement().timing().completion_deadline_offset_ms(),
                },
                "satisfaction": satisfaction_json(task),
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
        "schema_version": domain::MISSION_PLAN_SCHEMA_V0_7,
        "mission": {
            "id": plan.goal().mission_id().as_str(),
            "objective": plan.goal().objective(),
            "actors": plan.actors().iter().map(|actor| serde_json::json!({"id": actor.id().as_str()})).collect::<Vec<_>>(),
        },
        "contexts": contexts,
        "tasks": tasks,
    })
}

/// Returns whether the v0.6 compatibility contract can preserve every semantic field.
fn is_v0_6_compatible(plan: &MissionPlan) -> bool {
    plan.task_graph().tasks().iter().all(|task| {
        task.expected_effect() == task.description()
            && matches!(
                task.satisfaction_basis(),
                domain::TaskSatisfactionBasis::ExecutionReport
            )
            && task.requirement().roles().iter().all(|role| {
                let requirements = role.capability_requirements();
                role.capability().is_some()
                    && role.actor_id().is_some()
                    && requirements.len() == 1
                    && requirements[0].constraints().is_empty()
                    && role.required_contract() == Some(requirements[0].contract())
                    && task.execution_intent(role.role_id()).is_some_and(|intent| {
                        intent.capability_contract() == requirements[0].contract()
                            && intent.objective() == intent.operation().to_string()
                    })
            })
    })
}

/// Preserves the historical v0.6 checkpoint shape when it remains lossless.
fn legacy_mission_plan_json(plan: &MissionPlan) -> serde_json::Value {
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
                        .expect("v0.6-compatible Role has one contract");
                    let scope = match task.continuity().resource_scope(role.role_id()) {
                        domain::ResourceBindingScope::Task => "task",
                        domain::ResourceBindingScope::Context => "context",
                    };
                    serde_json::json!({
                        "id": role.role_id().as_str(),
                        "actor": role.actor_id().expect("v0.6-compatible Role has one Actor").as_str(),
                        "capability": format!("{:?}", role.capability().expect("v0.6-compatible Role has a coarse capability")).to_lowercase(),
                        "contract": contract_json(contract),
                        "resources": role.resource_requirements().iter().map(|resource| serde_json::json!({
                            "kind": format!("{:?}", resource.kind()).to_lowercase(),
                            "units": resource.units(),
                        })).collect::<Vec<_>>(),
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
                "timing": {
                    "earliest_start_offset_ms": task.requirement().timing().earliest_start_offset_ms(),
                    "latest_start_offset_ms": task.requirement().timing().latest_start_offset_ms(),
                    "completion_deadline_offset_ms": task.requirement().timing().completion_deadline_offset_ms(),
                    "estimated_duration_ms": task.requirement().timing().estimated_duration_ms(),
                },
                "satisfaction": {"basis": "execution-report"},
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
        "schema_version": domain::MISSION_PLAN_SCHEMA_V0_6,
        "mission": {"id": plan.goal().mission_id().as_str(), "objective": plan.goal().objective()},
        "contexts": contexts,
        "tasks": tasks,
    })
}

/// Serializes one exact capability requirement and its feasibility predicates.
fn capability_requirement_json(requirement: &domain::CapabilityRequirement) -> serde_json::Value {
    serde_json::json!({
        "contract": contract_json(requirement.contract()),
        "constraints": requirement.constraints().iter().map(|constraint| serde_json::json!({
            "attribute": constraint.attribute(),
            "operator": match constraint.operator() {
                domain::CapabilityConstraintOperator::Equals => "equals",
                domain::CapabilityConstraintOperator::AtLeast => "at-least",
                domain::CapabilityConstraintOperator::AtMost => "at-most",
            },
            "value": execution_value_json(constraint.value()),
        })).collect::<Vec<_>>(),
    })
}

/// Serializes a Task's expected effect and evidence acceptance policy.
fn satisfaction_json(task: &domain::PlannedTask) -> serde_json::Value {
    match task.satisfaction_basis() {
        domain::TaskSatisfactionBasis::ExecutionReport => serde_json::json!({
            "expected_effect": task.expected_effect(),
            "basis": "execution-report",
        }),
        domain::TaskSatisfactionBasis::VerifierEvidence(verifier) => serde_json::json!({
            "expected_effect": task.expected_effect(),
            "basis": "verifier-evidence",
            "verifier": {
                "contract": contract_json(verifier.verifier()),
                "predicate": verifier.predicate(),
                "max_evidence_age_ms": verifier.max_evidence_age_ms(),
            },
        }),
    }
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
