//! Compiled Local Integration catalog access and request rendering.

use super::*;

impl CompiledLocalCatalog {
    /// Compiles and validates the entire catalog atomically before any connection is opened.
    pub fn compile(
        config: NodeServiceConfig,
        config_directory: &Path,
    ) -> Result<Self, CatalogError> {
        let supports_artifacts = matches!(
            config.schema.as_str(),
            CONFIG_SCHEMA_V0_3
                | CONFIG_SCHEMA_V0_4
                | CONFIG_SCHEMA_V0_5
                | CONFIG_SCHEMA_V0_6
                | CONFIG_SCHEMA_V0_7
        );
        let requires_readiness = matches!(
            config.schema.as_str(),
            CONFIG_SCHEMA_V0_4 | CONFIG_SCHEMA_V0_5 | CONFIG_SCHEMA_V0_6 | CONFIG_SCHEMA_V0_7
        );
        let supports_state_memory = matches!(
            config.schema.as_str(),
            CONFIG_SCHEMA_V0_5 | CONFIG_SCHEMA_V0_6 | CONFIG_SCHEMA_V0_7
        );
        let supports_peer_observers = matches!(
            config.schema.as_str(),
            CONFIG_SCHEMA_V0_6 | CONFIG_SCHEMA_V0_7
        );
        let separates_capabilities_and_operations = config.schema == CONFIG_SCHEMA_V0_7;
        require(
            matches!(
                config.schema.as_str(),
                CONFIG_SCHEMA_V0_2
                    | CONFIG_SCHEMA_V0_3
                    | CONFIG_SCHEMA_V0_4
                    | CONFIG_SCHEMA_V0_5
                    | CONFIG_SCHEMA_V0_6
                    | CONFIG_SCHEMA_V0_7
            ),
            "schema",
            format!(
                "expected `{CONFIG_SCHEMA_V0_2}`, `{CONFIG_SCHEMA_V0_3}`, `{CONFIG_SCHEMA_V0_4}`, `{CONFIG_SCHEMA_V0_5}`, `{CONFIG_SCHEMA_V0_6}`, or `{CONFIG_SCHEMA_V0_7}`"
            ),
        )?;
        validate_identity(&config.node_id, "node_id")?;
        validate_server_endpoint(&config.server_endpoint)?;
        require(
            config.reconnect_delay_ms > 0,
            "reconnect_delay_ms",
            "must be non-zero",
        )?;
        require(
            !config.state_directory.as_os_str().is_empty(),
            "state_directory",
            "must not be empty",
        )?;
        let state_directory = resolve_path(config_directory, &config.state_directory);

        let (local_systems, health_configs) = compile_local_systems(config.local_systems)?;
        require(
            !local_systems.is_empty(),
            "local_systems",
            "must contain at least one local system",
        )?;
        let connections =
            compile_connections(config.connections, &local_systems, config_directory)?;
        require(
            !connections.is_empty(),
            "connections",
            "must contain at least one local connection",
        )?;
        let health_checks = compile_health_checks(health_configs, &local_systems, &connections)?;
        require(
            supports_state_memory
                || (config.state_exports.is_empty() && config.memory_providers.is_empty()),
            "state_exports",
            format!("State and Memory declarations require schema `{CONFIG_SCHEMA_V0_5}`"),
        )?;
        let state_exports =
            compile_state_exports(config.state_exports, &local_systems, &connections)?;
        require(
            supports_peer_observers || config.peer_channel_observers.is_empty(),
            "peer_channel_observers",
            format!("requires schema `{CONFIG_SCHEMA_V0_6}`"),
        )?;
        let peer_channel_observers = compile_peer_channel_observers(
            config.peer_channel_observers,
            &local_systems,
            &connections,
        )?;
        let memory_providers = compile_memory_providers(
            config.memory_providers,
            &local_systems,
            &connections,
            &state_directory,
            matches!(
                config.schema.as_str(),
                CONFIG_SCHEMA_V0_6 | CONFIG_SCHEMA_V0_7
            ),
        )?;
        let resources = compile_resources(config.resources, &local_systems)?;
        let sensors = compile_sensors(config.sensors, &local_systems)?;
        let (capability_profiles, operations) = if separates_capabilities_and_operations {
            require(
                config.capabilities.is_empty(),
                "capabilities",
                "legacy combined declarations are not allowed by node-config/v0.7",
            )?;
            let profiles = compile_capability_profiles(
                config.capability_profiles,
                &local_systems,
                &connections,
            )?;
            let operations = compile_operations(
                config.operations,
                &local_systems,
                &connections,
                &resources,
                supports_artifacts,
            )?;
            (profiles, operations)
        } else {
            require(
                config.capability_profiles.is_empty() && config.operations.is_empty(),
                "capability_profiles",
                format!("separate profiles and operations require schema `{CONFIG_SCHEMA_V0_7}`"),
            )?;
            compile_legacy_capabilities(
                config.capabilities,
                &local_systems,
                &connections,
                &resources,
                supports_artifacts,
                requires_readiness,
            )?
        };
        require(
            !capability_profiles.is_empty(),
            "capability_profiles",
            "must contain at least one canonical capability profile",
        )?;
        require(
            !operations.is_empty(),
            "operations",
            "must contain at least one executable canonical operation",
        )?;
        require(
            supports_artifacts || config.artifacts.is_none(),
            "artifacts",
            format!("requires schema `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        let artifacts = compile_artifacts(config.artifacts, config_directory)?;

        Ok(Self {
            schema: config.schema,
            node_id: config.node_id,
            server_endpoint: config.server_endpoint,
            state_directory,
            reconnect_delay_ms: config.reconnect_delay_ms,
            local_systems,
            connections,
            health_checks,
            operations,
            capability_profiles,
            resources,
            sensors,
            state_exports,
            peer_channel_observers,
            memory_providers,
            artifacts,
        })
    }

    /// Returns the accepted versioned node-config schema identity.
    pub fn schema(&self) -> &str {
        &self.schema
    }

    /// Returns the stable node identity.
    pub fn node_id(&self) -> &str {
        &self.node_id
    }

    /// Returns the formal remote RoboGuide Server endpoint.
    pub fn server_endpoint(&self) -> &str {
        &self.server_endpoint
    }

    /// Returns the resolved durable execution-journal directory.
    pub fn state_directory(&self) -> &Path {
        &self.state_directory
    }

    /// Returns the configured reconnect delay.
    pub const fn reconnect_delay_ms(&self) -> u64 {
        self.reconnect_delay_ms
    }

    /// Returns local systems in stable lexical identity order.
    pub const fn local_systems(&self) -> &BTreeMap<String, CompiledLocalSystem> {
        &self.local_systems
    }

    /// Returns fixed local connections in stable lexical identity order.
    pub const fn connections(&self) -> &BTreeMap<String, CompiledConnection> {
        &self.connections
    }

    /// Returns local-system health checks in stable owner order.
    pub const fn health_checks(&self) -> &BTreeMap<String, CompiledHealthCheck> {
        &self.health_checks
    }

    /// Returns legacy-named canonical operation workflows in stable lexical order.
    ///
    /// New code should use [`Self::operations`]. This accessor remains for source
    /// compatibility with node-config/v0.2-v0.6 consumers.
    pub const fn capabilities(&self) -> &BTreeMap<String, CompiledCapability> {
        &self.operations
    }

    /// Returns canonical operation workflows in stable lexical identity order.
    pub const fn operations(&self) -> &BTreeMap<String, CompiledOperation> {
        &self.operations
    }

    /// Returns exact capability evidence in stable canonical-contract order.
    pub const fn capability_profiles(&self) -> &BTreeMap<String, CompiledCapabilityProfile> {
        &self.capability_profiles
    }

    /// Returns the semantic Node Contract selected by this configuration generation.
    pub fn node_contract_version(&self) -> &'static str {
        if self.schema == CONFIG_SCHEMA_V0_7 {
            integration::grpc::v0_4::NODE_CONTRACT_VERSION
        } else {
            integration::grpc::v0_4::LEGACY_NODE_CONTRACT_VERSION
        }
    }

    /// Returns Control-visible resources in stable lexical identity order.
    pub const fn resources(&self) -> &BTreeMap<String, CompiledResource> {
        &self.resources
    }

    /// Returns sensors in stable lexical identity order.
    pub const fn sensors(&self) -> &BTreeMap<String, CompiledSensor> {
        &self.sensors
    }

    /// Returns selective State channels in deterministic export identity order.
    pub const fn state_exports(&self) -> &BTreeMap<String, CompiledStateExport> {
        &self.state_exports
    }

    /// Returns fixed peer-channel observation sources in deterministic identity order.
    pub const fn peer_channel_observers(&self) -> &BTreeMap<String, CompiledPeerChannelObserver> {
        &self.peer_channel_observers
    }

    /// Returns selective Memory providers in deterministic provider identity order.
    pub const fn memory_providers(&self) -> &BTreeMap<String, CompiledMemoryProvider> {
        &self.memory_providers
    }

    /// Returns optional startup-validated Spatial Memory artifact configuration.
    pub const fn artifact_service(&self) -> Option<&CompiledArtifactService> {
        self.artifacts.as_ref()
    }
}

impl CompiledArtifactService {
    /// Returns the central artifact data-plane endpoint.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    /// Returns the deployment-owned cache directory.
    pub fn cache_directory(&self) -> &Path {
        &self.cache_directory
    }

    /// Returns the maximum artifact size accepted by this node.
    pub const fn max_artifact_bytes(&self) -> u64 {
        self.max_artifact_bytes
    }

    /// Returns the bounded artifact transfer chunk size.
    pub const fn chunk_size_bytes(&self) -> usize {
        self.chunk_size_bytes
    }

    /// Returns the artifact data-plane connection timeout.
    pub const fn connect_timeout_ms(&self) -> u64 {
        self.connect_timeout_ms
    }

    /// Returns the artifact data-plane read-idle timeout.
    pub const fn read_timeout_ms(&self) -> u64 {
        self.read_timeout_ms
    }

    /// Returns validated static input bindings.
    pub const fn input_bindings(&self) -> &BTreeMap<String, ArtifactInputBindingConfig> {
        &self.input_bindings
    }

    /// Returns validated static output bindings.
    pub const fn output_bindings(&self) -> &BTreeMap<String, ArtifactOutputBindingConfig> {
        &self.output_bindings
    }
}

impl CompiledLocalSystem {
    /// Returns the stable local-system identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the runtime name.
    pub fn runtime_name(&self) -> &str {
        &self.runtime_name
    }

    /// Returns the runtime version.
    pub fn runtime_version(&self) -> &str {
        &self.runtime_version
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

impl CompiledHealthCheck {
    /// Returns the local system observed by this check.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns the fixed health operation step.
    pub const fn step(&self) -> &CompiledWorkflowStep {
        &self.step
    }

    /// Maps one completed health-step response into a canonical fact.
    pub fn map(&self, context: &WorkflowContext) -> Result<LocalHealthFact, MappingError> {
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
        let state =
            self.states.get(&key).copied().ok_or_else(|| {
                MappingError::MissingSource(format!("unmapped health state {key}"))
            })?;
        let detail = self
            .detail_pointer
            .as_ref()
            .and_then(|pointer| response.pointer(pointer))
            .map(value_to_reason)
            .unwrap_or_default();
        Ok(LocalHealthFact { state, detail })
    }
}
