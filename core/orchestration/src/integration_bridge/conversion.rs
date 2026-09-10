//! Node Protocol and domain conversion plus event-evidence primitives.

use super::*;

/// Returns whether committed assignments exactly and uniquely cover a TaskExecution's roles.
pub(super) fn task_assignments_are_complete(execution: &domain::TaskExecution) -> bool {
    let expected_roles = execution.role_scopes().keys().collect::<BTreeSet<_>>();
    let assigned_roles = execution
        .assignments()
        .iter()
        .map(|assignment| assignment.role_id())
        .collect::<BTreeSet<_>>();
    expected_roles == assigned_roles && execution.assignments().len() == assigned_roles.len()
}

/// Computes each Task's effective coupling mode from its Context default and override.
pub(super) fn effective_task_coupling_modes(
    plan: &domain::MissionPlan,
) -> BTreeMap<domain::TaskId, ExecutionCouplingMode> {
    plan.task_graph()
        .tasks()
        .iter()
        .map(|task| {
            let continuity = task.continuity();
            let mode = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == continuity.context_id())
                .map(|context| {
                    continuity
                        .coupling_mode_override()
                        .unwrap_or_else(|| context.coupling_mode())
                })
                .unwrap_or_default();
            (task.task_id().clone(), mode)
        })
        .collect()
}

/// Selects the latest evidence for one exact node export and payload schema.
pub(super) fn latest_group_record(
    records: &[StateRecord],
    node_id: &NodeId,
    state_export_id: &str,
    payload_schema: &str,
) -> Option<StateRecord> {
    records
        .iter()
        .filter(|record| {
            record.key().source().node_id() == Some(node_id)
                && record.key().channel_id() == state_export_id
                && record.payload_schema() == payload_schema
        })
        .max_by_key(|record| (record.received_at(), record.sequence()))
        .cloned()
}

/// Persists canonical Runtime facts without granting Integration lifecycle authority.
pub(super) fn append_runtime_evidence<E: EventSink>(
    events: &mut E,
    event: &ExecutionEvent,
    timestamp: TimestampMs,
    correlation_id: &CorrelationId,
) {
    let payload = match event {
        ExecutionEvent::TaskActivated { .. } => return,
        ExecutionEvent::RoleCompleted { command } => {
            EventPayload::NodeObservation(NodeEvent::TaskCompleted {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
            })
        }
        ExecutionEvent::RoleFailed { command, reason } => {
            EventPayload::NodeObservation(NodeEvent::TaskFailed {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
                reason: reason.clone(),
            })
        }
        ExecutionEvent::RecoveryRequired {
            execution_id,
            node_id,
            context,
            reason,
        } => EventPayload::RuntimeExecutionRecoveryRequired {
            execution_id: execution_id.clone(),
            node_id: node_id.clone(),
            group_id: context.as_ref().map(|command| command.group_id().clone()),
            task_ref: context.as_ref().map(|command| command.task_ref().clone()),
            role_id: context.as_ref().map(|command| command.role_id().clone()),
            reason: reason.clone(),
        },
        ExecutionEvent::RelationRegistered { relation } => {
            EventPayload::ExecutionRelationRegistered {
                group_id: relation.group_id().clone(),
                relation_id: relation.relation_id().clone(),
                source_task_ref: relation.source_task_ref().clone(),
                source_role_id: relation.source_role_id().clone(),
                target_task_ref: relation.target_task_ref().clone(),
                target_role_id: relation.target_role_id().clone(),
                kind: relation.kind(),
                relation_type: relation.relation_type().clone(),
                coupling_mode: relation.coupling_mode(),
            }
        }
        ExecutionEvent::RelationStateChanged {
            relation,
            previous,
            current,
            source_execution_id,
            target_execution_id,
        } => EventPayload::ExecutionRelationStateChanged {
            group_id: relation.group_id().clone(),
            relation_id: relation.relation_id().clone(),
            previous: *previous,
            current: *current,
            source_execution_id: source_execution_id.clone(),
            target_execution_id: target_execution_id.clone(),
            relation_type: relation.relation_type().clone(),
            coupling_mode: relation.coupling_mode(),
        },
        ExecutionEvent::RelationReconciliationRequired {
            relation,
            state,
            source_execution_id,
            target_execution_id,
            reason,
        } => EventPayload::ExecutionRelationReconciliationRequired {
            group_id: relation.group_id().clone(),
            relation_id: relation.relation_id().clone(),
            state: *state,
            source_task_ref: relation.source_task_ref().clone(),
            source_role_id: relation.source_role_id().clone(),
            target_task_ref: relation.target_task_ref().clone(),
            target_role_id: relation.target_role_id().clone(),
            source_execution_id: source_execution_id.clone(),
            target_execution_id: target_execution_id.clone(),
            reason: reason.clone(),
            relation_type: relation.relation_type().clone(),
            coupling_mode: relation.coupling_mode(),
        },
    };
    events.append(timestamp, correlation_id, None, payload);
}

/// Converts the transport-neutral Runtime status into the bridge compatibility enum.
pub(super) fn remote_status(status: ExecutionStatus) -> RemoteExecutionStatus {
    match status {
        ExecutionStatus::Dispatched | ExecutionStatus::Accepted => RemoteExecutionStatus::Accepted,
        ExecutionStatus::Running => RemoteExecutionStatus::Running,
        ExecutionStatus::Completed => RemoteExecutionStatus::Completed,
        ExecutionStatus::Failed => RemoteExecutionStatus::Failed,
        ExecutionStatus::Cancelled => RemoteExecutionStatus::Cancelled,
        ExecutionStatus::Unknown => RemoteExecutionStatus::Unknown,
    }
}

/// Converts a formal registration into transport-neutral Domain facts.
pub(super) fn registration_from_wire(
    wire: NodeRegistration,
) -> Result<domain::NodeRegistration, IntegrationRuntimeError> {
    let state_exports = wire
        .state_exports
        .iter()
        .map(state_export_from_wire)
        .collect::<Result<Vec<_>, _>>()?;
    let memory_providers = wire
        .memory_providers
        .iter()
        .map(memory_provider_from_wire)
        .collect::<Result<Vec<_>, _>>()?;
    let local_systems = wire
        .local_systems
        .into_iter()
        .map(|local_system| {
            let runtime = local_system.runtime.ok_or_else(|| {
                IntegrationRuntimeError::Protocol("local system lacks runtime".to_string())
            })?;
            Ok(LocalSystemDescriptor::new(
                LocalSystemId::new(local_system.id)?,
                LocalRuntime::new(runtime.name, runtime.version)?,
                local_system.metadata.into_iter().collect(),
            ))
        })
        .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
    let mut capability_kinds = BTreeMap::<CapabilityKind, bool>::new();
    let mut capability_owners = BTreeMap::new();
    let mut contract_kinds = BTreeMap::new();
    let mut capability_readiness = BTreeMap::new();
    for capability in wire.capabilities {
        let kind = capability_kind(&capability.kind)?;
        capability_kinds
            .entry(kind)
            .and_modify(|available| *available |= capability.available)
            .or_insert(capability.available);
        let owner = LocalSystemId::new(capability.local_system_id)?;
        for contract in capability.contracts {
            let contract = parse_contract(&contract)?;
            if capability_owners
                .insert(contract.clone(), owner.clone())
                .is_some()
            {
                return Err(IntegrationRuntimeError::Protocol(
                    "canonical capability has multiple owners".to_string(),
                ));
            }
            contract_kinds.insert(contract.clone(), kind);
            capability_readiness.insert(contract, capability.available);
        }
    }
    let capabilities = capability_kinds
        .into_iter()
        .map(|(kind, available)| Capability::new(kind, available))
        .collect();
    let sensors = wire
        .sensors
        .into_iter()
        .map(|sensor| {
            Ok(SensorDescriptor::new(
                SensorId::new(sensor.id)?,
                sensor.kind,
                LocalSystemId::new(sensor.local_system_id)?,
                sensor.metadata.into_iter().collect(),
            ))
        })
        .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
    let mut resources = Vec::new();
    let mut resource_owners = BTreeMap::new();
    for resource in wire.resources {
        let resource_id = ResourceId::new(resource.id)?;
        let owner = LocalSystemId::new(resource.local_system_id)?;
        resources.push(Resource::new(
            resource_id.clone(),
            resource_kind(&resource.kind)?,
            resource.capacity,
        )?);
        resource_owners.insert(resource_id, owner);
    }
    Ok(
        domain::NodeRegistration::new_with_local_systems_and_readiness(
            NodeId::new(wire.node_id)?,
            local_systems,
            NodeContractVersion::new(wire.node_contract_version)?,
            capabilities,
            capability_owners,
            contract_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
        )?
        .with_state_memory_exports(state_exports, memory_providers)?,
    )
}

/// Converts one wire State export through the node-owned authority invariants.
pub(super) fn state_export_from_wire(
    wire: &integration::grpc::v0_4::StateExportDescriptor,
) -> Result<StateExportDescriptor, IntegrationRuntimeError> {
    let object_class = match integration::grpc::v0_4::StateObjectClass::try_from(wire.object_class)
    {
        Ok(integration::grpc::v0_4::StateObjectClass::Node) => StateObjectClass::Node,
        Ok(integration::grpc::v0_4::StateObjectClass::World) => StateObjectClass::World,
        Ok(integration::grpc::v0_4::StateObjectClass::Roboguide) => StateObjectClass::RoboGuide,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown State object class".to_string(),
            ));
        }
    };
    let semantic = match integration::grpc::v0_4::StateSemantic::try_from(wire.semantic) {
        Ok(integration::grpc::v0_4::StateSemantic::Reported) => StateSemantic::Reported,
        Ok(integration::grpc::v0_4::StateSemantic::Observed) => StateSemantic::Observed,
        Ok(integration::grpc::v0_4::StateSemantic::Desired) => StateSemantic::Desired,
        Ok(integration::grpc::v0_4::StateSemantic::Committed) => StateSemantic::Committed,
        Ok(integration::grpc::v0_4::StateSemantic::Derived) => StateSemantic::Derived,
        Ok(integration::grpc::v0_4::StateSemantic::Belief) => StateSemantic::Belief,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown State semantic".to_string(),
            ));
        }
    };
    Ok(StateExportDescriptor::new(
        wire.export_id.clone(),
        LocalSystemId::new(wire.local_system_id.clone())?,
        StateObjectRef::new(
            object_class,
            wire.object_type.clone(),
            wire.object_id.clone(),
        )?,
        semantic,
        wire.payload_schema.clone(),
        wire.valid_for_ms,
    )?)
}

/// Converts one wire Memory provider without creating a new storage authority.
pub(super) fn memory_provider_from_wire(
    wire: &integration::grpc::v0_4::MemoryProviderDescriptor,
) -> Result<MemoryProviderDescriptor, IntegrationRuntimeError> {
    let kind = match integration::grpc::v0_4::MemoryKind::try_from(wire.kind) {
        Ok(integration::grpc::v0_4::MemoryKind::Execution) => MemoryKind::Execution,
        Ok(integration::grpc::v0_4::MemoryKind::Spatial) => MemoryKind::Spatial,
        Ok(integration::grpc::v0_4::MemoryKind::Semantic) => MemoryKind::Semantic,
        Ok(integration::grpc::v0_4::MemoryKind::Experience) => MemoryKind::Experience,
        Ok(integration::grpc::v0_4::MemoryKind::Artifact) => MemoryKind::Artifact,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory kind".to_string(),
            ));
        }
    };
    let scope = match integration::grpc::v0_4::MemoryScopeKind::try_from(wire.scope) {
        Ok(integration::grpc::v0_4::MemoryScopeKind::Local) => MemoryScopeLimit::Local,
        Ok(integration::grpc::v0_4::MemoryScopeKind::ExecutionGroup) => {
            return Err(IntegrationRuntimeError::Protocol(
                "Memory provider scope cannot contain an execution Group identity".to_string(),
            ));
        }
        Ok(integration::grpc::v0_4::MemoryScopeKind::Global) => MemoryScopeLimit::Global,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory scope".to_string(),
            ));
        }
    };
    let visibility = match integration::grpc::v0_4::MemoryVisibility::try_from(wire.visibility) {
        Ok(integration::grpc::v0_4::MemoryVisibility::Discoverable) => {
            MemoryVisibility::Discoverable
        }
        Ok(integration::grpc::v0_4::MemoryVisibility::Exchangeable) => {
            MemoryVisibility::Exchangeable
        }
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory visibility".to_string(),
            ));
        }
    };
    Ok(MemoryProviderDescriptor::new(
        wire.provider_id.clone(),
        LocalSystemId::new(wire.local_system_id.clone())?,
        kind,
        scope,
        visibility,
        wire.payload_schema.clone(),
        wire.media_type.clone(),
    )?)
}

/// Converts current protocol health into Domain health.
pub(super) fn status_from_wire(
    status: Option<&integration::grpc::v0_4::NodeStatus>,
    observed_at: TimestampMs,
) -> Result<NodeStatus, IntegrationRuntimeError> {
    let status = status.ok_or_else(|| {
        IntegrationRuntimeError::Protocol("heartbeat is missing local health status".to_string())
    })?;
    let health = match status.health.as_str() {
        "online" => NodeHealth::Online,
        "degraded" => NodeHealth::Degraded,
        "offline" => NodeHealth::Offline,
        other => {
            return Err(IntegrationRuntimeError::Protocol(format!(
                "unknown node health {other}"
            )));
        }
    };
    Ok(NodeStatus::new(health, observed_at))
}
/// Parses a wire capability kind.
pub(super) fn capability_kind(value: &str) -> Result<CapabilityKind, IntegrationRuntimeError> {
    match value {
        "mobility" => Ok(CapabilityKind::Mobility),
        "transport" => Ok(CapabilityKind::Transport),
        "compute" => Ok(CapabilityKind::Compute),
        "observation" => Ok(CapabilityKind::Observation),
        _ => Err(IntegrationRuntimeError::Protocol(format!(
            "unknown capability {value}"
        ))),
    }
}
/// Parses a wire resource kind.
pub(super) fn resource_kind(value: &str) -> Result<ResourceKind, IntegrationRuntimeError> {
    match value {
        "space" => Ok(ResourceKind::Space),
        "compute" => Ok(ResourceKind::Compute),
        "time" => Ok(ResourceKind::Time),
        _ => Err(IntegrationRuntimeError::Protocol(format!(
            "unknown resource {value}"
        ))),
    }
}
/// Parses `namespace.name@version` canonical identity.
pub(super) fn parse_contract(
    value: &str,
) -> Result<CapabilityContractRef, IntegrationRuntimeError> {
    let (name, version) = value
        .rsplit_once('@')
        .ok_or_else(|| IntegrationRuntimeError::Protocol("contract lacks version".to_string()))?;
    let (namespace, name) = name
        .rsplit_once('.')
        .ok_or_else(|| IntegrationRuntimeError::Protocol("contract lacks namespace".to_string()))?;
    Ok(CapabilityContractRef::new(namespace, name, version)?)
}

/// Converts existing canonical ExecutionCommand into formal wire invocation.
pub(super) fn invocation_from_command(command: &ExecutionCommand) -> CanonicalInvocation {
    CanonicalInvocation {
        mission_id: command.mission_id().as_str().to_string(),
        task_id: command.task_id().as_str().to_string(),
        group_id: command.group_id().as_str().to_string(),
        role_id: command.role_id().as_str().to_string(),
        capability_contract: command.intent().capability_contract().to_string(),
        parameters: command
            .intent()
            .parameters()
            .iter()
            .map(|(key, value)| (key.clone(), scalar(value)))
            .collect(),
    }
}
/// Converts one transport-neutral scalar.
pub(super) fn scalar(value: &ExecutionValue) -> ScalarValue {
    use integration::grpc::v0_4::scalar_value::Value;
    ScalarValue {
        value: Some(match value {
            ExecutionValue::Bool(value) => Value::BoolValue(*value),
            ExecutionValue::Integer(value) => Value::IntegerValue(*value),
            ExecutionValue::Float(value) => Value::FloatValue(*value),
            ExecutionValue::String(value) => Value::StringValue(value.clone()),
        }),
    }
}
