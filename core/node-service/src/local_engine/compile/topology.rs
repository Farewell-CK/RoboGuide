//! Connection, resource, sensor, and readiness topology compilation.

use super::*;

/// Compiles fixed connections and resolves descriptor paths.
pub(super) fn compile_connections(
    configs: Vec<ConnectionConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    config_directory: &Path,
    budget: &mut crate::source_io::SourceBudget,
    descriptor_sources: &mut BTreeMap<PathBuf, Vec<u8>>,
) -> Result<CompilationConnections, CatalogError> {
    let mut connections = BTreeMap::new();
    let mut descriptors = BTreeMap::new();
    for config in configs {
        validate_identity(config.id(), "connections.id")?;
        require(
            systems.contains_key(config.local_system()),
            format!("connections.{}.local_system", config.id()),
            format!("unknown local system `{}`", config.local_system()),
        )?;
        validate_local_endpoint(
            config.endpoint(),
            &format!("connections.{}.endpoint", config.id()),
        )?;
        let (id, connection) = match config {
            ConnectionConfig::Http {
                id,
                local_system,
                endpoint,
                timeout_ms,
                headers,
            } => {
                require(
                    timeout_ms > 0,
                    format!("connections.{id}.timeout_ms"),
                    "must be non-zero",
                )?;
                let headers = compile_credentials(headers, &format!("connections.{id}.headers"))?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Http {
                        id,
                        owner: local_system,
                        endpoint,
                        timeout_ms,
                        credential_headers: headers,
                    },
                )
            }
            ConnectionConfig::Grpc {
                id,
                local_system,
                endpoint,
                descriptor_set,
                reflection,
                timeout_ms,
                metadata,
            } => {
                require(
                    timeout_ms > 0,
                    format!("connections.{id}.timeout_ms"),
                    "must be non-zero",
                )?;
                require(
                    descriptor_set.is_some() ^ reflection,
                    format!("connections.{id}.descriptor_set"),
                    "configure exactly one descriptor_set or reflection=true",
                )?;
                let descriptor_set =
                    descriptor_set
                        .map(|path| resolve_path(config_directory, &path))
                        .map(|path| {
                            if !descriptors.contains_key(&path) {
                                let bytes =
                                    if let Some(bytes) = descriptor_sources.get(&path) {
                                        bytes.clone()
                                    } else {
                                        let bytes = budget
                                    .read(&path, crate::MAX_DESCRIPTOR_SOURCE_BYTES)
                                    .map_err(|error| validation(
                                        format!("connections.{id}.descriptor_set"),
                                        format!("cannot read bounded descriptor source: {error}"),
                                    ))?;
                                        descriptor_sources.insert(path.clone(), bytes.clone());
                                        bytes
                                    };
                                let pool =
                                    DescriptorPool::decode(bytes.as_slice()).map_err(|error| {
                                        validation(
                                            format!("connections.{id}.descriptor_set"),
                                            format!("cannot decode gRPC descriptor set: {error}"),
                                        )
                                    })?;
                                descriptors.insert(path.clone(), pool);
                            }
                            Ok::<_, CatalogError>(path)
                        })
                        .transpose()?;
                let metadata =
                    compile_credentials(metadata, &format!("connections.{id}.metadata"))?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Grpc {
                        id,
                        owner: local_system,
                        endpoint,
                        descriptor_set,
                        reflection,
                        timeout_ms,
                        credential_metadata: metadata,
                    },
                )
            }
            ConnectionConfig::Mcp {
                id,
                local_system,
                endpoint,
                timeout_ms,
                headers,
            } => {
                require(
                    timeout_ms > 0,
                    format!("connections.{id}.timeout_ms"),
                    "must be non-zero",
                )?;
                let headers = compile_credentials(headers, &format!("connections.{id}.headers"))?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Mcp {
                        id,
                        owner: local_system,
                        endpoint,
                        timeout_ms,
                        credential_headers: headers,
                    },
                )
            }
        };
        insert_unique(&mut connections, id, connection, "connections.id")?;
    }
    Ok(CompilationConnections {
        entries: connections,
        descriptors,
    })
}

/// Compiles unique resources and validates ownership and capacity.
pub(super) fn compile_resources(
    configs: Vec<ResourceConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
) -> Result<BTreeMap<String, CompiledResource>, CatalogError> {
    let mut resources = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "resources.id")?;
        validate_identity(&config.kind, "resources.kind")?;
        require(
            matches!(config.kind.as_str(), "space" | "compute" | "time"),
            format!("resources.{}.kind", config.id),
            "must be space, compute, or time",
        )?;
        require(
            config.capacity > 0,
            format!("resources.{}.capacity", config.id),
            "must be non-zero",
        )?;
        require(
            systems.contains_key(&config.owner),
            format!("resources.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        let id = config.id.clone();
        let mode = config.allocation_mode.unwrap_or_default();
        require(
            match mode {
                domain::ResourceAllocationMode::Exclusive => config.resource_class.is_none(),
                domain::ResourceAllocationMode::Divisible => {
                    config.kind == "compute"
                        && config
                            .resource_class
                            .as_ref()
                            .is_some_and(|value| !value.trim().is_empty() && value.len() <= 128)
                }
            },
            "resources.resource_class",
            "divisible Compute requires explicit typed units; exclusive resources carry no class",
        )?;
        let resource = CompiledResource {
            id: config.id,
            kind: config.kind,
            capacity: config.capacity,
            allocation_mode: mode,
            resource_class: config.resource_class,
            owner: config.owner,
            metadata: config.metadata,
        };
        insert_unique(&mut resources, id, resource, "resources.id")?;
    }
    Ok(resources)
}

/// Compiles unique sensors and validates ownership.
pub(super) fn compile_sensors(
    configs: Vec<SensorConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
) -> Result<BTreeMap<String, CompiledSensor>, CatalogError> {
    let mut sensors = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "sensors.id")?;
        validate_identity(&config.kind, "sensors.kind")?;
        require(
            systems.contains_key(&config.owner),
            format!("sensors.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        let id = config.id.clone();
        let sensor = CompiledSensor {
            id: config.id,
            kind: config.kind,
            owner: config.owner,
            metadata: config.metadata,
        };
        insert_unique(&mut sensors, id, sensor, "sensors.id")?;
    }
    Ok(sensors)
}

/// Compiles one fixed exact-capability readiness observation.
pub(super) fn compile_capability_readiness(
    config: CapabilityReadinessConfig,
    contract: &str,
    owner: &str,
    connections: &CompilationConnections,
) -> Result<CompiledCapabilityReadiness, CatalogError> {
    let field = format!("capabilities.{contract}.readiness");
    validate_step_sources(std::slice::from_ref(&config.step), false, &field)?;
    if config
        .step
        .request
        .bindings
        .iter()
        .any(|binding| contains_pointer_expression(&binding.value))
    {
        return Err(validation(
            format!("{field}.step"),
            "readiness request mappings must use deployment constants only",
        ));
    }
    let mut step_ids = BTreeSet::new();
    let mut steps = compile_steps(vec![config.step], owner, connections, &mut step_ids)?;
    let step = steps
        .pop()
        .expect("one readiness step compiles into one step");
    validate_pointer(&config.state_pointer).map_err(|source| CatalogError::Mapping {
        step: field.clone(),
        source,
    })?;
    if let Some(pointer) = &config.detail_pointer {
        validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
            step: field.clone(),
            source,
        })?;
    }
    require(!config.ready.is_empty(), &field, "ready must be nonempty")?;
    require(
        !config.unavailable.is_empty(),
        &field,
        "unavailable must be nonempty",
    )?;
    let mut states = BTreeMap::new();
    for (values, available) in [(config.ready, true), (config.unavailable, false)] {
        for value in values {
            require(
                !value.trim().is_empty(),
                &field,
                "state values must be nonblank",
            )?;
            let key = if config.case_sensitive {
                value
            } else {
                value.to_ascii_lowercase()
            };
            require(
                states.insert(key.clone(), available).is_none(),
                &field,
                format!("duplicate readiness state `{key}`"),
            )?;
        }
    }
    Ok(CompiledCapabilityReadiness {
        step,
        state_pointer: config.state_pointer,
        detail_pointer: config.detail_pointer,
        states,
        case_sensitive: config.case_sensitive,
    })
}
