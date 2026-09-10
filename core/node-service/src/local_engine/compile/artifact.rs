//! Artifact service and static binding compilation.

use super::*;

/// Compiles optional artifact settings and rejects unsafe or ambiguous static bindings.
pub(super) fn compile_artifacts(
    config: Option<ArtifactServiceConfig>,
    config_directory: &Path,
) -> Result<Option<CompiledArtifactService>, CatalogError> {
    let Some(config) = config else {
        return Ok(None);
    };
    validate_server_endpoint(&config.endpoint).map_err(|error| match error {
        CatalogError::Validation { reason, .. } => validation("artifacts.endpoint", reason),
        other => other,
    })?;
    require(
        config.max_artifact_bytes > 0,
        "artifacts.max_artifact_bytes",
        "must be non-zero",
    )?;
    require(
        config.chunk_size_bytes > 0,
        "artifacts.chunk_size_bytes",
        "must be non-zero",
    )?;
    require(
        config.connect_timeout_ms > 0,
        "artifacts.connect_timeout_ms",
        "must be non-zero",
    )?;
    require(
        config.read_timeout_ms > 0,
        "artifacts.read_timeout_ms",
        "must be non-zero",
    )?;
    require(
        !config.cache_directory.as_os_str().is_empty(),
        "artifacts.cache_directory",
        "must not be empty",
    )?;
    let cache_directory = resolve_path(config_directory, &config.cache_directory);
    let mut binding_paths = BTreeMap::new();
    let mut input_bindings = BTreeMap::new();
    for binding in config.input_bindings {
        validate_artifact_binding_identity(&binding.id, "artifacts.input_bindings.id")?;
        validate_artifact_map_identity(&binding.map_id, "artifacts.input_bindings.map_id")?;
        validate_artifact_revision_identity(
            &binding.revision_id,
            "artifacts.input_bindings.revision_id",
        )?;
        validate_relative_artifact_path(
            &binding.target_path,
            "artifacts.input_bindings.target_path",
        )?;
        register_artifact_binding_path(
            &mut binding_paths,
            &binding.target_path,
            "artifacts.input_bindings.target_path",
        )?;
        if let Some(digest) = &binding.content_digest {
            validate_artifact_digest(digest, "artifacts.input_bindings.content_digest")?;
        }
        require(
            input_bindings.insert(binding.id.clone(), binding).is_none(),
            "artifacts.input_bindings.id",
            "duplicate binding identity",
        )?;
    }
    let mut output_bindings = BTreeMap::new();
    for binding in config.output_bindings {
        validate_artifact_binding_identity(&binding.id, "artifacts.output_bindings.id")?;
        require(
            !input_bindings.contains_key(&binding.id),
            "artifacts.output_bindings.id",
            "binding identity is already used by an input binding",
        )?;
        validate_artifact_map_identity(&binding.map_id, "artifacts.output_bindings.map_id")?;
        validate_artifact_revision_identity(
            &binding.revision_id,
            "artifacts.output_bindings.revision_id",
        )?;
        validate_relative_artifact_path(
            &binding.source_path,
            "artifacts.output_bindings.source_path",
        )?;
        register_artifact_binding_path(
            &mut binding_paths,
            &binding.source_path,
            "artifacts.output_bindings.source_path",
        )?;
        for (value, field) in [
            (&binding.media_type, "artifacts.output_bindings.media_type"),
            (
                &binding.format_name,
                "artifacts.output_bindings.format_name",
            ),
            (
                &binding.format_version,
                "artifacts.output_bindings.format_version",
            ),
            (&binding.root_frame, "artifacts.output_bindings.root_frame"),
            (
                &binding.coordinate_convention,
                "artifacts.output_bindings.coordinate_convention",
            ),
            (
                &binding.spatial_anchor_id,
                "artifacts.output_bindings.spatial_anchor_id",
            ),
        ] {
            validate_artifact_text(value, field)?;
        }
        if binding
            .resolution_meters
            .is_some_and(|value| !value.is_finite() || value <= 0.0)
        {
            return Err(validation(
                "artifacts.output_bindings.resolution_meters",
                "must be finite and positive",
            ));
        }
        require(
            output_bindings
                .insert(binding.id.clone(), binding)
                .is_none(),
            "artifacts.output_bindings.id",
            "duplicate binding identity",
        )?;
    }
    Ok(Some(CompiledArtifactService {
        endpoint: config.endpoint,
        cache_directory,
        max_artifact_bytes: config.max_artifact_bytes,
        chunk_size_bytes: config.chunk_size_bytes,
        connect_timeout_ms: config.connect_timeout_ms,
        read_timeout_ms: config.read_timeout_ms,
        input_bindings,
        output_bindings,
    }))
}

/// Reserves one relative artifact path and rejects aliases or file/directory overlap.
pub(super) fn register_artifact_binding_path(
    paths: &mut BTreeMap<PathBuf, String>,
    path: &Path,
    field: &str,
) -> Result<(), CatalogError> {
    if let Some((existing, owner)) = paths
        .iter()
        .find(|(existing, _)| existing.starts_with(path) || path.starts_with(existing))
    {
        return Err(validation(
            field,
            format!(
                "path {} overlaps {} owned by {owner}",
                path.display(),
                existing.display()
            ),
        ));
    }
    paths.insert(path.to_path_buf(), field.to_string());
    Ok(())
}

/// Validates an artifact identity used in a URL or fixed binding.
pub(super) fn validate_artifact_binding_identity(
    value: &str,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value && !value.contains(['/', '\\']),
        field,
        "must be a nonblank path-safe identity",
    )
}

/// Validates a logical map identity using the Domain path-safe selector invariant.
pub(super) fn validate_artifact_map_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    domain::MapId::new(value.to_string())
        .map(|_| ())
        .map_err(|error| validation(field, error.to_string()))
}

/// Validates an immutable map revision identity using the Domain path-safe selector invariant.
pub(super) fn validate_artifact_revision_identity(
    value: &str,
    field: &str,
) -> Result<(), CatalogError> {
    domain::MapRevisionId::new(value.to_string())
        .map(|_| ())
        .map_err(|error| validation(field, error.to_string()))
}

/// Validates a nonblank opaque artifact metadata value without imposing an identity grammar.
pub(super) fn validate_artifact_text(value: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value,
        field,
        "must be nonblank and have no surrounding whitespace",
    )
}

/// Validates a deployment-owned relative artifact path.
pub(super) fn validate_relative_artifact_path(
    path: &Path,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        !path.as_os_str().is_empty() && !path.is_absolute(),
        field,
        "must be a nonempty relative file path",
    )?;
    require(
        !path.components().any(|component| {
            matches!(
                component,
                std::path::Component::CurDir
                    | std::path::Component::ParentDir
                    | std::path::Component::RootDir
                    | std::path::Component::Prefix(_)
            )
        }),
        field,
        "must not contain current-directory or parent traversal segments",
    )
}

/// Validates a plain or `sha256:`-prefixed lowercase digest.
pub(super) fn validate_artifact_digest(value: &str, field: &str) -> Result<(), CatalogError> {
    let digest = value.strip_prefix("sha256:").unwrap_or(value);
    require(
        digest.len() == 64
            && digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
        field,
        "must be a lowercase SHA-256 digest",
    )
}
