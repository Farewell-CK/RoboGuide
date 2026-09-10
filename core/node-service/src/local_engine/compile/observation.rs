//! Local health, State, peer, and Memory observation compilation.

use super::*;

/// Compiles unique local systems.
pub(super) fn compile_local_systems(
    configs: Vec<LocalSystemConfig>,
) -> Result<CompiledLocalSystems, CatalogError> {
    let mut systems = BTreeMap::new();
    let mut health_checks = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "local_systems.id")?;
        validate_identity(&config.runtime_name, "local_systems.runtime_name")?;
        validate_identity(&config.runtime_version, "local_systems.runtime_version")?;
        let id = config.id.clone();
        health_checks.insert(id.clone(), config.health);
        let system = CompiledLocalSystem {
            id: config.id,
            runtime_name: config.runtime_name,
            runtime_version: config.runtime_version,
            metadata: config.metadata,
        };
        insert_unique(&mut systems, id, system, "local_systems.id")?;
    }
    Ok((systems, health_checks))
}

/// Compiles one required health check per configured local system.
pub(super) fn compile_health_checks(
    configs: BTreeMap<String, HealthCheckConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<BTreeMap<String, CompiledHealthCheck>, CatalogError> {
    let mut checks = BTreeMap::new();
    for (owner, config) in configs {
        validate_step_sources(
            std::slice::from_ref(&config.step),
            false,
            &format!("local_systems.{owner}.health"),
        )?;
        let mut step_ids = BTreeSet::new();
        for binding in &config.step.request.bindings {
            if contains_pointer_expression(&binding.value) {
                return Err(validation(
                    format!("local_systems.{owner}.health.step"),
                    "health request mappings must use deployment constants only",
                ));
            }
        }
        let mut steps = compile_steps(vec![config.step], &owner, connections, &mut step_ids)?;
        let step = steps.pop().expect("one health step compiles into one step");
        validate_pointer(&config.state_pointer).map_err(|source| CatalogError::Mapping {
            step: format!("local_systems.{owner}.health"),
            source,
        })?;
        if let Some(pointer) = &config.detail_pointer {
            validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
                step: format!("local_systems.{owner}.health"),
                source,
            })?;
        }
        let mut states = BTreeMap::new();
        insert_health_states(
            &mut states,
            config.online,
            LocalHealthState::Online,
            config.case_sensitive,
        )?;
        insert_health_states(
            &mut states,
            config.degraded,
            LocalHealthState::Degraded,
            config.case_sensitive,
        )?;
        insert_health_states(
            &mut states,
            config.offline,
            LocalHealthState::Offline,
            config.case_sensitive,
        )?;
        require(
            states
                .values()
                .any(|state| *state == LocalHealthState::Online)
                && states
                    .values()
                    .any(|state| *state == LocalHealthState::Degraded)
                && states
                    .values()
                    .any(|state| *state == LocalHealthState::Offline),
            format!("local_systems.{owner}.health"),
            "online, degraded, and offline mappings must all be nonempty",
        )?;
        checks.insert(
            owner.clone(),
            CompiledHealthCheck {
                owner,
                step,
                state_pointer: config.state_pointer,
                detail_pointer: config.detail_pointer,
                states,
                case_sensitive: config.case_sensitive,
            },
        );
    }
    require(
        checks.len() == systems.len(),
        "local_systems.health",
        "every local system must have one health check",
    )?;
    Ok(checks)
}

/// Compiles selective fixed State sampling operations without granting lifecycle authority.
pub(super) fn compile_state_exports(
    configs: Vec<StateExportConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<BTreeMap<String, CompiledStateExport>, CatalogError> {
    let mut exports = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "state_exports.id")?;
        require(
            systems.contains_key(&config.owner),
            format!("state_exports.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        require(
            matches!(config.object_class.as_str(), "node" | "world"),
            format!("state_exports.{}.object_class", config.id),
            "must be node or world",
        )?;
        require(
            matches!(config.semantic.as_str(), "reported" | "observed"),
            format!("state_exports.{}.semantic", config.id),
            "must be reported or observed",
        )?;
        validate_identity(&config.object_type, "state_exports.object_type")?;
        validate_identity(&config.object_id, "state_exports.object_id")?;
        validate_identity(&config.payload_schema, "state_exports.payload_schema")?;
        require(
            config.valid_for_ms > 0,
            format!("state_exports.{}.valid_for_ms", config.id),
            "must be positive",
        )?;
        require(
            config.interval_ms >= 100,
            format!("state_exports.{}.interval_ms", config.id),
            "must be at least 100ms",
        )?;
        let field = format!("state_exports.{}", config.id);
        validate_step_sources(std::slice::from_ref(&config.step), false, &field)?;
        validate_read_only_observation_operation(&config.step.operation, &field, "State export")?;
        if config
            .step
            .request
            .bindings
            .iter()
            .any(|binding| contains_pointer_expression(&binding.value))
        {
            return Err(validation(
                format!("{field}.step"),
                "State sampling requests must use deployment constants only",
            ));
        }
        for pointer in [
            Some(&config.value_pointer),
            config.source_observed_at_pointer.as_ref(),
            config.confidence_pointer.as_ref(),
        ]
        .into_iter()
        .flatten()
        {
            validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
                step: field.clone(),
                source,
            })?;
        }
        let mut step_ids = BTreeSet::new();
        let mut steps =
            compile_steps(vec![config.step], &config.owner, connections, &mut step_ids)?;
        let id = config.id.clone();
        let export = CompiledStateExport {
            id: config.id,
            owner: config.owner,
            object_class: config.object_class,
            object_type: config.object_type,
            object_id: config.object_id,
            semantic: config.semantic,
            payload_schema: config.payload_schema,
            valid_for_ms: config.valid_for_ms,
            interval_ms: config.interval_ms,
            step: steps
                .pop()
                .expect("one State export step compiles into one step"),
            value_pointer: config.value_pointer,
            source_observed_at_pointer: config.source_observed_at_pointer,
            confidence_pointer: config.confidence_pointer,
        };
        insert_unique(&mut exports, id, export, "state_exports.id")?;
    }
    Ok(exports)
}

/// Compiles fixed Local EAIOS peer-readiness observers without granting channel authority.
pub(super) fn compile_peer_channel_observers(
    configs: Vec<PeerChannelObserverConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<BTreeMap<String, CompiledPeerChannelObserver>, CatalogError> {
    let mut observers = BTreeMap::new();
    let mut owners = BTreeSet::new();
    for config in configs {
        validate_identity(&config.id, "peer_channel_observers.id")?;
        require(
            systems.contains_key(&config.owner),
            format!("peer_channel_observers.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        require(
            owners.insert(config.owner.clone()),
            format!("peer_channel_observers.{}.owner", config.id),
            format!(
                "local system `{}` already has a peer-channel observer",
                config.owner
            ),
        )?;
        require(
            config.interval_ms >= 100,
            format!("peer_channel_observers.{}.interval_ms", config.id),
            "must be at least 100ms",
        )?;
        require(
            (1..=60_000).contains(&config.valid_for_ms),
            format!("peer_channel_observers.{}.valid_for_ms", config.id),
            "must be between 1ms and 60000ms",
        )?;
        require(
            config.valid_for_ms >= config.interval_ms,
            format!("peer_channel_observers.{}.valid_for_ms", config.id),
            "must be at least the observation interval",
        )?;
        let field = format!("peer_channel_observers.{}", config.id);
        validate_step_sources(std::slice::from_ref(&config.step), false, &field)?;
        validate_read_only_observation_operation(
            &config.step.operation,
            &field,
            "Peer-channel observation",
        )?;
        if config
            .step
            .request
            .bindings
            .iter()
            .any(|binding| contains_pointer_expression(&binding.value))
        {
            return Err(validation(
                format!("{field}.step"),
                "Peer-channel observation requests must use deployment constants only",
            ));
        }
        validate_pointer(&config.channels_pointer).map_err(|source| CatalogError::Mapping {
            step: field.clone(),
            source,
        })?;
        let mut step_ids = BTreeSet::new();
        let mut steps =
            compile_steps(vec![config.step], &config.owner, connections, &mut step_ids)?;
        let id = config.id.clone();
        let observer = CompiledPeerChannelObserver {
            id: config.id,
            owner: config.owner,
            interval_ms: config.interval_ms,
            valid_for_ms: config.valid_for_ms,
            step: steps
                .pop()
                .expect("one peer-channel observer step compiles into one step"),
            channels_pointer: config.channels_pointer,
        };
        insert_unique(&mut observers, id, observer, "peer_channel_observers.id")?;
    }
    Ok(observers)
}

/// Restricts periodic observations to operations that are mechanically read-only.
pub(super) fn validate_read_only_observation_operation(
    operation: &LocalOperationConfig,
    field: &str,
    label: &str,
) -> Result<(), CatalogError> {
    match operation {
        LocalOperationConfig::Http { method, .. } if method == "GET" => Ok(()),
        LocalOperationConfig::Http { .. } => Err(validation(
            format!("{field}.step.operation"),
            format!("{label} HTTP operations must use GET"),
        )),
        LocalOperationConfig::GrpcUnary { .. }
        | LocalOperationConfig::GrpcServerStream { .. }
        | LocalOperationConfig::McpTool { .. } => Err(validation(
            format!("{field}.step.operation"),
            format!(
                "{label} gRPC and MCP operations require an explicit read-only contract and are not enabled"
            ),
        )),
    }
}

/// Compiles provider metadata while preserving local storage and exchange ownership.
pub(super) fn compile_memory_providers(
    configs: Vec<MemoryProviderConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
    state_directory: &Path,
    supports_workflows: bool,
) -> Result<BTreeMap<String, CompiledMemoryProvider>, CatalogError> {
    let mut providers = BTreeMap::new();
    let mut storage_roots: BTreeSet<PathBuf> = BTreeSet::new();
    for config in configs {
        validate_identity(&config.id, "memory_providers.id")?;
        require(
            systems.contains_key(&config.owner),
            format!("memory_providers.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        require(
            matches!(
                config.kind.as_str(),
                "execution" | "spatial" | "semantic" | "experience" | "artifact"
            ),
            format!("memory_providers.{}.kind", config.id),
            "must be execution, spatial, semantic, experience, or artifact",
        )?;
        require(
            matches!(config.scope.as_str(), "local" | "global"),
            format!("memory_providers.{}.scope", config.id),
            "must be local or global",
        )?;
        require(
            matches!(config.visibility.as_str(), "discoverable" | "exchangeable"),
            format!("memory_providers.{}.visibility", config.id),
            "must be discoverable or exchangeable",
        )?;
        validate_identity(&config.payload_schema, "memory_providers.payload_schema")?;
        require(
            !supports_workflows || config.payload_schema != domain::SPATIAL_MEMORY_SCHEMA_V0_1,
            format!("memory_providers.{}.payload_schema", config.id),
            "v0.6 generic workflows cannot claim the dedicated typed map and localization-verification schema",
        )?;
        require(
            !config.media_type.trim().is_empty(),
            format!("memory_providers.{}.media_type", config.id),
            "must not be empty",
        )?;
        require(
            supports_workflows
                || (config.discover.is_none()
                    && config.export.is_none()
                    && config.import.is_none()
                    && config.storage_directory.is_none()),
            format!("memory_providers.{}", config.id),
            format!("Memory workflows require schema `{CONFIG_SCHEMA_V0_6}`"),
        )?;
        let storage_directory = match config.storage_directory.as_deref() {
            Some(path) => {
                validate_relative_artifact_path(
                    path,
                    &format!("memory_providers.{}.storage_directory", config.id),
                )?;
                resolve_path(state_directory, path)
            }
            None => state_directory.join("memory").join(&config.id),
        };
        require(
            storage_roots.iter().all(|existing| {
                !storage_directory.starts_with(existing)
                    && !existing.starts_with(&storage_directory)
            }),
            format!("memory_providers.{}.storage_directory", config.id),
            "must not equal, contain, or be contained by another Memory ledger root",
        )?;
        storage_roots.insert(storage_directory.clone());
        let discover = compile_memory_workflow(
            config.discover,
            &config.id,
            &config.owner,
            connections,
            "discover",
        )?;
        let export = compile_memory_workflow(
            config.export,
            &config.id,
            &config.owner,
            connections,
            "export",
        )?;
        let import = compile_memory_workflow(
            config.import,
            &config.id,
            &config.owner,
            connections,
            "import",
        )?;
        require(
            config.visibility != "exchangeable"
                || export
                    .as_ref()
                    .is_none_or(|workflow| workflow.artifact_path_pointer().is_some()),
            format!(
                "memory_providers.{}.export.artifact_path_pointer",
                config.id
            ),
            "is required for an exchangeable provider export workflow",
        )?;
        let id = config.id.clone();
        let provider = CompiledMemoryProvider {
            id: config.id,
            owner: config.owner,
            kind: config.kind,
            scope: config.scope,
            visibility: config.visibility,
            payload_schema: config.payload_schema,
            media_type: config.media_type,
            operational: supports_workflows,
            storage_directory,
            discover,
            export,
            import,
        };
        insert_unique(&mut providers, id, provider, "memory_providers.id")?;
    }
    Ok(providers)
}

/// Compiles one optional Memory workflow using the same fixed local routing as capabilities.
pub(super) fn compile_memory_workflow(
    config: Option<MemoryWorkflowConfig>,
    provider_id: &str,
    owner: &str,
    connections: &BTreeMap<String, CompiledConnection>,
    operation: &str,
) -> Result<Option<CompiledMemoryWorkflow>, CatalogError> {
    let Some(config) = config else {
        return Ok(None);
    };
    require(
        !config.steps.is_empty(),
        format!("memory_providers.{provider_id}.{operation}.steps"),
        "must contain at least one step",
    )?;
    validate_step_sources(
        &config.steps,
        false,
        &format!("memory_providers.{provider_id}.{operation}.steps"),
    )?;
    let steps = compile_steps(config.steps, owner, connections, &mut BTreeSet::new())?;
    if let Some(pointer) = &config.manifests_pointer {
        validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
            step: format!("memory_providers.{provider_id}.{operation}"),
            source,
        })?;
    }
    if let Some(pointer) = &config.artifact_path_pointer {
        validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
            step: format!("memory_providers.{provider_id}.{operation}"),
            source,
        })?;
    }
    match operation {
        "discover" => {
            require(
                config.manifests_pointer.is_some(),
                format!("memory_providers.{provider_id}.discover.manifests_pointer"),
                "is required for discovery",
            )?;
            require(
                config.artifact_path_pointer.is_none(),
                format!("memory_providers.{provider_id}.discover.artifact_path_pointer"),
                "is not valid for discovery",
            )?;
        }
        "export" => require(
            config.manifests_pointer.is_none(),
            format!("memory_providers.{provider_id}.export.manifests_pointer"),
            "is not valid for export",
        )?,
        "import" => require(
            config.manifests_pointer.is_none() && config.artifact_path_pointer.is_none(),
            format!("memory_providers.{provider_id}.import"),
            "does not accept result pointers",
        )?,
        _ => {
            return Err(validation(
                format!("memory_providers.{provider_id}.{operation}"),
                "unknown Memory provider operation",
            ));
        }
    }
    Ok(Some(CompiledMemoryWorkflow {
        steps,
        manifests_pointer: config.manifests_pointer,
        artifact_path_pointer: config.artifact_path_pointer,
    }))
}

/// Returns whether an expression reads dynamic invocation or prior workflow context.
pub(super) fn contains_pointer_expression(expression: &crate::ValueExpressionConfig) -> bool {
    match expression {
        crate::ValueExpressionConfig::Pointer { .. } => true,
        crate::ValueExpressionConfig::Constant { .. } => false,
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            arguments.iter().any(contains_pointer_expression)
        }
    }
}

/// Inserts one health phase while rejecting ambiguous local values.
pub(super) fn insert_health_states(
    states: &mut BTreeMap<String, LocalHealthState>,
    values: Vec<String>,
    state: LocalHealthState,
    case_sensitive: bool,
) -> Result<(), CatalogError> {
    for value in values {
        validate_identity(&value, "local_systems.health.state")?;
        let key = if case_sensitive {
            value
        } else {
            value.to_ascii_lowercase()
        };
        require(
            states.insert(key.clone(), state).is_none(),
            "local_systems.health",
            format!("local health state `{key}` has multiple mappings"),
        )?;
    }
    Ok(())
}
