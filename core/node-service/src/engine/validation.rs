//! Invocation, Memory, artifact, resource, and journal snapshot validation.

use super::*;

/// Validates and bounds the exact publish-eligible set returned by one Memory provider.
///
/// This function deliberately has no ledger lookup or promotion behavior: every returned
/// manifest must originate in the provider response. The query and live-scope checks are
/// RoboGuide safety filters, not a sharing-selection policy.
pub(super) fn accept_provider_discovery(
    mut manifests: Vec<MemoryArtifactManifest>,
    query: &MemoryQuery,
    invocation: &serde_json::Value,
) -> Result<Vec<MemoryArtifactManifest>, EngineError> {
    for manifest in &manifests {
        manifest
            .validate()
            .map_err(|error| EngineError::Memory(error.to_string()))?;
    }
    manifests.retain(|manifest| {
        query.matches(manifest) && memory_scope_visible_in_context(manifest, invocation)
    });
    manifests.sort_by(|left, right| left.selector().cmp(right.selector()));
    if manifests
        .windows(2)
        .any(|pair| pair[0].selector() == pair[1].selector() && pair[0] != pair[1])
    {
        return Err(EngineError::Memory(
            "Memory discovery returned conflicting immutable manifests".to_string(),
        ));
    }
    manifests.dedup();
    Ok(manifests)
}

/// Requires concrete ExecutionGroup Memory to match the live operation context.
pub(super) fn validate_memory_operation_scope(
    manifest: &MemoryArtifactManifest,
    invocation: &serde_json::Value,
) -> Result<(), EngineError> {
    let domain::MemoryScope::ExecutionGroup(group_id) = manifest.scope() else {
        return Ok(());
    };
    let context_group = invocation
        .get("group_id")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty());
    if context_group == Some(group_id.as_str()) {
        Ok(())
    } else {
        Err(EngineError::Configuration(
            "ExecutionGroup Memory requires the matching live execution group context".to_string(),
        ))
    }
}

/// Applies the same live-group invariant to provider-local discovery filters.
pub(super) fn validate_memory_query_scope(
    query: &MemoryQuery,
    invocation: &serde_json::Value,
) -> Result<(), EngineError> {
    let Some(domain::MemoryScope::ExecutionGroup(group_id)) = query.scope.as_ref() else {
        return Ok(());
    };
    let context_group = invocation
        .get("group_id")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.is_empty());
    if context_group == Some(group_id.as_str()) {
        Ok(())
    } else {
        Err(EngineError::Configuration(
            "ExecutionGroup Memory discovery requires the matching live execution group context"
                .to_string(),
        ))
    }
}

/// Hides Group-scoped metadata unless the caller presents the exact live logical Group identity.
pub(super) fn memory_scope_visible_in_context(
    manifest: &MemoryArtifactManifest,
    invocation: &serde_json::Value,
) -> bool {
    match manifest.scope() {
        domain::MemoryScope::ExecutionGroup(group_id) => invocation
            .get("group_id")
            .and_then(serde_json::Value::as_str)
            .is_some_and(|context_group| context_group == group_id.as_str()),
        domain::MemoryScope::Local | domain::MemoryScope::Global => true,
    }
}

/// Returns the node-config spelling of one generic Memory kind.
pub(super) const fn memory_kind_name(kind: domain::MemoryKind) -> &'static str {
    match kind {
        domain::MemoryKind::Execution => "execution",
        domain::MemoryKind::Spatial => "spatial",
        domain::MemoryKind::Semantic => "semantic",
        domain::MemoryKind::Experience => "experience",
        domain::MemoryKind::Artifact => "artifact",
    }
}

/// Resolves an EAIOS export handoff path within the configured Node-managed root.
pub(super) fn resolve_memory_export_path(
    root: &std::path::Path,
    relative: &str,
) -> Result<PathBuf, EngineError> {
    let relative = std::path::Path::new(relative);
    if relative.as_os_str().is_empty()
        || relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err(EngineError::Memory(
            "Memory export path must be relative to the Node-managed handoff root".to_string(),
        ));
    }
    let canonical_root = std::fs::canonicalize(root).map_err(EngineError::Io)?;
    let candidate = std::fs::canonicalize(root.join(relative)).map_err(EngineError::Io)?;
    if !candidate.starts_with(&canonical_root) || !candidate.is_file() {
        return Err(EngineError::Memory(
            "Memory export path escapes the Node-managed handoff root or is not a regular file"
                .to_string(),
        ));
    }
    Ok(candidate)
}

/// Returns whether an artifact completion error conclusively proves no successful finalization.
pub(super) fn artifact_error_is_deterministic(error: &EngineError) -> bool {
    match error {
        EngineError::Artifact(ArtifactError::Status { status, .. }) => {
            !matches!(status.as_u16(), 408 | 425 | 429 | 500..=599)
        }
        EngineError::Artifact(error) => !matches!(
            error,
            ArtifactError::RemoteOutcomeUnknown { .. } | ArtifactError::Http(_)
        ),
        EngineError::Protocol(_)
        | EngineError::Configuration(_)
        | EngineError::UnsupportedCapability(_)
        | EngineError::MissingCommittedResource
        | EngineError::ExecutionConflict(_)
        | EngineError::LocalLockConflict { .. }
        | EngineError::UnknownExecution(_)
        | EngineError::Catalog(_)
        | EngineError::Mapping(_)
        | EngineError::Json(_) => true,
        EngineError::ReconciliationRequired(_)
        | EngineError::LockState
        | EngineError::Io(_)
        | EngineError::Journal(_)
        | EngineError::Driver(_)
        | EngineError::Memory(_) => false,
    }
}

/// Returns whether import failure conclusively proves a semantic/configuration rejection.
pub(super) fn memory_import_error_is_deterministic(error: &EngineError) -> bool {
    matches!(
        error,
        EngineError::Configuration(_)
            | EngineError::Catalog(_)
            | EngineError::Mapping(_)
            | EngineError::Json(_)
            | EngineError::Protocol(_)
    )
}

/// Validates canonical command identity before any lock or local side effect.
pub(super) fn validate_invocation_identity(
    execution_id: &str,
    invocation: &CanonicalInvocation,
) -> Result<(), EngineError> {
    if execution_id.trim().is_empty()
        || invocation.mission_id.trim().is_empty()
        || invocation.task_id.trim().is_empty()
        || invocation.group_id.trim().is_empty()
        || invocation.role_id.trim().is_empty()
        || invocation.capability_contract.trim().is_empty()
    {
        Err(EngineError::Protocol(
            "execution and canonical invocation identities must be nonblank".to_string(),
        ))
    } else {
        Ok(())
    }
}

/// Parses and validates the complete artifact intent fixed by capability configuration.
pub(super) fn artifact_directive<'a>(
    invocation: &'a serde_json::Value,
    expected_operation: Option<ArtifactOperation>,
) -> Result<Option<ArtifactDirective<'a>>, EngineError> {
    let parameters = invocation
        .get("parameters")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| EngineError::Protocol("invocation parameters must be an object".into()))?;
    let artifact_fields = [
        "artifact_slot",
        "artifact_operation",
        "map_id",
        "revision_id",
        "spatial_anchor_id",
    ];
    let carries_artifact_intent = artifact_fields
        .iter()
        .any(|field| parameters.contains_key(*field));
    match (expected_operation, carries_artifact_intent) {
        (None, false) => return Ok(None),
        (None, true) => {
            return Err(EngineError::Protocol(
                "capability does not permit artifact parameters".to_string(),
            ));
        }
        (Some(_), false) => {
            return Err(EngineError::Protocol(
                "artifact capability requires artifact_slot, artifact_operation, map_id, revision_id, and spatial_anchor_id"
                    .to_string(),
            ));
        }
        (Some(_), true) => {}
    }
    let required_string = |field: &'static str| -> Result<&'a str, EngineError> {
        parameters
            .get(field)
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.trim().is_empty() && *value == value.trim())
            .ok_or_else(|| {
                EngineError::Protocol(format!("artifact intent requires nonblank string {field}"))
            })
    };
    let slot = parameters
        .get("artifact_slot")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty() && *value == value.trim())
        .ok_or_else(|| {
            EngineError::Protocol(
                "artifact_slot must be a nonblank string when an artifact operation is used".into(),
            )
        })?;
    let operation = match required_string("artifact_operation")? {
        "prepare-output" => ArtifactOperation::PrepareOutput,
        "publish" => ArtifactOperation::Publish,
        "import" => ArtifactOperation::Import,
        "verify" => ArtifactOperation::Verify,
        _ => {
            return Err(EngineError::Protocol(
                "artifact_operation must be prepare-output/publish/import/verify".into(),
            ));
        }
    };
    if Some(operation) != expected_operation {
        return Err(EngineError::Protocol(format!(
            "artifact_operation {} differs from capability-configured {}",
            operation.as_str(),
            expected_operation
                .expect("artifact operation was required above")
                .as_str()
        )));
    }
    Ok(Some(ArtifactDirective {
        slot,
        operation,
        map_id: required_string("map_id")?,
        revision_id: required_string("revision_id")?,
        spatial_anchor_id: required_string("spatial_anchor_id")?,
    }))
}

/// Returns the typed Mission identity carried by one canonical invocation.
pub(super) fn invocation_mission_id(
    invocation: &serde_json::Value,
) -> Result<MissionId, EngineError> {
    let mission_id = invocation
        .get("mission_id")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| EngineError::Protocol("invocation mission_id is missing".into()))?;
    MissionId::new(mission_id.to_string())
        .map_err(ArtifactError::Domain)
        .map_err(EngineError::Artifact)
}

/// Builds immutable output provenance from the execution that produced the bytes.
pub(super) fn artifact_provenance(
    invocation: &serde_json::Value,
    execution_id: &str,
    node_id: &str,
    local_system_id: &str,
) -> Result<ArtifactProvenance, EngineError> {
    let mission = invocation_mission_id(invocation)?;
    let task_id = invocation
        .get("task_id")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| EngineError::Protocol("invocation task_id is missing".to_string()))?;
    let task_ref = TaskRef::new(
        mission.clone(),
        TaskId::new(task_id.to_string()).map_err(ArtifactError::Domain)?,
    );
    Ok(ArtifactProvenance {
        producer_node_id: NodeId::new(node_id.to_string()).map_err(ArtifactError::Domain)?,
        producer_local_system_id: Some(
            LocalSystemId::new(local_system_id.to_string()).map_err(ArtifactError::Domain)?,
        ),
        source_mission_id: mission,
        source_execution_id: Some(execution_id.to_string()),
        source_task_ref: Some(task_ref),
        created_at: TimestampMs::new(current_timestamp_ms()),
        parent_revision_id: None,
    })
}

/// Confirms canonical map/revision parameters select the configured immutable binding.
pub(super) fn validate_artifact_binding_reference(
    directive: ArtifactDirective<'_>,
    expected_map_id: &str,
    expected_revision_id: &str,
    expected_anchor_id: Option<&str>,
) -> Result<(), EngineError> {
    if directive.map_id != expected_map_id || directive.revision_id != expected_revision_id {
        return Err(EngineError::Protocol(format!(
            "artifact intent {}/{} differs from configured binding {expected_map_id}/{expected_revision_id}",
            directive.map_id, directive.revision_id
        )));
    }
    if let Some(expected_anchor_id) = expected_anchor_id
        && directive.spatial_anchor_id != expected_anchor_id
    {
        return Err(EngineError::Protocol(format!(
            "artifact intent anchor {} differs from configured binding {}",
            directive.spatial_anchor_id, expected_anchor_id
        )));
    }
    Ok(())
}

/// Confirms an input manifest matches the intent's selector and fixed spatial anchor.
pub(super) fn validate_input_manifest_reference(
    directive: ArtifactDirective<'_>,
    manifest: &MapArtifactManifest,
) -> Result<(), EngineError> {
    if manifest.selector().map_id().as_str() != directive.map_id
        || manifest.selector().revision_id().as_str() != directive.revision_id
        || manifest.anchor_id().as_str() != directive.spatial_anchor_id
    {
        return Err(EngineError::Protocol(
            "artifact manifest selector or spatial anchor differs from execution intent"
                .to_string(),
        ));
    }
    Ok(())
}

/// Returns the local wall-clock millisecond used for artifact provenance evidence.
pub(super) fn current_timestamp_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default()
}

/// Validates Control commitments against the compiled node resource catalog.
pub(super) fn validate_resources(
    catalog: &CompiledLocalCatalog,
    capability: &CompiledCapability,
    resource_ids: &[String],
) -> Result<(), EngineError> {
    let supplied = resource_ids.iter().collect::<BTreeSet<_>>();
    if supplied.len() != resource_ids.len() {
        return Err(EngineError::Protocol(
            "Execute resource IDs must be unique".to_string(),
        ));
    }
    if supplied
        .iter()
        .any(|resource_id| !catalog.resources().contains_key(resource_id.as_str()))
    {
        return Err(EngineError::Protocol(
            "Execute references an unknown node resource".to_string(),
        ));
    }
    if !capability
        .required_resources()
        .iter()
        .all(|required| supplied.contains(required))
    {
        return Err(EngineError::MissingCommittedResource);
    }
    Ok(())
}

/// Builds deterministic local lock keys for one execution, including its artifact binding.
pub(super) fn lock_keys(
    capability: &CompiledCapability,
    resource_ids: &[String],
    invocation: &serde_json::Value,
) -> Result<BTreeSet<String>, EngineError> {
    let mut keys = resource_ids
        .iter()
        .map(|resource| format!("resource:{resource}"))
        .chain(
            capability
                .local_locks()
                .iter()
                .map(|lock| format!("local:{lock}")),
        )
        .collect::<BTreeSet<_>>();
    if let Some(directive) = artifact_directive(invocation, capability.artifact_operation())? {
        keys.insert(format!("artifact-binding:{}", directive.slot));
    }
    Ok(keys)
}

/// Converts a canonical protobuf invocation into stable JSON mapping context.
pub(super) fn canonical_invocation_json(
    invocation: &CanonicalInvocation,
    resource_ids: &[String],
) -> Result<serde_json::Value, EngineError> {
    let parameters = invocation
        .parameters
        .iter()
        .map(|(name, value)| {
            let value = value
                .value
                .as_ref()
                .ok_or_else(|| EngineError::Protocol("invocation scalar is empty".to_string()))?;
            use integration::grpc::v0_4::scalar_value::Value;
            let value = match value {
                Value::BoolValue(value) => serde_json::Value::Bool(*value),
                Value::IntegerValue(value) => serde_json::Value::Number((*value).into()),
                Value::FloatValue(value) => serde_json::Number::from_f64(*value)
                    .map(serde_json::Value::Number)
                    .ok_or_else(|| {
                        EngineError::Protocol("invocation float is not finite".into())
                    })?,
                Value::StringValue(value) => serde_json::Value::String(value.clone()),
            };
            Ok((name.clone(), value))
        })
        .collect::<Result<BTreeMap<_, _>, EngineError>>()?;
    Ok(serde_json::json!({
        "mission_id": invocation.mission_id,
        "task_id": invocation.task_id,
        "group_id": invocation.group_id,
        "role_id": invocation.role_id,
        "capability_contract": invocation.capability_contract,
        "parameters": parameters,
        "resource_ids": resource_ids,
    }))
}

/// Decodes canonical JSON retained in the durable journal.
pub(super) fn decode_invocation_json(bytes: &[u8]) -> Result<serde_json::Value, EngineError> {
    serde_json::from_slice(bytes).map_err(EngineError::Json)
}

/// Converts one journal record into a Node Protocol reconnect snapshot.
pub(super) fn snapshot_from_record(
    record: JournalExecution,
) -> Result<ExecutionSnapshot, EngineError> {
    let phase = match record.status() {
        JournalStatus::Dispatching | JournalStatus::ReconciliationRequired => {
            ExecutionPhase::Unknown
        }
        JournalStatus::Accepted => ExecutionPhase::Accepted,
        JournalStatus::Running => ExecutionPhase::Started,
        JournalStatus::Completed => ExecutionPhase::Completed,
        JournalStatus::Failed => ExecutionPhase::Failed,
        JournalStatus::Cancelled => ExecutionPhase::Cancelled,
    };
    Ok(ExecutionSnapshot {
        session_id: String::new(),
        execution_id: record.execution_id().to_string(),
        last_sequence: record.sequence(),
        phase: phase as i32,
        reason: record.reason().to_string(),
    })
}

/// Computes a stable lowercase SHA-256 digest for workflow identity.
pub(super) fn digest_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

/// Hashes capability behavior, referenced connections, and selected artifact metadata.
pub(crate) fn workflow_digest(
    catalog: &CompiledLocalCatalog,
    capability: &CompiledCapability,
    invocation: &serde_json::Value,
) -> Result<String, EngineError> {
    let mut identity = format!("{capability:?}");
    let connection_ids = capability
        .workflow()
        .execute()
        .iter()
        .chain(capability.workflow().status())
        .chain(capability.workflow().cancel())
        .map(crate::CompiledWorkflowStep::connection)
        .collect::<BTreeSet<_>>();
    for connection_id in connection_ids {
        let connection = catalog.connections().get(connection_id).ok_or_else(|| {
            EngineError::Configuration(format!(
                "workflow references unavailable connection {connection_id}"
            ))
        })?;
        identity.push_str(&format!("\n{connection:?}"));
    }
    if let Some(directive) = artifact_directive(invocation, capability.artifact_operation())? {
        let artifacts = catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration(
                "artifact capability requires configured artifact service".to_string(),
            )
        })?;
        identity.push_str(&format!(
            "\nartifact-service:{:?}:{}:{}:{}:{}:{}\nartifact-intent:{}:{}:{}:{}:{}",
            artifacts.cache_directory(),
            artifacts.endpoint(),
            artifacts.max_artifact_bytes(),
            artifacts.chunk_size_bytes(),
            artifacts.connect_timeout_ms(),
            artifacts.read_timeout_ms(),
            directive.operation.as_str(),
            directive.slot,
            directive.map_id,
            directive.revision_id,
            directive.spatial_anchor_id,
        ));
        match directive.operation {
            ArtifactOperation::PrepareOutput | ArtifactOperation::Publish => {
                let binding = artifacts
                    .output_bindings()
                    .get(directive.slot)
                    .ok_or_else(|| {
                        EngineError::Configuration(format!(
                            "artifact output binding `{}` is not configured",
                            directive.slot
                        ))
                    })?;
                validate_artifact_binding_reference(
                    directive,
                    &binding.map_id,
                    &binding.revision_id,
                    Some(&binding.spatial_anchor_id),
                )?;
                identity.push_str(&format!("\nartifact-output:{binding:?}"));
            }
            ArtifactOperation::Import | ArtifactOperation::Verify => {
                let binding = artifacts
                    .input_bindings()
                    .get(directive.slot)
                    .ok_or_else(|| {
                        EngineError::Configuration(format!(
                            "artifact input binding `{}` is not configured",
                            directive.slot
                        ))
                    })?;
                validate_artifact_binding_reference(
                    directive,
                    &binding.map_id,
                    &binding.revision_id,
                    None,
                )?;
                identity.push_str(&format!("\nartifact-input:{binding:?}"));
            }
        }
    }
    Ok(digest_text(&identity))
}
