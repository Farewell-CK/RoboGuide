//! Execution, command-receipt, and State-observation fact reduction.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Converts execution facts into Runtime evidence and terminal NodeEvent values.
    pub(super) fn consume_execution(
        &mut self,
        fact: ReceivedExecutionFact<'_>,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let phase = ExecutionPhase::try_from(fact.phase).map_err(|_| {
            IntegrationRuntimeError::Protocol("unknown execution phase".to_string())
        })?;
        let node_id = NodeId::new(fact.node_id)?;
        let runtime_status = match phase {
            ExecutionPhase::Accepted => ExecutionStatus::Accepted,
            ExecutionPhase::Started => ExecutionStatus::Running,
            ExecutionPhase::Completed => ExecutionStatus::Completed,
            ExecutionPhase::Failed => ExecutionStatus::Failed,
            ExecutionPhase::Cancelled => ExecutionStatus::Cancelled,
            ExecutionPhase::Unknown | ExecutionPhase::Unspecified => ExecutionStatus::Unknown,
        };
        let runtime_events = self
            .runtime
            .observe_execution(
                fact.execution_id,
                node_id.clone(),
                fact.sequence,
                runtime_status,
                fact.reason,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Applies one Node command receipt to the durable Runtime dispatch intent.
    pub(super) fn consume_command_receipt(
        &mut self,
        node_id: &str,
        session_id: &str,
        receipt: integration::grpc::v0_4::CommandReceipt,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        if receipt.session_id != session_id {
            return Err(IntegrationRuntimeError::Protocol(
                "command receipt session does not match the admitted Node route".to_string(),
            ));
        }
        let kind = integration::grpc::v0_4::CommandKind::try_from(receipt.kind).map_err(|_| {
            IntegrationRuntimeError::Protocol("command receipt kind is invalid".to_string())
        })?;
        if kind == integration::grpc::v0_4::CommandKind::Unspecified {
            return Err(IntegrationRuntimeError::Protocol(
                "command receipt kind is unspecified".to_string(),
            ));
        }
        let node_id = NodeId::new(node_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let status = integration::grpc::v0_4::CommandReceiptStatus::try_from(receipt.status)
            .map_err(|_| {
                IntegrationRuntimeError::Protocol("command receipt status is invalid".to_string())
            })?;
        if status == integration::grpc::v0_4::CommandReceiptStatus::Unspecified {
            return Err(IntegrationRuntimeError::Protocol(
                "command receipt status is unspecified".to_string(),
            ));
        }
        if kind == integration::grpc::v0_4::CommandKind::CommandCancel {
            let runtime_events = self
                .runtime
                .observe_cancellation_receipt(
                    &receipt.execution_id,
                    &receipt.command_id,
                    &node_id,
                    status == integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted,
                    receipt.reason,
                )
                .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
            for event in runtime_events {
                append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
                self.runtime_events.push_back(event);
            }
            return Ok(());
        }
        let persisted = match status {
            integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted => true,
            integration::grpc::v0_4::CommandReceiptStatus::CommandRejected => false,
            integration::grpc::v0_4::CommandReceiptStatus::Unspecified => {
                return Err(IntegrationRuntimeError::Protocol(
                    "command receipt status is unspecified".to_string(),
                ));
            }
        };
        let runtime_events = self
            .runtime
            .observe_dispatch_receipt(
                &receipt.execution_id,
                &receipt.command_id,
                &node_id,
                persisted,
                receipt.reason,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Converts one accepted protocol batch into an atomic State projection update and evidence.
    pub(super) fn consume_state_observations(
        &mut self,
        node_id: &str,
        session_id: &str,
        sequence: u64,
        observations: Vec<integration::grpc::v0_4::StateObservation>,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let node_id = NodeId::new(node_id)?;
        let registration = self
            .state
            .node(&node_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "State observations require a registered node".to_string(),
                )
            })?
            .registration();
        let exports = registration
            .state_exports()
            .iter()
            .map(|export| (export.export_id(), export))
            .collect::<BTreeMap<_, _>>();
        let records = observations
            .into_iter()
            .map(|observation| {
                let export = exports.get(observation.export_id.as_str()).ok_or_else(|| {
                    IntegrationRuntimeError::Protocol(format!(
                        "State observation references undeclared export {}",
                        observation.export_id
                    ))
                })?;
                let value = serde_json::from_slice(&observation.json_value).map_err(|error| {
                    IntegrationRuntimeError::Protocol(format!(
                        "State observation JSON is invalid: {error}"
                    ))
                })?;
                StateRecord::new_with_source_epoch(
                    export.object().clone(),
                    export.semantic(),
                    StateSource::Node {
                        node_id: node_id.clone(),
                        local_system_id: export.local_system_id().clone(),
                    },
                    export.export_id(),
                    export.payload_schema(),
                    value,
                    observation
                        .has_source_observed_at
                        .then(|| TimestampMs::new(observation.source_observed_at_ms)),
                    received_at,
                    export.valid_for_ms(),
                    observation
                        .has_confidence
                        .then_some(observation.confidence_millionths),
                    Some(session_id.to_string()),
                    sequence,
                )
                .map_err(Into::into)
            })
            .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
        let mut candidate = self.state_records.clone();
        for record in &records {
            candidate.record_state(record.clone())?;
        }
        self.state_records = candidate;
        for record in records {
            self.events.append(
                received_at,
                correlation_id,
                None,
                EventPayload::StateRecordObserved { record },
            );
        }
        Ok(())
    }
}
