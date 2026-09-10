//! Compiled execution connection, capability, workflow, resource, and sensor contracts.

use super::*;

impl CompiledConnection {
    /// Returns the stable connection identity.
    pub fn id(&self) -> &str {
        match self {
            Self::Http { id, .. } | Self::Grpc { id, .. } | Self::Mcp { id, .. } => id,
        }
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        match self {
            Self::Http { owner, .. } | Self::Grpc { owner, .. } | Self::Mcp { owner, .. } => owner,
        }
    }

    /// Returns the driver implementation family.
    pub const fn driver_kind(&self) -> DriverKind {
        match self {
            Self::Http { .. } => DriverKind::Http,
            Self::Grpc { .. } => DriverKind::Grpc,
            Self::Mcp { .. } => DriverKind::Mcp,
        }
    }

    /// Returns the fixed local endpoint.
    pub fn endpoint(&self) -> &str {
        match self {
            Self::Http { endpoint, .. }
            | Self::Grpc { endpoint, .. }
            | Self::Mcp { endpoint, .. } => endpoint,
        }
    }

    /// Renders one fixed operation with a dynamic request body only.
    pub(super) fn render_request(
        &self,
        operation: &LocalOperationConfig,
        payload: serde_json::Value,
    ) -> Result<CompiledDriverRequest, CatalogError> {
        match (self, operation) {
            (
                Self::Http {
                    endpoint,
                    timeout_ms,
                    credential_headers,
                    ..
                },
                LocalOperationConfig::Http { method, path },
            ) => Ok(CompiledDriverRequest::Http {
                endpoint: endpoint.clone(),
                method: method.clone(),
                path: path.clone(),
                credential_headers: credential_headers.clone(),
                body: payload,
                timeout_ms: *timeout_ms,
            }),
            (
                Self::Grpc {
                    endpoint,
                    descriptor_set,
                    reflection,
                    timeout_ms,
                    credential_metadata,
                    ..
                },
                LocalOperationConfig::GrpcUnary { service, method }
                | LocalOperationConfig::GrpcServerStream { service, method },
            ) => Ok(CompiledDriverRequest::Grpc {
                endpoint: endpoint.clone(),
                descriptor_set: descriptor_set.clone(),
                reflection: *reflection,
                service: service.clone(),
                method: method.clone(),
                server_streaming: matches!(
                    operation,
                    LocalOperationConfig::GrpcServerStream { .. }
                ),
                credential_metadata: credential_metadata.clone(),
                message: payload,
                timeout_ms: *timeout_ms,
            }),
            (
                Self::Mcp {
                    endpoint,
                    timeout_ms,
                    credential_headers,
                    ..
                },
                LocalOperationConfig::McpTool { tool },
            ) => Ok(CompiledDriverRequest::Mcp {
                endpoint: endpoint.clone(),
                tool: tool.clone(),
                credential_headers: credential_headers.clone(),
                arguments: payload,
                timeout_ms: *timeout_ms,
            }),
            _ => Err(validation(
                "workflow.operation",
                "operation driver does not match its compiled connection",
            )),
        }
    }
}

impl CompiledCapability {
    /// Returns the canonical capability contract.
    pub fn contract(&self) -> &str {
        &self.contract
    }

    /// Returns the coarse capability kind consumed by Control Matching.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the sole local-system owner.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns resource identities that must be present in Control's commitment.
    pub const fn required_resources(&self) -> &BTreeSet<String> {
        &self.required_resources
    }

    /// Returns node-local concurrency lock identities.
    pub const fn local_locks(&self) -> &BTreeSet<String> {
        &self.local_locks
    }

    /// Returns the artifact action fixed for this capability, when configured.
    pub const fn artifact_operation(&self) -> Option<ArtifactOperationConfig> {
        self.artifact_operation
    }

    /// Returns the exact-contract readiness observation when configured.
    pub const fn readiness(&self) -> Option<&CompiledCapabilityReadiness> {
        self.readiness.as_ref()
    }

    /// Returns immutable local execution behavior.
    pub const fn workflow(&self) -> &CompiledWorkflow {
        &self.workflow
    }
}

impl CompiledWorkflow {
    /// Returns physical/computational dispatch steps.
    pub fn execute(&self) -> &[CompiledWorkflowStep] {
        &self.execute
    }

    /// Returns status/reconciliation steps.
    pub fn status(&self) -> &[CompiledWorkflowStep] {
        &self.status
    }

    /// Returns cancellation-request steps.
    pub fn cancel(&self) -> &[CompiledWorkflowStep] {
        &self.cancel
    }

    /// Returns the configured status polling interval.
    pub const fn poll_interval_ms(&self) -> u64 {
        self.poll_interval_ms
    }

    /// Extracts the local execution handle from completed dispatch responses.
    pub fn local_handle(&self, context: &WorkflowContext) -> Result<String, MappingError> {
        evaluate(&self.local_handle, context.as_json())?
            .as_str()
            .filter(|handle| !handle.trim().is_empty())
            .map(ToString::to_string)
            .ok_or_else(|| MappingError::InvalidFunctionArguments("local_handle".to_string()))
    }

    /// Projects current local state without treating cancel acknowledgement as terminal.
    pub fn map_execution_state(
        &self,
        context: &WorkflowContext,
    ) -> Result<MappedExecutionFact, MappingError> {
        self.execution_state.map(context)
    }

    /// Returns whether the compiled state map covers every non-ambiguous lifecycle phase.
    pub fn execution_state_mapped(&self) -> bool {
        [
            MappedExecutionPhase::Accepted,
            MappedExecutionPhase::Running,
            MappedExecutionPhase::Completed,
            MappedExecutionPhase::Failed,
            MappedExecutionPhase::Cancelled,
        ]
        .iter()
        .all(|phase| {
            self.execution_state
                .states
                .values()
                .any(|mapped| mapped == phase)
        })
    }
}

impl CompiledWorkflowStep {
    /// Returns the stable workflow-local step identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the fixed connection identity.
    pub fn connection(&self) -> &str {
        &self.connection
    }

    /// Returns the fixed local operation selected by this workflow step.
    pub const fn operation(&self) -> &LocalOperationConfig {
        &self.operation
    }

    /// Renders one driver request while preserving compiled route and operation authority.
    pub fn render(
        &self,
        catalog: &CompiledLocalCatalog,
        context: &WorkflowContext,
    ) -> Result<CompiledDriverRequest, CatalogError> {
        let connection = catalog.connections.get(&self.connection).ok_or_else(|| {
            validation(
                "workflow.connection",
                format!("compiled connection `{}` is unavailable", self.connection),
            )
        })?;
        let payload = self
            .request
            .render(context)
            .map_err(|source| CatalogError::Mapping {
                step: self.id.clone(),
                source,
            })?;
        connection.render_request(&self.operation, payload)
    }
}

impl CompiledExecutionStateMapping {
    /// Maps configured local state and optional reason into one canonical fact.
    pub(super) fn map(
        &self,
        context: &WorkflowContext,
    ) -> Result<MappedExecutionFact, MappingError> {
        let state = context
            .as_json()
            .pointer(&self.state_pointer)
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| MappingError::MissingSource(self.state_pointer.clone()))?;
        let normalized = if self.case_sensitive {
            state.to_string()
        } else {
            state.to_ascii_lowercase()
        };
        let phase = self
            .states
            .get(&normalized)
            .copied()
            .ok_or_else(|| MappingError::MissingSource(format!("unmapped state `{state}`")))?;
        let reason = self
            .reason_pointer
            .as_ref()
            .and_then(|pointer| context.as_json().pointer(pointer).map(value_to_reason));
        Ok(MappedExecutionFact { phase, reason })
    }
}

impl CompiledResource {
    /// Returns the stable resource identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the transport-neutral resource kind.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns non-zero resource capacity.
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

impl CompiledSensor {
    /// Returns the stable sensor identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the transport-neutral sensor kind.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}
