//! Session acceptance, routing, lease, and registration validation helpers.

use super::*;

/// Emits one local route-loss observation without requiring a remote acknowledgement.
pub(super) fn emit_unavailable(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    node_id: String,
    session_id: String,
) {
    let _ = events.send(GrpcNodeEventDelivery::observation(
        GrpcNodeEvent::Unavailable {
            node_id,
            session_id,
        },
    ));
}

/// Delivers one validated fact and waits for application authority plus persistence acceptance.
pub(super) async fn deliver_for_acceptance(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    event: GrpcNodeEvent,
) -> Result<(), Status> {
    let (response, decision) = oneshot::channel();
    events
        .send(GrpcNodeEventDelivery::requiring_acceptance(event, response))
        .map_err(|_| Status::unavailable("Controller fact consumer is closed"))?;
    let decision = tokio::time::timeout(APPLICATION_ACCEPTANCE_TIMEOUT, decision)
        .await
        .map_err(|_| Status::deadline_exceeded("Controller fact acceptance timed out"))?
        .map_err(|_| Status::unavailable("Controller fact response was dropped"))?;
    decision.map_err(|failure| match failure {
        ApplicationAcceptanceFailure::Rejected(reason) => {
            Status::failed_precondition(format!("Controller rejected fact: {reason}"))
        }
        ApplicationAcceptanceFailure::Unavailable(reason) => {
            Status::unavailable(format!("Controller fact acceptance unavailable: {reason}"))
        }
    })
}

/// Activates one accepted session and emits `Registered` before commands can be routed.
pub(super) fn activate_current_route(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
    lease_id: &str,
) -> Result<(), Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    let route = sessions
        .get_mut(node_id)
        .filter(|route| route.session_id == session_id)
        .ok_or_else(|| Status::aborted("registration session was superseded"))?;
    route
        .sender
        .send(Ok(ServerMessage {
            message: Some(ServerPayload::Registered(Registered {
                session_id: session_id.to_string(),
                lease_id: lease_id.to_string(),
            })),
        }))
        .map_err(|_| Status::unavailable("response stream closed"))?;
    route.last_heartbeat = std::time::Instant::now();
    route.active = true;
    Ok(())
}

/// Removes a route only when it is still owned by the supplied session.
pub(super) fn remove_current_route(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
) -> Result<bool, Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    if sessions
        .get(node_id)
        .is_some_and(|route| route.session_id == session_id)
    {
        sessions.remove(node_id);
        Ok(true)
    } else {
        Ok(false)
    }
}

/// Returns whether the current route exceeded its heartbeat lease.
pub(super) fn route_is_expired(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
) -> Result<bool, Status> {
    let sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    Ok(sessions.get(node_id).is_none_or(|route| {
        route.session_id != session_id || route.last_heartbeat.elapsed() >= route.lease_duration
    }))
}

/// Accepts only the current session and renews only its matching lease heartbeat.
pub(super) fn accept_current_message(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
    message: &NodeMessage,
) -> Result<bool, Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    let Some(route) = sessions.get_mut(node_id) else {
        return Ok(false);
    };
    if route.session_id != session_id {
        return Ok(false);
    }
    if let Some(NodePayload::Heartbeat(heartbeat)) = &message.message {
        if heartbeat.session_id != session_id || heartbeat.lease_id != route.lease_id {
            return Ok(false);
        }
        if heartbeat.sequence <= route.management_sequence {
            return Ok(false);
        }
        route.management_sequence = heartbeat.sequence;
        route.last_heartbeat = std::time::Instant::now();
    } else {
        let message_session = match &message.message {
            Some(NodePayload::RegistrationUpdate(value)) => {
                if value.session_id != session_id {
                    return Ok(false);
                }
                if value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                let registration = value.registration.as_ref().ok_or_else(|| {
                    Status::invalid_argument("RegistrationUpdate requires registration")
                })?;
                if registration.node_id != node_id
                    || registration.node_contract_version != route.node_contract_version
                {
                    return Ok(false);
                }
                validate_registration(registration)?;
                route.management_sequence = value.sequence;
                route.state_export_ids = registration
                    .state_exports
                    .iter()
                    .map(|export| export.export_id.clone())
                    .collect();
                &value.session_id
            }
            Some(NodePayload::StateObservationBatch(value)) => {
                if value.session_id != session_id || value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                validate_state_observation_batch(value, &route.state_export_ids)?;
                route.management_sequence = value.sequence;
                &value.session_id
            }
            Some(NodePayload::PeerChannelReadiness(value)) => {
                if value.session_id != session_id || value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                if value.group_id.trim().is_empty()
                    || value.context_id.trim().is_empty()
                    || value.context_role_id.trim().is_empty()
                    || value.local_system_id.trim().is_empty()
                    || value.channel_instance_id.trim().is_empty()
                    || value.profile_id.trim().is_empty()
                    || value.message_schema.trim().is_empty()
                    || value.valid_for_ms == 0
                    || value.valid_for_ms > 60_000
                {
                    return Err(Status::invalid_argument(
                        "PeerChannelReadiness identities and bounded validity must be valid",
                    ));
                }
                route.management_sequence = value.sequence;
                &value.session_id
            }
            Some(NodePayload::CommandReceipt(value)) => {
                if value.session_id != session_id || value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                if value.command_id.trim().is_empty() || value.execution_id.trim().is_empty() {
                    return Err(Status::invalid_argument(
                        "CommandReceipt requires command and execution identities",
                    ));
                }
                if crate::grpc::v0_4::CommandKind::try_from(value.kind)
                    .ok()
                    .is_none_or(|kind| kind == crate::grpc::v0_4::CommandKind::Unspecified)
                {
                    return Err(Status::invalid_argument("CommandReceipt kind is invalid"));
                }
                if crate::grpc::v0_4::CommandReceiptStatus::try_from(value.status)
                    .ok()
                    .is_none_or(|status| {
                        status == crate::grpc::v0_4::CommandReceiptStatus::Unspecified
                    })
                {
                    return Err(Status::invalid_argument("CommandReceipt status is invalid"));
                }
                route.management_sequence = value.sequence;
                &value.session_id
            }
            Some(NodePayload::ExecutionEvent(value)) => &value.session_id,
            Some(NodePayload::ExecutionSnapshot(value)) => &value.session_id,
            Some(NodePayload::Error(value)) => &value.session_id,
            _ => return Ok(false),
        };
        if message_session != session_id {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Validates the explicitly versioned registration without inferring Local How on the Server.
pub(super) fn validate_registration(
    registration: &crate::grpc::v0_4::NodeRegistration,
) -> Result<(), Status> {
    if registration.node_id.trim().is_empty()
        || !matches!(
            registration.node_contract_version.as_str(),
            NODE_CONTRACT_VERSION | PREVIOUS_NODE_CONTRACT_VERSION | LEGACY_NODE_CONTRACT_VERSION
        )
    {
        return Err(Status::invalid_argument(
            "registration node and contract identities are invalid",
        ));
    }
    let mut local_system_ids = BTreeSet::new();
    for local_system in &registration.local_systems {
        if local_system.id.trim().is_empty() || !local_system_ids.insert(local_system.id.as_str()) {
            return Err(Status::invalid_argument(
                "local system IDs must be nonblank and unique",
            ));
        }
        let runtime = local_system
            .runtime
            .as_ref()
            .ok_or_else(|| Status::invalid_argument("local system runtime is required"))?;
        if runtime.name.trim().is_empty() || runtime.version.trim().is_empty() {
            return Err(Status::invalid_argument(
                "local runtime name and version must be nonblank",
            ));
        }
    }
    if local_system_ids.is_empty() {
        return Err(Status::invalid_argument(
            "registration requires at least one local system",
        ));
    }
    validate_capabilities(registration, &local_system_ids)?;
    validate_operation_support(registration, &local_system_ids)?;
    let mut sensor_ids = BTreeSet::new();
    for sensor in &registration.sensors {
        require_known_owner(&sensor.local_system_id, &local_system_ids)?;
        if sensor.id.trim().is_empty()
            || sensor.kind.trim().is_empty()
            || !sensor_ids.insert(&sensor.id)
        {
            return Err(Status::invalid_argument(
                "sensor IDs must be nonblank and unique",
            ));
        }
    }
    let mut resource_ids = BTreeSet::new();
    for resource in &registration.resources {
        require_known_owner(&resource.local_system_id, &local_system_ids)?;
        if resource.id.trim().is_empty()
            || !matches!(resource.kind.as_str(), "space" | "compute" | "time")
            || resource.capacity == 0
            || !resource_ids.insert(&resource.id)
        {
            return Err(Status::invalid_argument(
                "resource IDs must be unique with positive capacity",
            ));
        }
    }
    let mut export_ids = BTreeSet::new();
    for export in &registration.state_exports {
        require_known_owner(&export.local_system_id, &local_system_ids)?;
        if export.export_id.trim().is_empty()
            || !export_ids.insert(export.export_id.as_str())
            || export.object_type.trim().is_empty()
            || export.object_id.trim().is_empty()
            || export.payload_schema.trim().is_empty()
            || export.valid_for_ms == 0
            || !matches!(export.object_class, 1 | 2)
            || !matches!(export.semantic, 3 | 4)
        {
            return Err(Status::invalid_argument(
                "State exports must be unique node/world Reported/Observed channels with schemas and positive validity",
            ));
        }
    }
    let mut provider_ids = BTreeSet::new();
    for provider in &registration.memory_providers {
        require_known_owner(&provider.local_system_id, &local_system_ids)?;
        let scope_valid = matches!(provider.scope, 1 | 3) && provider.execution_group_id.is_empty();
        if provider.provider_id.trim().is_empty()
            || !provider_ids.insert(provider.provider_id.as_str())
            || !matches!(provider.kind, 1..=5)
            || !scope_valid
            || !matches!(provider.visibility, 1 | 2)
            || provider.payload_schema.trim().is_empty()
            || provider.media_type.trim().is_empty()
        {
            return Err(Status::invalid_argument(
                "Memory providers must have unique identities, local/global maximum scope, known kinds/visibility, and nonblank schemas",
            ));
        }
    }
    Ok(())
}

/// Enforces one unambiguous capability representation for each Node Contract version.
fn validate_capabilities(
    registration: &crate::grpc::v0_4::NodeRegistration,
    local_system_ids: &BTreeSet<&str>,
) -> Result<(), Status> {
    let mut contracts: BTreeSet<&str> = BTreeSet::new();
    if registration.node_contract_version == LEGACY_NODE_CONTRACT_VERSION {
        if !registration.capability_profiles.is_empty() {
            return Err(Status::invalid_argument(
                "Node Contract v0.4 cannot declare capability profiles",
            ));
        }
        for capability in &registration.capabilities {
            require_known_owner(&capability.local_system_id, local_system_ids)?;
            if !valid_capability_kind(&capability.kind) || capability.contracts.is_empty() {
                return Err(Status::invalid_argument(
                    "capability kind is unsupported or has no canonical contracts",
                ));
            }
            for contract in &capability.contracts {
                if !valid_contract_identity(contract) || !contracts.insert(contract.as_str()) {
                    return Err(Status::invalid_argument(
                        "canonical capability contracts must have one unique owner",
                    ));
                }
            }
        }
        return Ok(());
    }
    if !registration.capabilities.is_empty() {
        return Err(Status::invalid_argument(
            "Node Contract v0.5 cannot mix legacy capabilities with capability profiles",
        ));
    }
    for profile in &registration.capability_profiles {
        require_known_owner(&profile.local_system_id, local_system_ids)?;
        if !valid_contract_identity(&profile.contract)
            || !valid_capability_kind(&profile.kind)
            || !contracts.insert(profile.contract.as_str())
        {
            return Err(Status::invalid_argument(
                "capability profiles require unique contracts, known kinds, and one owner",
            ));
        }
        super::validate_scalar_map(&profile.attributes, "capability profile attributes")?;
    }
    Ok(())
}

/// Validates exact canonical operation support without exposing or inferring Local How.
fn validate_operation_support(
    registration: &crate::grpc::v0_4::NodeRegistration,
    local_system_ids: &BTreeSet<&str>,
) -> Result<(), Status> {
    if registration.node_contract_version != NODE_CONTRACT_VERSION {
        if !registration.operation_support.is_empty() {
            return Err(Status::invalid_argument(
                "Node Contracts before v0.6 cannot declare operation support",
            ));
        }
        return Ok(());
    }
    if registration.operation_support.is_empty() {
        return Err(Status::invalid_argument(
            "Node Contract v0.6 requires at least one supported operation",
        ));
    }
    let mut operations = BTreeSet::new();
    for support in &registration.operation_support {
        require_known_owner(&support.local_system_id, local_system_ids)?;
        let operation = support.operation.as_ref().ok_or_else(|| {
            Status::invalid_argument("operation support requires an OperationRef")
        })?;
        let identity = format!(
            "{}.{}@{}",
            operation.namespace, operation.name, operation.version
        );
        if !valid_contract_identity(&identity) || !operations.insert(identity) {
            return Err(Status::invalid_argument(
                "operation support requires unique canonical operation identities",
            ));
        }
    }
    Ok(())
}

/// Returns whether the transitional coarse kind is understood by current Control.
fn valid_capability_kind(kind: &str) -> bool {
    matches!(kind, "mobility" | "transport" | "compute" | "observation")
}

/// Validates one bounded State batch against the current registration snapshot.
pub(super) fn validate_state_observation_batch(
    batch: &crate::grpc::v0_4::StateObservationBatch,
    export_ids: &BTreeSet<String>,
) -> Result<(), Status> {
    const MAX_BATCH_RECORDS: usize = 64;
    const MAX_RECORD_BYTES: usize = 64 * 1024;
    const MAX_BATCH_BYTES: usize = 512 * 1024;

    if batch.observations.is_empty() || batch.observations.len() > MAX_BATCH_RECORDS {
        return Err(Status::invalid_argument(
            "State observation batch must contain 1 through 64 records",
        ));
    }
    let mut observed_exports = BTreeSet::new();
    let mut total_bytes = 0usize;
    for observation in &batch.observations {
        total_bytes = total_bytes.saturating_add(observation.json_value.len());
        if !export_ids.contains(&observation.export_id)
            || !observed_exports.insert(observation.export_id.as_str())
            || observation.json_value.len() > MAX_RECORD_BYTES
            || observation.has_confidence && observation.confidence_millionths > 1_000_000
            || serde_json::from_slice::<serde_json::Value>(&observation.json_value).is_err()
        {
            return Err(Status::invalid_argument(
                "State observations must reference unique registered exports and contain bounded valid JSON",
            ));
        }
    }
    if total_bytes > MAX_BATCH_BYTES {
        return Err(Status::invalid_argument(
            "State observation batch exceeds 512 KiB",
        ));
    }
    Ok(())
}

/// Validates the extensible `namespace.name@version` canonical identity shape.
fn valid_contract_identity(contract: &str) -> bool {
    contract
        .rsplit_once('@')
        .and_then(|(name, version)| name.rsplit_once('.').map(|parts| (parts, version)))
        .is_some_and(|((namespace, name), version)| {
            !namespace.trim().is_empty()
                && !name.trim().is_empty()
                && !version.trim().is_empty()
                && namespace
                    .split('.')
                    .all(|segment| !segment.is_empty() && !segment.chars().any(char::is_whitespace))
                && !namespace.contains('@')
                && !name.contains(['.', '@'])
                && !name.chars().any(char::is_whitespace)
                && !version.contains('@')
                && !version.chars().any(char::is_whitespace)
        })
}

/// Rejects a declaration whose owner is absent from the registration snapshot.
fn require_known_owner(owner: &str, known: &BTreeSet<&str>) -> Result<(), Status> {
    if owner.trim().is_empty() || !known.contains(owner) {
        return Err(Status::invalid_argument(
            "declaration references an unknown local system",
        ));
    }
    Ok(())
}
