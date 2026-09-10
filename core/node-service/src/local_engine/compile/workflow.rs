//! Capability workflow, mapping, and endpoint validation compilation.

use super::*;

/// Compiles canonical capability owners and their local workflows.
pub(super) fn compile_capabilities(
    configs: Vec<CapabilityBindingConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
    resources: &BTreeMap<String, CompiledResource>,
    supports_artifacts: bool,
    requires_readiness: bool,
) -> Result<BTreeMap<String, CompiledCapability>, CatalogError> {
    let mut capabilities = BTreeMap::new();
    for config in configs {
        validate_contract(&config.contract)?;
        require(
            matches!(
                config.kind.as_str(),
                "mobility" | "transport" | "compute" | "observation"
            ),
            format!("capabilities.{}.kind", config.contract),
            "must be mobility, transport, compute, or observation",
        )?;
        require(
            !capabilities.contains_key(&config.contract),
            "capabilities.contract",
            format!("duplicate identity `{}`", config.contract),
        )?;
        require(
            systems.contains_key(&config.owner),
            format!("capabilities.{}.owner", config.contract),
            format!("unknown local system `{}`", config.owner),
        )?;
        let required_resources = unique_nonblank(
            config.required_resources,
            &format!("capabilities.{}.required_resources", config.contract),
        )?;
        for resource_id in &required_resources {
            let resource = resources.get(resource_id).ok_or_else(|| {
                validation(
                    format!("capabilities.{}.required_resources", config.contract),
                    format!("unknown resource `{resource_id}`"),
                )
            })?;
            require(
                resource.owner == config.owner,
                format!("capabilities.{}.required_resources", config.contract),
                format!("resource `{resource_id}` belongs to `{}`", resource.owner),
            )?;
        }
        let local_locks = unique_nonblank(
            config.local_locks,
            &format!("capabilities.{}.local_locks", config.contract),
        )?;
        require(
            supports_artifacts || config.artifact_operation.is_none(),
            format!("capabilities.{}.artifact_operation", config.contract),
            format!("requires schema `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        require(
            !requires_readiness || config.readiness.is_some(),
            format!("capabilities.{}.readiness", config.contract),
            format!("is required by schema `{CONFIG_SCHEMA_V0_4}`"),
        )?;
        require(
            requires_readiness || config.readiness.is_none(),
            format!("capabilities.{}.readiness", config.contract),
            format!("requires schema `{CONFIG_SCHEMA_V0_4}`"),
        )?;
        let readiness = config
            .readiness
            .map(|readiness| {
                compile_capability_readiness(
                    readiness,
                    &config.contract,
                    &config.owner,
                    connections,
                )
            })
            .transpose()?;
        let workflow = compile_workflow(config.workflow, &config.owner, connections)?;
        let contract = config.contract.clone();
        let capability = CompiledCapability {
            contract: config.contract,
            kind: config.kind,
            owner: config.owner,
            required_resources,
            local_locks,
            artifact_operation: config.artifact_operation,
            readiness,
            workflow,
        };
        insert_unique(
            &mut capabilities,
            contract,
            capability,
            "capabilities.contract",
        )?;
    }
    Ok(capabilities)
}

/// Compiles one complete workflow and enforces unique step identities.
pub(super) fn compile_workflow(
    config: WorkflowConfig,
    owner: &str,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<CompiledWorkflow, CatalogError> {
    validate_expression(&config.local_handle).map_err(|source| CatalogError::Mapping {
        step: "local_handle".to_string(),
        source,
    })?;
    require(
        !config.execute.is_empty(),
        "workflow.execute",
        "must not be empty",
    )?;
    require(
        !config.status.is_empty(),
        "workflow.status",
        "must not be empty",
    )?;
    require(
        !config.cancel.is_empty(),
        "workflow.cancel",
        "must not be empty",
    )?;
    require(
        config.poll_interval_ms > 0,
        "workflow.poll_interval_ms",
        "must be non-zero",
    )?;
    validate_step_sources(&config.execute, false, "workflow.execute")?;
    validate_step_sources(&config.status, true, "workflow.status")?;
    validate_step_sources(&config.cancel, true, "workflow.cancel")?;
    let execute_ids = config
        .execute
        .iter()
        .map(|step| step.id.as_str())
        .collect::<BTreeSet<_>>();
    validate_handle_expression(&config.local_handle, &execute_ids, "workflow.local_handle")?;
    let status_ids = config
        .status
        .iter()
        .map(|step| step.id.as_str())
        .collect::<BTreeSet<_>>();
    validate_status_pointer(
        &config.execution_state.state_pointer,
        &status_ids,
        "execution_state.state_pointer",
    )?;
    if let Some(pointer) = &config.execution_state.reason_pointer {
        validate_status_pointer(pointer, &status_ids, "execution_state.reason_pointer")?;
    }
    let mut step_ids = BTreeSet::new();
    let execute = compile_steps(config.execute, owner, connections, &mut step_ids)?;
    let status = compile_steps(config.status, owner, connections, &mut step_ids)?;
    let cancel = compile_steps(config.cancel, owner, connections, &mut step_ids)?;
    let execution_state = compile_execution_state(config.execution_state)?;
    Ok(CompiledWorkflow {
        execute,
        status,
        cancel,
        local_handle: config.local_handle,
        poll_interval_ms: config.poll_interval_ms,
        execution_state,
    })
}

/// Requires the durable local handle to derive from a completed execute response.
pub(super) fn validate_handle_expression(
    expression: &crate::ValueExpressionConfig,
    execute_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    match expression {
        crate::ValueExpressionConfig::Pointer { pointer }
            if execute_steps
                .iter()
                .any(|step| pointer_targets_step(pointer, step)) =>
        {
            Ok(())
        }
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            for argument in arguments {
                validate_handle_expression(argument, execute_steps, field)?;
            }
            Ok(())
        }
        _ => Err(validation(
            field,
            "must derive from a configured execute-step response",
        )),
    }
}

/// Validates request expressions against only facts available before each ordered step.
pub(super) fn validate_step_sources(
    steps: &[WorkflowStepConfig],
    local_handle_available: bool,
    field: &str,
) -> Result<(), CatalogError> {
    let mut completed = BTreeSet::new();
    for step in steps {
        for binding in &step.request.bindings {
            validate_expression_sources(
                &binding.value,
                local_handle_available,
                &completed,
                &format!("{field}.{}.request", step.id),
            )?;
        }
        completed.insert(step.id.as_str());
    }
    Ok(())
}

/// Validates one expression tree without allowing future or cross-phase step references.
pub(super) fn validate_expression_sources(
    expression: &crate::ValueExpressionConfig,
    local_handle_available: bool,
    completed_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    match expression {
        crate::ValueExpressionConfig::Constant { .. } => Ok(()),
        crate::ValueExpressionConfig::Pointer { pointer } => {
            if pointer == "/invocation" || pointer.starts_with("/invocation/") {
                return Ok(());
            }
            if pointer == "/artifacts" || pointer.starts_with("/artifacts/") {
                return Ok(());
            }
            if local_handle_available
                && (pointer == "/local_handle" || pointer.starts_with("/local_handle/"))
            {
                return Ok(());
            }
            if completed_steps
                .iter()
                .any(|step| pointer_targets_step(pointer, step))
            {
                return Ok(());
            }
            Err(validation(
                field,
                format!("mapping source `{pointer}` is unavailable at this step"),
            ))
        }
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            for argument in arguments {
                validate_expression_sources(
                    argument,
                    local_handle_available,
                    completed_steps,
                    field,
                )?;
            }
            Ok(())
        }
    }
}

/// Requires execution state mappings to read a status-step response.
pub(super) fn validate_status_pointer(
    pointer: &str,
    status_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        status_steps
            .iter()
            .any(|step| pointer_targets_step(pointer, step)),
        field,
        "must reference a configured status step",
    )
}

/// Returns whether one JSON Pointer targets a specific configured step.
pub(super) fn pointer_targets_step(pointer: &str, step: &str) -> bool {
    let escaped = escape_pointer_segment(step);
    let prefix = format!("/steps/{escaped}");
    pointer == prefix || pointer.starts_with(&format!("{prefix}/"))
}

/// Escapes one JSON Pointer path segment.
pub(super) fn escape_pointer_segment(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

/// Compiles an ordered step list against connection ownership and driver kind.
pub(super) fn compile_steps(
    configs: Vec<WorkflowStepConfig>,
    owner: &str,
    connections: &BTreeMap<String, CompiledConnection>,
    step_ids: &mut BTreeSet<String>,
) -> Result<Vec<CompiledWorkflowStep>, CatalogError> {
    let mut steps = Vec::with_capacity(configs.len());
    for config in configs {
        validate_identity(&config.id, "workflow.step.id")?;
        require(
            step_ids.insert(config.id.clone()),
            "workflow.step.id",
            format!("duplicate step `{}`", config.id),
        )?;
        let connection = connections.get(&config.connection).ok_or_else(|| {
            validation(
                format!("workflow.{}.connection", config.id),
                format!("unknown connection `{}`", config.connection),
            )
        })?;
        require(
            connection.owner() == owner,
            format!("workflow.{}.connection", config.id),
            format!(
                "connection `{}` belongs to `{}`, not capability owner `{owner}`",
                config.connection,
                connection.owner()
            ),
        )?;
        validate_operation(&config.operation, connection, &config.id)?;
        let request = CompiledRequestMapping::compile(config.request).map_err(|source| {
            CatalogError::Mapping {
                step: config.id.clone(),
                source,
            }
        })?;
        steps.push(CompiledWorkflowStep {
            id: config.id,
            connection: config.connection,
            operation: config.operation,
            request,
        });
    }
    Ok(steps)
}

/// Validates fixed operation syntax and its connection driver family.
pub(super) fn validate_operation(
    operation: &LocalOperationConfig,
    connection: &CompiledConnection,
    step_id: &str,
) -> Result<(), CatalogError> {
    let field = format!("workflow.{step_id}.operation");
    match (connection.driver_kind(), operation) {
        (DriverKind::Http, LocalOperationConfig::Http { method, path }) => {
            require(
                matches!(method.as_str(), "GET" | "POST" | "PUT" | "PATCH" | "DELETE"),
                &field,
                "HTTP method must be GET, POST, PUT, PATCH, or DELETE",
            )?;
            require(
                path.starts_with('/')
                    && !path.contains(['{', '}', '$', '?', '#'])
                    && !path.contains(".."),
                &field,
                "HTTP path must be a fixed absolute path without templates, query, or traversal",
            )
        }
        (
            DriverKind::Grpc,
            LocalOperationConfig::GrpcUnary { service, method }
            | LocalOperationConfig::GrpcServerStream { service, method },
        ) => {
            validate_fixed_symbol(service, &field, true)?;
            validate_fixed_symbol(method, &field, false)?;
            if let CompiledConnection::Grpc {
                descriptor_set: Some(path),
                ..
            } = connection
            {
                validate_grpc_method_descriptor(
                    path,
                    service,
                    method,
                    matches!(operation, LocalOperationConfig::GrpcServerStream { .. }),
                    &field,
                )?;
            }
            Ok(())
        }
        (DriverKind::Mcp, LocalOperationConfig::McpTool { tool }) => {
            validate_fixed_symbol(tool, &field, true)
        }
        _ => Err(validation(
            field,
            "operation kind does not match connection driver",
        )),
    }
}

/// Decodes one configured descriptor set before the node opens any local connection.
pub(super) fn validate_descriptor_set(path: &Path, field: &str) -> Result<(), CatalogError> {
    let bytes = std::fs::read(path)
        .map_err(|error| validation(field, format!("cannot read `{}`: {error}", path.display())))?;
    DescriptorPool::decode(bytes.as_slice()).map_err(|error| {
        validation(
            field,
            format!(
                "cannot decode gRPC descriptor set `{}`: {error}",
                path.display()
            ),
        )
    })?;
    Ok(())
}

/// Confirms that one descriptor-backed gRPC workflow step names an exact method shape.
pub(super) fn validate_grpc_method_descriptor(
    path: &Path,
    service_name: &str,
    method_name: &str,
    configured_server_streaming: bool,
    field: &str,
) -> Result<(), CatalogError> {
    let bytes = std::fs::read(path)
        .map_err(|error| validation(field, format!("cannot read `{}`: {error}", path.display())))?;
    let pool = DescriptorPool::decode(bytes.as_slice()).map_err(|error| {
        validation(
            field,
            format!(
                "cannot decode gRPC descriptor set `{}`: {error}",
                path.display()
            ),
        )
    })?;
    let service = pool.get_service_by_name(service_name).ok_or_else(|| {
        validation(
            field,
            format!("gRPC service `{service_name}` is absent from the descriptor set"),
        )
    })?;
    let method = service
        .methods()
        .find(|descriptor| descriptor.name() == method_name)
        .ok_or_else(|| {
            validation(
                field,
                format!(
                    "gRPC method `{service_name}.{method_name}` is absent from the descriptor set"
                ),
            )
        })?;
    require(
        !method.is_client_streaming(),
        field,
        format!("gRPC method `{service_name}.{method_name}` uses unsupported client streaming"),
    )?;
    require(
        method.is_server_streaming() == configured_server_streaming,
        field,
        format!(
            "configured streaming mode does not match gRPC method `{service_name}.{method_name}`"
        ),
    )
}

/// Compiles a disjoint local-state lookup table.
pub(super) fn compile_execution_state(
    config: ExecutionStateMappingConfig,
) -> Result<CompiledExecutionStateMapping, CatalogError> {
    validate_pointer(&config.state_pointer).map_err(|source| CatalogError::Mapping {
        step: "execution_state".to_string(),
        source,
    })?;
    if let Some(reason_pointer) = &config.reason_pointer {
        validate_pointer(reason_pointer).map_err(|source| CatalogError::Mapping {
            step: "execution_state".to_string(),
            source,
        })?;
    }
    require(
        !config.running.is_empty(),
        "execution_state.running",
        "must not be empty",
    )?;
    require(
        !config.completed.is_empty(),
        "execution_state.completed",
        "must not be empty",
    )?;
    require(
        !config.failed.is_empty(),
        "execution_state.failed",
        "must not be empty",
    )?;
    require(
        !config.cancelled.is_empty(),
        "execution_state.cancelled",
        "must not be empty",
    )?;
    let mut states = BTreeMap::new();
    insert_states(
        &mut states,
        config.accepted,
        MappedExecutionPhase::Accepted,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.running,
        MappedExecutionPhase::Running,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.completed,
        MappedExecutionPhase::Completed,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.failed,
        MappedExecutionPhase::Failed,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.cancelled,
        MappedExecutionPhase::Cancelled,
        config.case_sensitive,
    )?;
    Ok(CompiledExecutionStateMapping {
        state_pointer: config.state_pointer,
        reason_pointer: config.reason_pointer,
        states,
        case_sensitive: config.case_sensitive,
    })
}

/// Inserts one phase's local states while rejecting ambiguous mappings.
pub(super) fn insert_states(
    states: &mut BTreeMap<String, MappedExecutionPhase>,
    values: Vec<String>,
    phase: MappedExecutionPhase,
    case_sensitive: bool,
) -> Result<(), CatalogError> {
    for value in values {
        validate_identity(&value, "execution_state.value")?;
        let key = if case_sensitive {
            value
        } else {
            value.to_ascii_lowercase()
        };
        require(
            states.insert(key.clone(), phase).is_none(),
            "execution_state",
            format!("local state `{key}` maps to more than one phase"),
        )?;
    }
    Ok(())
}

/// Compiles environment-only credentials without reading or retaining their values.
pub(super) fn compile_credentials(
    configs: BTreeMap<String, crate::CredentialSourceConfig>,
    field: &str,
) -> Result<BTreeMap<String, String>, CatalogError> {
    configs
        .into_iter()
        .map(|(name, source)| {
            validate_identity(&name, field)?;
            validate_identity(&source.env, field)?;
            Ok((name, source.env))
        })
        .collect()
}

/// Validates a remote RoboGuide Server endpoint without requiring loopback.
pub(super) fn validate_server_endpoint(endpoint: &str) -> Result<(), CatalogError> {
    let url = url::Url::parse(endpoint)
        .map_err(|error| validation("server_endpoint", error.to_string()))?;
    require(
        matches!(url.scheme(), "http" | "https") && url.host().is_some(),
        "server_endpoint",
        "must be an absolute http(s) gRPC endpoint",
    )
}

/// Validates a local endpoint and forbids configuration-driven remote calls.
pub(super) fn validate_local_endpoint(endpoint: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !endpoint.contains(['{', '}', '$']),
        field,
        "must be fixed and cannot contain template syntax",
    )?;
    let url = url::Url::parse(endpoint).map_err(|error| validation(field, error.to_string()))?;
    if url.scheme() == "unix" {
        return require(
            url.path().starts_with('/') && !url.path().is_empty(),
            field,
            "Unix endpoint must use an absolute socket path",
        );
    }
    require(
        matches!(url.scheme(), "http" | "https"),
        field,
        "must use http(s) or unix scheme",
    )?;
    require(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        field,
        "must not contain inline credentials, query, or fragment",
    )?;
    let loopback = match url.host() {
        Some(url::Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    };
    require(
        loopback,
        field,
        "must target localhost or a loopback address",
    )
}

/// Validates canonical `namespace.name@version` identity.
pub(super) fn validate_contract(contract: &str) -> Result<(), CatalogError> {
    let Some((qualified_name, version)) = contract.split_once('@') else {
        return Err(validation(
            "capabilities.contract",
            "must use namespace.name@version",
        ));
    };
    let Some((namespace, name)) = qualified_name.rsplit_once('.') else {
        return Err(validation(
            "capabilities.contract",
            "must use namespace.name@version",
        ));
    };
    require(
        !namespace.trim().is_empty()
            && !name.trim().is_empty()
            && !version.trim().is_empty()
            && namespace
                .split('.')
                .all(|segment| !segment.is_empty() && !segment.chars().any(char::is_whitespace))
            && !namespace.contains('@')
            && !name.contains(['.', '@'])
            && !name.chars().any(char::is_whitespace)
            && !version.contains('@'),
        "capabilities.contract",
        "must contain non-empty namespace, name, and version",
    )
}

/// Validates a fixed service, method, or tool symbol without templates.
pub(super) fn validate_fixed_symbol(
    value: &str,
    field: &str,
    allow_dots: bool,
) -> Result<(), CatalogError> {
    let valid = !value.is_empty()
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric()
                || character == '_'
                || (allow_dots && matches!(character, '.' | '-' | '/'))
        });
    require(
        valid,
        field,
        "contains invalid or dynamic symbol characters",
    )
}

/// Validates a non-empty identity without surrounding whitespace.
pub(super) fn validate_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value,
        field,
        "must be non-empty and have no surrounding whitespace",
    )
}

/// Returns a unique set of validated configured identities.
pub(super) fn unique_nonblank(
    values: Vec<String>,
    field: &str,
) -> Result<BTreeSet<String>, CatalogError> {
    let mut unique = BTreeSet::new();
    for value in values {
        validate_identity(&value, field)?;
        require(
            unique.insert(value.clone()),
            field,
            format!("duplicate value `{value}`"),
        )?;
    }
    Ok(unique)
}

/// Inserts one unique key into a compiled catalog map.
pub(super) fn insert_unique<T>(
    values: &mut BTreeMap<String, T>,
    key: String,
    value: T,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        values.insert(key.clone(), value).is_none(),
        field,
        format!("duplicate identity `{key}`"),
    )
}

/// Resolves one deployment path without requiring it to already exist.
pub(super) fn resolve_path(directory: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        directory.join(path)
    }
}

/// Converts a structured reason value into deterministic human-readable text.
pub(super) fn value_to_reason(value: &serde_json::Value) -> String {
    value
        .as_str()
        .map(ToString::to_string)
        .unwrap_or_else(|| value.to_string())
}

/// Creates one validation error.
pub(super) fn validation(field: impl Into<String>, reason: impl Into<String>) -> CatalogError {
    CatalogError::Validation {
        field: field.into(),
        reason: reason.into(),
    }
}

/// Returns success when an invariant holds or a field-specific error otherwise.
pub(super) fn require(
    condition: bool,
    field: impl Into<String>,
    reason: impl Into<String>,
) -> Result<(), CatalogError> {
    if condition {
        Ok(())
    } else {
        Err(validation(field, reason))
    }
}
