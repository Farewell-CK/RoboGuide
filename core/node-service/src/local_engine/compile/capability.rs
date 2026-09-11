//! Capability-profile and canonical-operation compilation.

use super::*;

/// Compiled profile evidence paired with executable operation workflows.
type CompiledCapabilityDeclarations = (
    BTreeMap<String, CompiledCapabilityProfile>,
    BTreeMap<String, CompiledOperation>,
);

/// Compiles legacy combined declarations into explicit profile and operation maps.
pub(super) fn compile_legacy_capabilities(
    configs: Vec<CapabilityBindingConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
    resources: &BTreeMap<String, CompiledResource>,
    supports_artifacts: bool,
    requires_readiness: bool,
) -> Result<CompiledCapabilityDeclarations, CatalogError> {
    let mut operations = BTreeMap::new();
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
            !operations.contains_key(&config.contract),
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
        let operation = CompiledOperation {
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
            &mut operations,
            contract,
            operation,
            "capabilities.contract",
        )?;
    }
    let profiles = operations
        .iter()
        .map(|(contract, operation)| {
            (
                contract.clone(),
                CompiledCapabilityProfile {
                    contract: contract.clone(),
                    kind: operation.kind.clone(),
                    owner: operation.owner.clone(),
                    attributes: BTreeMap::new(),
                    readiness: operation.readiness.clone(),
                },
            )
        })
        .collect();
    Ok((profiles, operations))
}

/// Compiles exact v0.7 capability evidence independently of executable operations.
pub(super) fn compile_capability_profiles(
    configs: Vec<CapabilityProfileConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<BTreeMap<String, CompiledCapabilityProfile>, CatalogError> {
    let mut profiles = BTreeMap::new();
    for config in configs {
        validate_contract(&config.contract)?;
        require(
            matches!(
                config.kind.as_str(),
                "mobility" | "transport" | "compute" | "observation"
            ),
            format!("capability_profiles.{}.kind", config.contract),
            "must be mobility, transport, compute, or observation",
        )?;
        require(
            systems.contains_key(&config.owner),
            format!("capability_profiles.{}.owner", config.contract),
            format!("unknown local system `{}`", config.owner),
        )?;
        let mut attributes = BTreeMap::new();
        for (name, value) in config.attributes {
            require(
                !name.trim().is_empty(),
                format!("capability_profiles.{}.attributes", config.contract),
                "attribute names must be nonblank",
            )?;
            let value = match value {
                serde_json::Value::Bool(value) => domain::ExecutionValue::Bool(value),
                serde_json::Value::Number(value) if value.is_i64() => {
                    domain::ExecutionValue::Integer(value.as_i64().expect("checked integer"))
                }
                serde_json::Value::Number(value) => {
                    let value = value.as_f64().ok_or_else(|| {
                        validation(
                            format!("capability_profiles.{}.attributes.{name}", config.contract),
                            "number must fit the v0.5 scalar profile",
                        )
                    })?;
                    require(
                        value.is_finite(),
                        format!("capability_profiles.{}.attributes.{name}", config.contract),
                        "float must be finite",
                    )?;
                    domain::ExecutionValue::Float(value)
                }
                serde_json::Value::String(value) => domain::ExecutionValue::String(value),
                _ => {
                    return Err(validation(
                        format!("capability_profiles.{}.attributes.{name}", config.contract),
                        "must be a boolean, signed integer, finite number, or string",
                    ));
                }
            };
            attributes.insert(name, value);
        }
        let readiness = compile_capability_readiness(
            config.readiness,
            &config.contract,
            &config.owner,
            connections,
        )?;
        let contract = config.contract.clone();
        insert_unique(
            &mut profiles,
            contract,
            CompiledCapabilityProfile {
                contract: config.contract,
                kind: config.kind,
                owner: config.owner,
                attributes,
                readiness: Some(readiness),
            },
            "capability_profiles.contract",
        )?;
    }
    Ok(profiles)
}

/// Compiles canonical operation mappings without treating them as capability evidence.
pub(super) fn compile_operations(
    configs: Vec<OperationBindingConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
    resources: &BTreeMap<String, CompiledResource>,
    supports_artifacts: bool,
) -> Result<BTreeMap<String, CompiledOperation>, CatalogError> {
    let mut operations = BTreeMap::new();
    for config in configs {
        validate_contract(&config.operation)?;
        require(
            systems.contains_key(&config.owner),
            format!("operations.{}.owner", config.operation),
            format!("unknown local system `{}`", config.owner),
        )?;
        let required_resources = unique_nonblank(
            config.required_resources,
            &format!("operations.{}.required_resources", config.operation),
        )?;
        for resource_id in &required_resources {
            let resource = resources.get(resource_id).ok_or_else(|| {
                validation(
                    format!("operations.{}.required_resources", config.operation),
                    format!("unknown resource `{resource_id}`"),
                )
            })?;
            require(
                resource.owner == config.owner,
                format!("operations.{}.required_resources", config.operation),
                format!("resource `{resource_id}` belongs to `{}`", resource.owner),
            )?;
        }
        let local_locks = unique_nonblank(
            config.local_locks,
            &format!("operations.{}.local_locks", config.operation),
        )?;
        require(
            supports_artifacts || config.artifact_operation.is_none(),
            format!("operations.{}.artifact_operation", config.operation),
            format!("requires schema `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        let workflow = compile_workflow(config.workflow, &config.owner, connections)?;
        let operation = config.operation.clone();
        insert_unique(
            &mut operations,
            operation,
            CompiledOperation {
                contract: config.operation,
                kind: String::new(),
                owner: config.owner,
                required_resources,
                local_locks,
                artifact_operation: config.artifact_operation,
                readiness: None,
                workflow,
            },
            "operations.operation",
        )?;
    }
    Ok(operations)
}
