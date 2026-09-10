//! Compiled health, State, peer-channel, and Memory observation contracts.

use super::*;

impl CompiledStateExport {
    /// Returns the node-wide export identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the local-system owner.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns the State object class spelling.
    pub fn object_class(&self) -> &str {
        &self.object_class
    }

    /// Returns the domain-specific object category.
    pub fn object_type(&self) -> &str {
        &self.object_type
    }

    /// Returns the stable object identity.
    pub fn object_id(&self) -> &str {
        &self.object_id
    }

    /// Returns the State semantic spelling.
    pub fn semantic(&self) -> &str {
        &self.semantic
    }

    /// Returns the JSON value schema.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the receive-relative validity period.
    pub const fn valid_for_ms(&self) -> u64 {
        self.valid_for_ms
    }

    /// Returns the configured sample interval.
    pub const fn interval_ms(&self) -> u64 {
        self.interval_ms
    }

    /// Returns the fixed local sampling operation.
    pub const fn step(&self) -> &CompiledWorkflowStep {
        &self.step
    }

    /// Maps one completed local response into a bounded source-aware State fact.
    pub fn map(&self, context: &WorkflowContext) -> Result<StateExportFact, MappingError> {
        let response_pointer = format!("/steps/{}", escape_pointer_segment(self.step.id()));
        let response = context
            .as_json()
            .pointer(&response_pointer)
            .ok_or(MappingError::MissingSource(response_pointer))?;
        let value = response
            .pointer(&self.value_pointer)
            .cloned()
            .ok_or_else(|| MappingError::MissingSource(self.value_pointer.clone()))?;
        if serde_json::to_vec(&value)
            .map_err(|_| MappingError::InvalidFunctionArguments("state JSON".to_string()))?
            .len()
            > MAX_PEER_CHANNEL_OBSERVATION_BYTES
        {
            return Err(MappingError::InvalidFunctionArguments(
                "state payload exceeds 64 KiB".to_string(),
            ));
        }
        let source_observed_at_ms = self
            .source_observed_at_pointer
            .as_ref()
            .map(|pointer| {
                response
                    .pointer(pointer)
                    .and_then(serde_json::Value::as_u64)
                    .ok_or_else(|| MappingError::MissingSource(pointer.clone()))
            })
            .transpose()?;
        let confidence_millionths = self
            .confidence_pointer
            .as_ref()
            .map(|pointer| {
                let confidence = response
                    .pointer(pointer)
                    .and_then(serde_json::Value::as_f64)
                    .filter(|value| value.is_finite() && (0.0..=1.0).contains(value))
                    .ok_or_else(|| MappingError::MissingSource(pointer.clone()))?;
                Ok::<u32, MappingError>((confidence * 1_000_000.0).round() as u32)
            })
            .transpose()?;
        Ok(StateExportFact {
            export_id: self.id.clone(),
            value,
            source_observed_at_ms,
            confidence_millionths,
        })
    }
}

impl CompiledPeerChannelObserver {
    /// Returns the node-wide observer identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the configuration-owned Local EAIOS identity.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns the configured observation interval.
    pub const fn interval_ms(&self) -> u64 {
        self.interval_ms
    }

    /// Returns the fixed read-only observation step.
    pub const fn step(&self) -> &CompiledWorkflowStep {
        &self.step
    }

    /// Maps a bounded local response into owner-qualified readiness facts.
    pub fn map(
        &self,
        context: &WorkflowContext,
    ) -> Result<Vec<PeerChannelReadinessFact>, MappingError> {
        let response_pointer = format!("/steps/{}", escape_pointer_segment(self.step.id()));
        let response = context
            .as_json()
            .pointer(&response_pointer)
            .ok_or_else(|| MappingError::MissingSource(response_pointer.clone()))?;
        if serde_json::to_vec(response)
            .map_err(|_| MappingError::InvalidFunctionArguments("peer readiness JSON".to_string()))?
            .len()
            > domain::MAX_STATE_PAYLOAD_BYTES
        {
            return Err(MappingError::InvalidFunctionArguments(
                "peer readiness response exceeds 64 KiB".to_string(),
            ));
        }
        let channels = response
            .pointer(&self.channels_pointer)
            .and_then(serde_json::Value::as_array)
            .ok_or_else(|| MappingError::MissingSource(self.channels_pointer.clone()))?;
        if channels.len() > MAX_PEER_CHANNELS_PER_OBSERVATION {
            return Err(MappingError::InvalidFunctionArguments(
                "peer readiness observation exceeds 64 endpoints".to_string(),
            ));
        }
        let facts = channels
            .iter()
            .map(|channel| {
                Ok(PeerChannelReadinessFact {
                    group_id: peer_channel_string(channel, "group_id")?,
                    context_id: peer_channel_string(channel, "context_id")?,
                    context_role_id: peer_channel_string(channel, "context_role_id")?,
                    local_system_id: self.owner.clone(),
                    channel_instance_id: peer_channel_string(channel, "channel_instance_id")?,
                    profile_id: peer_channel_string(channel, "profile_id")?,
                    message_schema: peer_channel_string(channel, "message_schema")?,
                    ready: channel
                        .get("ready")
                        .and_then(serde_json::Value::as_bool)
                        .ok_or_else(|| {
                            MappingError::MissingSource("peer readiness ready".to_string())
                        })?,
                    valid_for_ms: self.valid_for_ms,
                })
            })
            .collect::<Result<Vec<_>, MappingError>>()?;
        let mut identities = BTreeSet::new();
        for fact in &facts {
            if !identities.insert((
                fact.group_id.as_str(),
                fact.context_id.as_str(),
                fact.context_role_id.as_str(),
            )) {
                return Err(MappingError::InvalidFunctionArguments(
                    "peer readiness observation contains a duplicate logical endpoint".to_string(),
                ));
            }
        }
        Ok(facts)
    }
}

/// Reads one required nonblank string from a peer-channel observation object.
fn peer_channel_string(channel: &serde_json::Value, field: &str) -> Result<String, MappingError> {
    channel
        .get(field)
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(ToString::to_string)
        .ok_or_else(|| MappingError::MissingSource(format!("peer readiness {field}")))
}

impl CompiledMemoryProvider {
    /// Returns the node-wide provider identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the local-system owner.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns the Memory kind spelling.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the maximum scope spelling.
    pub fn scope(&self) -> &str {
        &self.scope
    }

    /// Returns the discovery/exchange visibility spelling.
    pub fn visibility(&self) -> &str {
        &self.visibility
    }

    /// Returns the provider payload schema.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the provider content media type.
    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    /// Returns whether v0.6 enables the Node ledger and operational provider workflow facade.
    pub const fn operational(&self) -> bool {
        self.operational
    }

    /// Returns the Node ledger and workflow-free reference-backend root.
    pub fn storage_directory(&self) -> &Path {
        &self.storage_directory
    }

    /// Returns the optional discovery workflow.
    pub const fn discover(&self) -> Option<&CompiledMemoryWorkflow> {
        self.discover.as_ref()
    }

    /// Returns the optional export workflow.
    pub const fn export(&self) -> Option<&CompiledMemoryWorkflow> {
        self.export.as_ref()
    }

    /// Returns the optional import workflow.
    pub const fn import(&self) -> Option<&CompiledMemoryWorkflow> {
        self.import.as_ref()
    }
}

impl CompiledMemoryWorkflow {
    /// Returns ordered provider-local workflow steps.
    pub fn steps(&self) -> &[CompiledWorkflowStep] {
        &self.steps
    }

    /// Returns the optional response pointer containing manifests.
    pub fn manifests_pointer(&self) -> Option<&str> {
        self.manifests_pointer.as_deref()
    }

    /// Returns the optional response pointer containing an artifact path.
    pub fn artifact_path_pointer(&self) -> Option<&str> {
        self.artifact_path_pointer.as_deref()
    }
}

impl CompiledCapabilityReadiness {
    /// Returns the fixed local observation step.
    pub const fn step(&self) -> &CompiledWorkflowStep {
        &self.step
    }

    /// Maps one completed observation response into an exact readiness fact.
    pub fn map(&self, context: &WorkflowContext) -> Result<CapabilityReadinessFact, MappingError> {
        let response_pointer = format!("/steps/{}", escape_pointer_segment(self.step.id()));
        let response = context
            .as_json()
            .pointer(&response_pointer)
            .ok_or_else(|| MappingError::MissingSource(response_pointer.clone()))?;
        let state = response
            .pointer(&self.state_pointer)
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| MappingError::MissingSource(self.state_pointer.clone()))?;
        let key = if self.case_sensitive {
            state.to_string()
        } else {
            state.to_ascii_lowercase()
        };
        let available = self.states.get(&key).copied().ok_or_else(|| {
            MappingError::MissingSource(format!("unmapped capability readiness state {key}"))
        })?;
        let detail = self
            .detail_pointer
            .as_ref()
            .and_then(|pointer| response.pointer(pointer))
            .map(value_to_reason)
            .unwrap_or_default();
        Ok(CapabilityReadinessFact { available, detail })
    }
}
