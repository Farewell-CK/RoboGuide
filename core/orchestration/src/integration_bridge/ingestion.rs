//! Bridge construction, checkpointing, and protocol event ingestion.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Creates the composition bridge around existing core authorities.
    pub fn new(
        control: ControlPlane,
        state: InMemorySharedNodeState,
        events: E,
        router: GrpcNodeRouter,
    ) -> Self {
        Self {
            control,
            state,
            state_records: StateRecordProjection::new(),
            events,
            router,
            runtime: RuntimeExecutionManager::new(),
            runtime_events: VecDeque::new(),
            restored_recovery_pending: false,
        }
    }

    /// Serializes a versioned checkpoint of Control, Shared State, and Runtime projections.
    pub fn checkpoint_json(&self) -> Result<String, IntegrationRuntimeError> {
        let checkpoint = ControllerCheckpoint {
            schema: CONTROLLER_CHECKPOINT_SCHEMA.to_string(),
            control: self.control.checkpoint(),
            nodes: self.state.snapshots(),
            state_records: self.state_records.snapshots(),
            runtime: self.runtime.checkpoint(),
        };
        serde_json::to_string(&checkpoint)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))
    }

    /// Restores one versioned checkpoint with fresh routes and conservative process-local facts.
    ///
    /// Leases are cleared by Control restore, node liveness is reset by State restore, and every
    /// nonterminal execution becomes Unknown. This method never routes or replays a command.
    pub fn restore_from_checkpoint(
        checkpoint_json: &str,
        events: E,
        router: GrpcNodeRouter,
        restored_at: TimestampMs,
    ) -> Result<Self, IntegrationRuntimeError> {
        let checkpoint: ControllerCheckpoint = serde_json::from_str(checkpoint_json)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        if !matches!(
            checkpoint.schema.as_str(),
            CONTROLLER_CHECKPOINT_SCHEMA | PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA
        ) {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "unsupported controller checkpoint schema {}",
                checkpoint.schema
            )));
        }
        let control = ControlPlane::restore(checkpoint.control)?;
        let state = InMemorySharedNodeState::restore(checkpoint.nodes, restored_at)
            .map_err(IntegrationRuntimeError::Checkpoint)?;
        let state_records = StateRecordProjection::restore(checkpoint.state_records)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        let runtime = RuntimeExecutionManager::restore(checkpoint.runtime)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        Ok(Self {
            control,
            state,
            state_records,
            events,
            router,
            runtime,
            runtime_events: VecDeque::new(),
            restored_recovery_pending: true,
        })
    }

    /// Consumes one validated Integration Server event into Control, State, and Runtime evidence.
    pub fn consume(
        &mut self,
        event: GrpcNodeEvent,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        self.runtime.refresh_peer_channel_deadlines(received_at);
        match event {
            GrpcNodeEvent::Registered {
                lease_id,
                registration,
                ..
            } => {
                let registration = registration_from_wire(registration)?;
                let node_id = registration.node_id().clone();
                let lease = NodeLease::new(
                    LeaseId::new(lease_id)?,
                    node_id.clone(),
                    received_at,
                    15_000,
                )?;
                self.control.register_node_with_lease(
                    &mut self.state,
                    registration,
                    NodeStatus::new(NodeHealth::Offline, received_at),
                    lease,
                    received_at,
                    correlation_id,
                    &mut self.events,
                )?;
                self.runtime.fence_peer_channels_for_node(&node_id);
            }
            GrpcNodeEvent::NodeMessage {
                node_id,
                session_id,
                message,
            } => {
                // Direct composition callers may replay a protocol fact without a live gRPC
                // route.  When a route exists, however, the session fence is authoritative and
                // stale or expired sessions must be ignored.
                let current = if self
                    .router
                    .has_session(&node_id)
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                {
                    self.router
                        .session_is_current(&node_id, &session_id)
                        .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                } else {
                    true
                };
                if !current {
                    return Ok(());
                }
                match message.message {
                    Some(NodePayload::Heartbeat(heartbeat)) => {
                        let node_id = NodeId::new(node_id)?;
                        let lease_id = LeaseId::new(heartbeat.lease_id)?;
                        let status = status_from_wire(heartbeat.status.as_ref(), received_at)?;
                        self.control.accept_heartbeat(
                            &mut self.state,
                            NodeHeartbeat::new(node_id, lease_id, status),
                            received_at,
                            15_000,
                            correlation_id,
                            &mut self.events,
                        )?;
                    }
                    Some(NodePayload::ExecutionEvent(event)) => self.consume_execution(
                        ReceivedExecutionFact {
                            node_id: &node_id,
                            execution_id: &event.execution_id,
                            sequence: event.sequence,
                            phase: event.phase,
                            reason: &event.reason,
                        },
                        received_at,
                        correlation_id,
                    )?,
                    Some(NodePayload::ExecutionSnapshot(snapshot)) => self.consume_execution(
                        ReceivedExecutionFact {
                            node_id: &node_id,
                            execution_id: &snapshot.execution_id,
                            sequence: snapshot.last_sequence,
                            phase: snapshot.phase,
                            reason: &snapshot.reason,
                        },
                        received_at,
                        correlation_id,
                    )?,
                    Some(NodePayload::RegistrationUpdate(update)) => {
                        let registration =
                            registration_from_wire(update.registration.ok_or_else(|| {
                                IntegrationRuntimeError::Protocol(
                                    "registration update is empty".to_string(),
                                )
                            })?)?;
                        self.control.update_node_registration(
                            &mut self.state,
                            registration,
                            received_at,
                            correlation_id,
                            &mut self.events,
                        )?;
                        // A complete registration snapshot may change the LocalSystem that owns
                        // a committed capability. Require fresh endpoint proof under the new
                        // snapshot instead of retaining readiness admitted under the old one.
                        let node_id = NodeId::new(&node_id)?;
                        self.runtime.fence_peer_channels_for_node(&node_id);
                    }
                    Some(NodePayload::StateObservationBatch(batch)) => {
                        self.consume_state_observations(
                            &node_id,
                            &session_id,
                            batch.sequence,
                            batch.observations,
                            received_at,
                            correlation_id,
                        )?;
                    }
                    Some(NodePayload::PeerChannelReadiness(readiness)) => {
                        self.consume_peer_channel_readiness(
                            &node_id,
                            &session_id,
                            readiness,
                            received_at,
                            correlation_id,
                        )?;
                    }
                    Some(NodePayload::CommandReceipt(receipt)) => {
                        self.consume_command_receipt(
                            &node_id,
                            &session_id,
                            receipt,
                            received_at,
                            correlation_id,
                        )?;
                    }
                    _ => {}
                }
            }
            GrpcNodeEvent::Unavailable {
                node_id,
                session_id: _,
            } => {
                // A delayed disconnect from an old route must never make a newer session
                // appear unreachable.
                if self
                    .router
                    .has_session(&node_id)
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                {
                    return Ok(());
                }
                let node_id_value = NodeId::new(&node_id)?;
                self.state.record_node_liveness(
                    &node_id_value,
                    domain::NodeLivenessObservation::new(
                        domain::NodeLiveness::Unreachable,
                        received_at,
                    ),
                )?;
                self.runtime.fence_peer_channels_for_node(&node_id_value);
                self.observe_node_unavailable(
                    &node_id_value,
                    "Node Protocol route became unavailable",
                    received_at,
                    correlation_id,
                );
            }
        }
        Ok(())
    }
}
