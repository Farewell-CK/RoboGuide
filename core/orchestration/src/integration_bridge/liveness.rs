//! Lease, route, and peer-readiness liveness handling.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Applies time-driven lease and coordination expiry using existing Control/Runtime authority.
    pub fn tick(
        &mut self,
        now: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<Vec<NodeId>, IntegrationRuntimeError> {
        self.runtime.refresh_peer_channel_deadlines(now);
        if self.restored_recovery_pending {
            self.restored_recovery_pending = false;
            for attempt in self.runtime.attempt_history() {
                if attempt.status() != ExecutionStatus::Unknown
                    || self.runtime.current_attempt_id(
                        attempt.command().group_id(),
                        attempt.command().task_ref(),
                        attempt.command().role_id(),
                    ) != Some(attempt.execution_id())
                {
                    continue;
                }
                let event = ExecutionEvent::RecoveryRequired {
                    execution_id: attempt.execution_id().to_string(),
                    node_id: attempt.command().node_id().clone(),
                    context: Some(attempt.command().clone()),
                    reason: "Controller restart requires physical attempt reconciliation"
                        .to_string(),
                };
                append_runtime_evidence(&mut self.events, &event, now, correlation_id);
                self.runtime_events.push_back(event);
            }
        }
        let expired =
            self.control
                .expire_leases(&mut self.state, now, correlation_id, &mut self.events)?;
        for node_id in &expired {
            self.runtime.fence_peer_channels_for_node(node_id);
            self.observe_node_unavailable(
                node_id,
                "Controller application timer expired the Node lease",
                now,
                correlation_id,
            );
        }
        Ok(expired)
    }

    /// Queues Runtime recovery facts after one Node loses current execution authority.
    pub(super) fn observe_node_unavailable(
        &mut self,
        node_id: &NodeId,
        reason: &str,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
    ) {
        for event in self.runtime.observe_node_unavailable(node_id, reason) {
            append_runtime_evidence(&mut self.events, &event, timestamp, correlation_id);
            self.runtime_events.push_back(event);
        }
    }

    /// Validates an identified Local EAIOS acknowledgement against current Group ownership.
    pub(super) fn consume_peer_channel_readiness(
        &mut self,
        node_id: &str,
        session_id: &str,
        readiness: integration::grpc::v0_4::PeerChannelReadiness,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        if readiness.session_id != session_id {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness session does not match the admitted Node route".to_string(),
            ));
        }
        let group_id = domain::ExecutionGroupId::new(readiness.group_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let context_id = domain::CoordinationContextId::new(readiness.context_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let context_role_id = domain::ContextRoleId::new(readiness.context_role_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let local_system_id = LocalSystemId::new(readiness.local_system_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let node_id = NodeId::new(node_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        if readiness.valid_for_ms == 0 || readiness.valid_for_ms > 60_000 {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness validity must be between 1 and 60000 milliseconds".to_string(),
            ));
        }
        let registration = self
            .state
            .node(&node_id)
            .map(domain::NodeStateSnapshot::registration)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "peer readiness Node has no current registration".to_string(),
                )
            })?;
        let group = self.control.group(&group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("peer readiness Group is unknown".to_string())
        })?;
        let owns_role = group.task_executions().any(|execution| {
            execution.context_id() == &context_id
                && matches!(
                    execution.lifecycle(),
                    domain::TaskExecutionLifecycle::Ready | domain::TaskExecutionLifecycle::Active
                )
                && execution.assignments().iter().any(|assignment| {
                    assignment.node_id() == &node_id
                        && execution.context_role(assignment.role_id()) == Some(&context_role_id)
                        && group
                            .role_requirement(execution.task_ref(), assignment.role_id())
                            .and_then(domain::RoleRequirement::required_contract)
                            .and_then(|contract| registration.capability_owner(contract))
                            == Some(&local_system_id)
                })
        });
        if !owns_role {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness Node/Local EAIOS does not own the ContextRole binding".to_string(),
            ));
        }
        let evidence = PeerChannelReadinessEvidence {
            group_id,
            context_id,
            context_role_id,
            node_id,
            local_system_id,
            session_id: session_id.to_string(),
            channel_instance_id: readiness.channel_instance_id,
            profile_id: readiness.profile_id,
            message_schema: readiness.message_schema,
            sequence: readiness.sequence,
            received_at,
            expires_at: TimestampMs::new(
                received_at
                    .as_millis()
                    .checked_add(readiness.valid_for_ms)
                    .ok_or_else(|| {
                        IntegrationRuntimeError::Protocol(
                            "peer readiness deadline overflowed".to_string(),
                        )
                    })?,
            ),
            ready: readiness.ready,
        };
        self.runtime
            .observe_peer_channel_readiness(evidence.clone())
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        self.events.append(
            received_at,
            correlation_id,
            None,
            EventPayload::PeerChannelReadinessObserved {
                group_id: evidence.group_id,
                context_id: evidence.context_id,
                context_role_id: evidence.context_role_id,
                node_id: evidence.node_id,
                local_system_id: evidence.local_system_id,
                session_id: evidence.session_id,
                channel_instance_id: evidence.channel_instance_id,
                profile_id: evidence.profile_id,
                message_schema: evidence.message_schema,
                sequence: evidence.sequence,
                expires_at: evidence.expires_at,
                ready: evidence.ready,
            },
        );
        Ok(())
    }
}
