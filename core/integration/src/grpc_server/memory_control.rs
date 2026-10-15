//! Negotiated transport for already committed pre-execution Memory authority.

use super::*;

impl GrpcNodeRouter {
    /// Sends current-use authority only after quorum, certificate and exact original-session checks.
    pub fn send_memory_use_control(
        &self,
        node_id: &str,
        command: &domain::MemoryUseControl,
        remaining_budget_ms: u64,
    ) -> Result<(), Status> {
        command.validate().map_err(Status::invalid_argument)?;
        if command.operation == domain::MemoryUseOperation::Grant
            && (remaining_budget_ms == 0 || remaining_budget_ms > command.valid_for_ms)
        {
            return Err(Status::invalid_argument(
                "Memory-use original budget is unavailable",
            ));
        }
        let command_json =
            serde_json::to_string(command).map_err(|e| Status::invalid_argument(e.to_string()))?;
        self.send_preparation_payload(node_id, |route, controller_epoch| {
            if route.node_contract_version != MEMORY_USE_NODE_CONTRACT_VERSION
                || command.node_id.as_str() != node_id
                || (command.operation == domain::MemoryUseOperation::Grant
                    && command.source_session_id != route.session_id)
            {
                return Err(Status::failed_precondition(
                    "current Memory-use requires the original v0.10 Node/session",
                ));
            }
            Ok(ServerPayload::MemoryUseControl(
                crate::grpc::v0_4::MemoryUseControl {
                    session_id: route.session_id.clone(),
                    command_json,
                    controller_epoch,
                    valid_for_ms: remaining_budget_ms,
                },
            ))
        })
    }
    /// Sends metadata only after current quorum, identity, certificate, registration and lease checks.
    fn send_preparation_payload(
        &self,
        node_id: &str,
        build: impl FnOnce(&RoutedSession, String) -> Result<ServerPayload, Status>,
    ) -> Result<(), Status> {
        self.check_command_delivery()?;
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        let route = sessions
            .get(node_id)
            .ok_or_else(|| Status::unavailable("node is not connected"))?;
        self.validate_identity(node_id, &route.session_id)?;
        self.validate_certificate_expiry(route)?;
        if !crate::grpc::v0_4::supports_memory_preparation(&route.node_contract_version) {
            return Err(Status::failed_precondition(
                "prepared Memory commands require Node Contract v0.9",
            ));
        }
        if !route.active || route.last_heartbeat.elapsed() >= route.lease_duration {
            return Err(Status::unavailable(
                "prepared Memory route is pending or expired",
            ));
        }
        let payload = build(route, self.controller_epoch()?)?;
        route
            .sender
            .send(Ok(ServerMessage {
                message: Some(payload),
            }))
            .map_err(|_| Status::unavailable("node session closed"))
    }

    /// Sends one application-committed prepare/probe/revoke command without interpreting its outcome.
    pub fn send_memory_control(
        &self,
        node_id: &str,
        command: &domain::MemoryControlCommand,
        remaining_budget_ms: u64,
    ) -> Result<(), Status> {
        command.validate().map_err(Status::invalid_argument)?;
        if command.operation != domain::MemoryControlKind::Revoke
            && !(1..=120_000).contains(&remaining_budget_ms)
        {
            return Err(Status::invalid_argument(
                "Memory control original budget is unavailable",
            ));
        }
        let command_json =
            serde_json::to_string(command).map_err(|e| Status::invalid_argument(e.to_string()))?;
        if command_json.len() > 256 * 1024 {
            return Err(Status::invalid_argument(
                "Memory preparation metadata exceeds its bound",
            ));
        }
        self.send_preparation_payload(node_id, |route, controller_epoch| {
            if command.preparation.as_ref().is_some_and(|p| {
                p.node_id.as_str() != node_id || p.source_session_id != route.session_id
            }) {
                return Err(Status::failed_precondition(
                    "Memory preparation belongs to another original Node session",
                ));
            }
            Ok(ServerPayload::MemoryControl(
                crate::grpc::v0_4::MemoryControl {
                    session_id: route.session_id.clone(),
                    command_json,
                    controller_epoch,
                    valid_for_ms: remaining_budget_ms,
                },
            ))
        })
    }

    /// Sends exact prepared import content without dropping original typed-map provenance.
    pub fn send_prepared_memory_import(
        &self,
        node_id: &str,
        mut command: crate::grpc::v0_4::MemoryImport,
    ) -> Result<(), Status> {
        if command.memory_preparation_id.is_empty()
            || command.memory_preparation_id.len() > 128
            || command.command_id != format!("memory-import-{}", command.import_id)
            || command.manifest_json.len() > MAX_MEMORY_IMPORT_MANIFEST_BYTES
            || command.typed_map_manifest_json.len() > MAX_MEMORY_IMPORT_MANIFEST_BYTES
        {
            return Err(Status::invalid_argument(
                "prepared import identity or metadata bounds are invalid",
            ));
        }
        self.send_preparation_payload(node_id, |route, controller_epoch| {
            command.session_id = route.session_id.clone();
            command.controller_epoch = controller_epoch;
            command.authorization_digest = prepared_memory_import_digest(&command);
            Ok(ServerPayload::MemoryImport(command))
        })
    }

    /// Sends Execute only with the exact already admitted preparation and immutable invocation.
    pub fn execute_with_preparation(
        &self,
        node_id: &str,
        mut command: crate::grpc::v0_4::Execute,
    ) -> Result<(), Status> {
        if command.memory_preparation_id.is_empty()
            || command.memory_preparation_id.len() > 128
            || command.memory_preparation_digest.len() != 71
            || command.command_id != format!("dispatch-{}", command.execution_id)
            || command.resource_ids.len() > 64
            || command.resource_ids.iter().any(|id| id.trim().is_empty())
            || command.resource_ids.iter().collect::<BTreeSet<_>>().len()
                != command.resource_ids.len()
        {
            return Err(Status::invalid_argument(
                "prepared Execute lacks exact context or resources",
            ));
        }
        let invocation = command
            .invocation
            .take()
            .ok_or_else(|| Status::invalid_argument("prepared Execute lacks invocation"))?;
        if [
            &invocation.mission_id,
            &invocation.group_id,
            &invocation.task_id,
            &invocation.role_id,
        ]
        .iter()
        .any(|id| id.trim().is_empty())
        {
            return Err(Status::invalid_argument(
                "prepared Execute invocation identity is empty",
            ));
        }
        self.send_preparation_payload(node_id, |route, controller_epoch| {
            if !command.execution_session_json.is_empty() && !route.supports_execution_session {
                return Err(Status::failed_precondition(
                    "prepared Execute session was not negotiated",
                ));
            }
            command.invocation = Some(invocation_for_contract(
                invocation,
                &route.node_contract_version,
            )?);
            command.session_id = route.session_id.clone();
            command.controller_epoch = controller_epoch;
            Ok(ServerPayload::Execute(command))
        })
    }
}

/// Retains original legacy digest encoding while binding new prepared/typed metadata when present.
pub fn prepared_memory_import_digest(command: &crate::grpc::v0_4::MemoryImport) -> String {
    let legacy = memory_import_authorization_digest(
        &command.import_id,
        &command.command_id,
        &command.consumer_provider_id,
        &command.manifest_json,
        &command.mission_id,
        &command.group_id,
        &command.task_id,
        &command.role_id,
        &command.execution_id,
        &command.controller_epoch,
    );
    if command.memory_preparation_id.is_empty() && command.typed_map_manifest_json.is_empty() {
        return legacy;
    }
    let tuple = serde_json::json!({
        "schema":"roboguide.prepared-memory-import/v0.1", "original_digest":legacy,
        "preparation_id":command.memory_preparation_id, "typed_map_manifest_json":command.typed_map_manifest_json,
    });
    format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&tuple).expect("JSON values serialize"))
    )
}
