//! Read-only composition of existing Control, State and Runtime for Memory prerequisites.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Selects an actual registered receiver using Control and the same source-aware decision view.
    pub fn select_memory_provider(
        &self,
        node: &NodeId,
        requirement: &domain::MemoryRequirement,
        operation: &domain::OperationRef,
        now: TimestampMs,
    ) -> Result<domain::MemoryProviderDescriptor, IntegrationRuntimeError> {
        self.control
            .select_memory_provider(&self.decision_state(), node, requirement, operation, now)
            .map_err(|e| IntegrationRuntimeError::Protocol(e.to_string()))
    }

    /// Revalidates current source permission and exact attempt without changing any lifecycle.
    pub fn validate_memory_preparation(
        &self,
        preparation: &domain::MemoryPreparation,
        now: TimestampMs,
    ) -> Result<(), IntegrationRuntimeError> {
        self.validate_memory_execution_context(preparation)?;
        self.control
            .validate_memory_preparation(&self.decision_state(), preparation, now)
            .map_err(|e| IntegrationRuntimeError::Protocol(e.to_string()))
    }

    /// Requires the original attempt and current Read authority independently of import admission.
    pub fn validate_memory_use_context(
        &self,
        preparation: &domain::MemoryPreparation,
        now: TimestampMs,
    ) -> Result<(), IntegrationRuntimeError> {
        self.validate_memory_execution_context(preparation)?;
        self.control
            .validate_memory_use_context(&self.decision_state(), preparation, now)
            .map_err(|e| IntegrationRuntimeError::Protocol(e.to_string()))
    }

    /// Retains a single Runtime/registration/invocation check for both permission lifetimes.
    fn validate_memory_execution_context(
        &self,
        preparation: &domain::MemoryPreparation,
    ) -> Result<(), IntegrationRuntimeError> {
        if self.current_task_attempt_id(
            &preparation.group_id,
            &preparation.task_ref,
            &preparation.role_id,
        ) != Some(preparation.execution_id.as_str())
        {
            return Err(IntegrationRuntimeError::Protocol(
                "Memory context belongs to another current physical attempt".into(),
            ));
        }
        let command = self
            .runtime
            .execution_command(&preparation.execution_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "Memory context has no original Runtime command".into(),
                )
            })?;
        let resources = self
            .runtime
            .execution_resources(&preparation.execution_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "Memory context has no original committed resources".into(),
                )
            })?;
        if !self.dispatch_registration_is_current(command)
            || Self::memory_invocation_json(command, &resources)? != preparation.invocation
        {
            return Err(IntegrationRuntimeError::Protocol(
                "Memory context changed its original dispatch declaration or invocation".into(),
            ));
        }
        Ok(())
    }

    /// Encodes semantic invocation metadata, independently of vendor requests or Local How paths.
    pub fn memory_invocation_json(
        command: &ExecutionCommand,
        resources: &[domain::ResourceId],
    ) -> Result<serde_json::Value, IntegrationRuntimeError> {
        let mut parameters = serde_json::Map::new();
        for (name, value) in command.intent().parameters() {
            let value = match value {
                domain::ExecutionValue::Bool(value) => serde_json::Value::Bool(*value),
                domain::ExecutionValue::Integer(value) => serde_json::json!(value),
                domain::ExecutionValue::Float(value) => serde_json::Number::from_f64(*value)
                    .map(serde_json::Value::Number)
                    .ok_or_else(|| {
                        IntegrationRuntimeError::Protocol(
                            "Memory invocation contains non-finite parameters".into(),
                        )
                    })?,
                domain::ExecutionValue::String(value) => serde_json::Value::String(value.clone()),
            };
            parameters.insert(name.clone(), value);
        }
        let mut resources = resources
            .iter()
            .map(domain::ResourceId::as_str)
            .collect::<Vec<_>>();
        resources.sort();
        resources.dedup();
        let mut value = serde_json::json!({
            "mission_id":command.mission_id(),"group_id":command.group_id(),"task_id":command.task_id(),
            "role_id":command.role_id(),"operation":command.intent().operation().to_string(),
            "objective":command.intent().objective(),"parameters":parameters,"resource_ids":resources,
        });
        if let Some(session) = command.session() {
            value
                .as_object_mut()
                .expect("locally encoded invocation object")
                .insert(
                    "execution_session".into(),
                    serde_json::to_value(session)
                        .map_err(|e| IntegrationRuntimeError::Protocol(e.to_string()))?,
                );
        }
        Ok(value)
    }

    /// Delivers only application-committed metadata under the current transport epoch and identity.
    pub fn deliver_memory_control(
        &self,
        node: &NodeId,
        command: &domain::MemoryControlCommand,
        remaining_budget_ms: u64,
    ) -> Result<(), IntegrationRuntimeError> {
        self.router
            .send_memory_control(node.as_str(), command, remaining_budget_ms)
            .map_err(IntegrationRuntimeError::Route)
    }

    /// Sends an already committed use intent without granting permission in the transport layer.
    pub fn deliver_memory_use_control(
        &self,
        node: &NodeId,
        command: &domain::MemoryUseControl,
        remaining_budget_ms: u64,
    ) -> Result<(), IntegrationRuntimeError> {
        self.router
            .send_memory_use_control(node.as_str(), command, remaining_budget_ms)
            .map_err(IntegrationRuntimeError::Route)
    }
}
