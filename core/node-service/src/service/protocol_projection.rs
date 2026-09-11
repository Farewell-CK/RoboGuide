//! Node-local facts projected into formal Node Protocol messages.

use super::*;

/// Maps one local peer readiness fact into a session-scoped protocol message.
pub(super) fn peer_readiness_message(
    session_id: &str,
    sequence: u64,
    fact: crate::LocalPeerChannelReadiness,
) -> NodeMessage {
    NodeMessage {
        message: Some(NodePayload::PeerChannelReadiness(PeerChannelReadiness {
            session_id: session_id.to_string(),
            sequence,
            group_id: fact.group_id,
            context_id: fact.context_id,
            context_role_id: fact.context_role_id,
            local_system_id: fact.local_system_id,
            channel_instance_id: fact.channel_instance_id,
            profile_id: fact.profile_id,
            message_schema: fact.message_schema,
            ready: fact.ready,
            valid_for_ms: fact.valid_for_ms,
        })),
    }
}

/// Encodes sampled State facts into protocol-bounded deterministic batches.
pub(super) fn state_observation_batches(
    facts: Vec<crate::StateExportFact>,
) -> Vec<Vec<StateObservation>> {
    let mut batches = Vec::new();
    let mut current = Vec::new();
    let mut current_bytes = 0usize;
    for fact in facts {
        let Ok(json_value) = serde_json::to_vec(&fact.value) else {
            continue;
        };
        if json_value.len() > domain::MAX_STATE_PAYLOAD_BYTES {
            continue;
        }
        if !current.is_empty()
            && (current.len() == MAX_STATE_BATCH_RECORDS
                || current_bytes.saturating_add(json_value.len()) > MAX_STATE_BATCH_BYTES)
        {
            batches.push(std::mem::take(&mut current));
            current_bytes = 0;
        }
        current_bytes = current_bytes.saturating_add(json_value.len());
        current.push(StateObservation {
            export_id: fact.export_id,
            json_value,
            has_source_observed_at: fact.source_observed_at_ms.is_some(),
            source_observed_at_ms: fact.source_observed_at_ms.unwrap_or_default(),
            has_confidence: fact.confidence_millionths.is_some(),
            confidence_millionths: fact.confidence_millionths.unwrap_or_default(),
        });
    }
    if !current.is_empty() {
        batches.push(current);
    }
    batches
}

/// Sends an explicit local rejection without changing execution terminal state.
pub(super) fn send_local_rejection(
    outbound: &mpsc::UnboundedSender<NodeMessage>,
    session_id: &str,
    execution_id: &str,
    code: &str,
    error: &EngineError,
) -> Result<(), NodeServiceError> {
    outbound
        .send(NodeMessage {
            message: Some(NodePayload::Error(ProtocolError {
                code: code.to_string(),
                reason: error.to_string(),
                session_id: session_id.to_string(),
                execution_id: execution_id.to_string(),
            })),
        })
        .map_err(|_| NodeServiceError::Closed)
}

/// Builds a legacy complete registration with static-ready capability facts.
#[cfg(test)]
pub(super) fn registration_from_catalog(catalog: &crate::CompiledLocalCatalog) -> NodeRegistration {
    let readiness = catalog
        .capability_profiles()
        .keys()
        .cloned()
        .map(|contract| (contract, true))
        .collect();
    registration_from_readiness(catalog, &readiness)
}

/// Builds a complete registration from one current health/readiness observation.
pub(super) fn registration_from_observation(
    catalog: &crate::CompiledLocalCatalog,
    observation: &crate::NodeObservation,
) -> NodeRegistration {
    registration_from_readiness(catalog, &readiness_snapshot(observation))
}

/// Extracts the comparable exact-contract availability snapshot from one observation.
pub(super) fn readiness_snapshot(observation: &crate::NodeObservation) -> BTreeMap<String, bool> {
    observation
        .capabilities()
        .iter()
        .map(|(contract, fact)| (contract.clone(), fact.available))
        .collect()
}

/// Builds an ordered management batch with one monotonic sequence shared by updates and heartbeats.
pub(super) fn management_messages(
    session_id: &str,
    lease_id: &str,
    sequence: &mut u64,
    catalog: &crate::CompiledLocalCatalog,
    observation: &crate::NodeObservation,
    previous_readiness: &mut BTreeMap<String, bool>,
) -> Vec<NodeMessage> {
    let readiness = readiness_snapshot(observation);
    let mut messages = Vec::with_capacity(2);
    if readiness != *previous_readiness {
        *sequence = sequence.saturating_add(1);
        messages.push(NodeMessage {
            message: Some(NodePayload::RegistrationUpdate(RegistrationUpdate {
                session_id: session_id.to_string(),
                sequence: *sequence,
                registration: Some(registration_from_observation(catalog, observation)),
            })),
        });
        *previous_readiness = readiness;
    }
    *sequence = sequence.saturating_add(1);
    messages.push(NodeMessage {
        message: Some(NodePayload::Heartbeat(Heartbeat {
            session_id: session_id.to_string(),
            lease_id: lease_id.to_string(),
            sequence: *sequence,
            status: Some(observation.status().clone()),
        })),
    });
    messages
}

/// Builds one wire registration from a complete exact-contract availability snapshot.
pub(super) fn registration_from_readiness(
    catalog: &crate::CompiledLocalCatalog,
    readiness: &BTreeMap<String, bool>,
) -> NodeRegistration {
    let local_systems = catalog
        .local_systems()
        .values()
        .map(|system| LocalSystemDescriptor {
            id: system.id().to_string(),
            runtime: Some(LocalRuntime {
                name: system.runtime_name().to_string(),
                version: system.runtime_version().to_string(),
            }),
            metadata: system.metadata().clone().into_iter().collect(),
        })
        .collect();
    let current_contract =
        catalog.node_contract_version() == integration::grpc::v0_4::NODE_CONTRACT_VERSION;
    let capabilities = if current_contract {
        Vec::new()
    } else {
        catalog
            .capability_profiles()
            .values()
            .map(|profile| Capability {
                kind: profile.kind().to_string(),
                available: readiness
                    .get(profile.contract())
                    .copied()
                    .unwrap_or_else(|| profile.readiness().is_none()),
                contracts: vec![profile.contract().to_string()],
                local_system_id: profile.owner().to_string(),
            })
            .collect()
    };
    let capability_profiles = if current_contract {
        catalog
            .capability_profiles()
            .values()
            .map(|profile| CapabilityProfile {
                contract: profile.contract().to_string(),
                kind: profile.kind().to_string(),
                local_system_id: profile.owner().to_string(),
                ready: readiness
                    .get(profile.contract())
                    .copied()
                    .unwrap_or_else(|| profile.readiness().is_none()),
                attributes: profile
                    .attributes()
                    .iter()
                    .map(|(name, value)| (name.clone(), scalar_value(value)))
                    .collect(),
            })
            .collect()
    } else {
        Vec::new()
    };
    let resources = catalog
        .resources()
        .values()
        .map(|resource| Resource {
            id: resource.id().to_string(),
            kind: resource.kind().to_string(),
            capacity: resource.capacity(),
            metadata: resource.metadata().clone().into_iter().collect(),
            local_system_id: resource.owner().to_string(),
        })
        .collect();
    let sensors = catalog
        .sensors()
        .values()
        .map(|sensor| Sensor {
            id: sensor.id().to_string(),
            kind: sensor.kind().to_string(),
            metadata: sensor.metadata().clone().into_iter().collect(),
            local_system_id: sensor.owner().to_string(),
        })
        .collect();
    let state_exports = catalog
        .state_exports()
        .values()
        .map(|export| StateExportDescriptor {
            export_id: export.id().to_string(),
            local_system_id: export.owner().to_string(),
            object_class: match export.object_class() {
                "node" => StateObjectClass::Node as i32,
                "world" => StateObjectClass::World as i32,
                _ => StateObjectClass::Unspecified as i32,
            },
            object_type: export.object_type().to_string(),
            object_id: export.object_id().to_string(),
            semantic: match export.semantic() {
                "reported" => StateSemantic::Reported as i32,
                "observed" => StateSemantic::Observed as i32,
                _ => StateSemantic::Unspecified as i32,
            },
            payload_schema: export.payload_schema().to_string(),
            valid_for_ms: export.valid_for_ms(),
        })
        .collect();
    let memory_providers = catalog
        .memory_providers()
        .values()
        .map(|provider| MemoryProviderDescriptor {
            provider_id: provider.id().to_string(),
            local_system_id: provider.owner().to_string(),
            kind: match provider.kind() {
                "execution" => MemoryKind::Execution as i32,
                "spatial" => MemoryKind::Spatial as i32,
                "semantic" => MemoryKind::Semantic as i32,
                "experience" => MemoryKind::Experience as i32,
                "artifact" => MemoryKind::Artifact as i32,
                _ => MemoryKind::Unspecified as i32,
            },
            scope: match provider.scope() {
                "local" => MemoryScopeKind::Local as i32,
                "global" => MemoryScopeKind::Global as i32,
                _ => MemoryScopeKind::Unspecified as i32,
            },
            execution_group_id: String::new(),
            visibility: match provider.visibility() {
                "discoverable" => MemoryVisibility::Discoverable as i32,
                "exchangeable" => MemoryVisibility::Exchangeable as i32,
                _ => MemoryVisibility::Unspecified as i32,
            },
            payload_schema: provider.payload_schema().to_string(),
            media_type: provider.media_type().to_string(),
        })
        .collect();
    NodeRegistration {
        node_id: catalog.node_id().to_string(),
        local_systems,
        capabilities,
        sensors,
        resources,
        metadata: Default::default(),
        node_contract_version: catalog.node_contract_version().to_string(),
        state_exports,
        memory_providers,
        capability_profiles,
    }
}

/// Converts one transport-neutral scalar attribute into its protobuf representation.
fn scalar_value(value: &domain::ExecutionValue) -> ScalarValue {
    use integration::grpc::v0_4::scalar_value::Value;
    let value = match value {
        domain::ExecutionValue::Bool(value) => Value::BoolValue(*value),
        domain::ExecutionValue::Integer(value) => Value::IntegerValue(*value),
        domain::ExecutionValue::Float(value) => Value::FloatValue(*value),
        domain::ExecutionValue::String(value) => Value::StringValue(value.clone()),
    };
    ScalarValue { value: Some(value) }
}

/// Reads one required Server stream message.
pub(super) async fn next_server_payload(
    inbound: &mut tonic::Streaming<ServerMessage>,
) -> Result<ServerPayload, NodeServiceError> {
    inbound
        .message()
        .await
        .map_err(NodeServiceError::Status)?
        .and_then(|message| message.message)
        .ok_or(NodeServiceError::Closed)
}
