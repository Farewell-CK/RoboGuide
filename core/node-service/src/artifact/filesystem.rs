//! Artifact manifest validation and symlink-safe filesystem primitives.

use super::*;

/// Validates the static identity and metadata required to build an output manifest.
pub(super) fn validate_output_binding(
    binding: &ArtifactOutputBindingConfig,
) -> Result<(), ArtifactError> {
    validate_binding_identity(&binding.id, "output binding id")?;
    validate_binding_identity(&binding.map_id, "output map_id")?;
    validate_binding_identity(&binding.revision_id, "output revision_id")?;
    for (value, field) in [
        (&binding.media_type, "output media type"),
        (&binding.format_name, "output format name"),
        (&binding.format_version, "output format version"),
        (&binding.root_frame, "output root frame"),
        (
            &binding.coordinate_convention,
            "output coordinate convention",
        ),
        (&binding.spatial_anchor_id, "output spatial anchor"),
    ] {
        if value.trim().is_empty() {
            return Err(ArtifactError::Configuration(format!(
                "{field} must be nonblank"
            )));
        }
    }
    Ok(())
}

/// Builds the typed immutable manifest from exact bytes and producer provenance.
pub(super) fn build_output_manifest(
    binding: &ArtifactOutputBindingConfig,
    content_digest: &str,
    byte_size: u64,
    provenance: &ArtifactProvenance,
) -> Result<MapArtifactManifest, ArtifactError> {
    validate_output_binding(binding)?;
    let selector = MapRevisionSelector::new(
        MapId::new(binding.map_id.clone())?,
        MapRevisionId::new(binding.revision_id.clone())?,
    );
    let artifact = MapArtifactRef::new(
        selector,
        ContentDigest::new(normalize_digest(content_digest)?)?,
        byte_size,
    );
    MapArtifactManifest::new_with_format(
        artifact,
        binding.media_type.clone(),
        binding.format_name.clone(),
        binding.format_version.clone(),
        provenance.producer_node_id.clone(),
        provenance.producer_local_system_id.clone(),
        provenance.source_mission_id.clone(),
        provenance.source_execution_id.clone(),
        provenance.source_task_ref.clone(),
        binding.root_frame.clone(),
        binding.coordinate_convention.clone(),
        SpatialAnchorId::new(binding.spatial_anchor_id.clone())?,
        binding.resolution_meters,
        provenance.created_at,
        provenance.parent_revision_id.clone(),
    )
    .map_err(ArtifactError::Domain)
}

/// Confirms a prepared manifest still matches every immutable static output field.
pub(super) fn validate_manifest_binding(
    manifest: &MapArtifactManifest,
    binding: &ArtifactOutputBindingConfig,
) -> Result<(), ArtifactError> {
    let matches = manifest.selector().map_id().as_str() == binding.map_id
        && manifest.selector().revision_id().as_str() == binding.revision_id
        && manifest.media_type() == binding.media_type
        && manifest.format_name() == binding.format_name
        && manifest.format_version() == binding.format_version
        && manifest.root_frame() == binding.root_frame
        && manifest.coordinate_convention() == binding.coordinate_convention
        && manifest.anchor_id().as_str() == binding.spatial_anchor_id
        && manifest.resolution_meters() == binding.resolution_meters;
    if matches {
        Ok(())
    } else {
        Err(ArtifactError::ManifestMismatch(
            "prepared manifest metadata differs from output binding".to_string(),
        ))
    }
}

/// Computes a bounded SHA-256 digest from one already safely opened regular file.
///
/// The file is rewound before reading. Callers that need its bytes again must rewind it after
/// this function returns; keeping the same handle prevents a path replacement from changing the
/// inode that a later operation reads.
pub(super) async fn digest_open_file(
    file: &mut tokio::fs::File,
    chunk_size_bytes: usize,
    max_artifact_bytes: u64,
) -> Result<(String, u64), ArtifactError> {
    if chunk_size_bytes == 0 {
        return Err(ArtifactError::Configuration(
            "chunk_size_bytes must be non-zero".to_string(),
        ));
    }
    file.seek(SeekFrom::Start(0)).await?;
    let mut buffer = vec![0_u8; chunk_size_bytes];
    let mut hasher = Sha256::new();
    let mut size = 0_u64;
    loop {
        let length = file.read(&mut buffer).await?;
        if length == 0 {
            break;
        }
        size = size.checked_add(length as u64).ok_or_else(|| {
            ArtifactError::Configuration("source file size overflows u64".to_string())
        })?;
        if size > max_artifact_bytes {
            return Err(ArtifactError::Configuration(
                "source file exceeds configured artifact limit".to_string(),
            ));
        }
        hasher.update(&buffer[..length]);
    }
    Ok((format!("sha256:{:x}", hasher.finalize()), size))
}

/// Copies a mutable producer output into a private snapshot while hashing the exact bytes copied.
///
/// Hashing and copying happen in one read loop, so a source mutation cannot create a manifest for
/// bytes different from the immutable snapshot. The caller later publishes that snapshot with an
/// atomic non-overwriting hard link.
pub(super) async fn snapshot_open_file(
    mut source_file: tokio::fs::File,
    prepared_root: &Path,
    chunk_size_bytes: usize,
    max_artifact_bytes: u64,
) -> Result<(String, u64, PathBuf), ArtifactError> {
    if chunk_size_bytes == 0 {
        return Err(ArtifactError::Configuration(
            "chunk_size_bytes must be non-zero".to_string(),
        ));
    }
    ensure_directory_tree(prepared_root)?;
    let temporary = temporary_path(&prepared_root.join("snapshot"));
    let result = async {
        let mut snapshot = tokio::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .await?;
        let mut buffer = vec![0_u8; chunk_size_bytes];
        let mut hasher = Sha256::new();
        let mut size = 0_u64;
        loop {
            let length = source_file.read(&mut buffer).await?;
            if length == 0 {
                break;
            }
            size = size.checked_add(length as u64).ok_or_else(|| {
                ArtifactError::Configuration("source file size overflows u64".to_string())
            })?;
            if size > max_artifact_bytes {
                return Err(ArtifactError::Configuration(
                    "source file exceeds configured artifact limit".to_string(),
                ));
            }
            snapshot.write_all(&buffer[..length]).await?;
            hasher.update(&buffer[..length]);
        }
        snapshot.flush().await?;
        snapshot.sync_all().await?;
        drop(snapshot);
        Ok::<_, ArtifactError>((
            format!("sha256:{:x}", hasher.finalize()),
            size,
            temporary.clone(),
        ))
    }
    .await;
    if result.is_err() {
        let _ = remove_temporary_file(&temporary).await;
    }
    result
}

/// Verifies a bounded cached file before reusing it for a binding.
pub(super) async fn verified_file(
    path: &Path,
    expected_digest: &str,
    expected_size: u64,
    max_artifact_bytes: u64,
) -> Result<bool, ArtifactError> {
    let mut file = match open_regular_file(path).await {
        Ok(file) => file,
        Err(ArtifactError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(false);
        }
        Err(error) => return Err(error),
    };
    let metadata = file.metadata().await?;
    if metadata.len() != expected_size {
        return Ok(false);
    }
    if expected_size > max_artifact_bytes {
        return Err(ArtifactError::Configuration(format!(
            "cached artifact size {expected_size} exceeds configured limit {max_artifact_bytes}"
        )));
    }
    let chunk_size = usize::try_from(expected_size.clamp(1, 1024 * 1024)).unwrap_or(1024 * 1024);
    let (actual, actual_size) = digest_open_file(&mut file, chunk_size, max_artifact_bytes).await?;
    Ok(actual == expected_digest && actual_size == expected_size)
}

/// Copies and verifies exact input bytes before atomically exposing a read-only target.
pub(super) async fn copy_verified_atomic(
    source: &Path,
    destination: &Path,
    expected_digest: &str,
    expected_size: u64,
    max_artifact_bytes: u64,
) -> Result<(), ArtifactError> {
    let mut source_file = open_regular_file(source).await?;
    if let Some(parent) = destination.parent() {
        ensure_directory_tree(parent)?;
    }
    reject_symlink(destination)?;
    let temporary = temporary_path(destination);
    let result = async {
        let mut temporary_file = tokio::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .await?;
        tokio::io::copy(&mut source_file, &mut temporary_file).await?;
        temporary_file.flush().await?;
        temporary_file.sync_all().await?;
        Ok::<(), ArtifactError>(())
    }
    .await;
    if let Err(error) = result {
        let _ = remove_temporary_file(&temporary).await;
        return Err(error);
    }
    match verified_file(
        &temporary,
        expected_digest,
        expected_size,
        max_artifact_bytes,
    )
    .await
    {
        Ok(true) => {}
        Ok(false) => {
            remove_temporary_file(&temporary).await?;
            return Err(ArtifactError::ManifestMismatch(
                "staged input bytes differ from the published manifest".to_string(),
            ));
        }
        Err(error) => {
            let _ = remove_temporary_file(&temporary).await;
            return Err(error);
        }
    }
    seal_local_artifact(&temporary).await?;
    if let Err(error) = durable_rename(&temporary, destination).await {
        let _ = remove_temporary_file(&temporary).await;
        return Err(error);
    }
    Ok(())
}

/// Removes write permission from one node-owned verified artifact copy.
pub(super) async fn seal_local_artifact(path: &Path) -> Result<(), ArtifactError> {
    let file = open_regular_file(path).await?;
    let mut permissions = file.metadata().await?.permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions).await?;
    file.sync_all().await?;
    Ok(())
}

/// Removes a temporary artifact path while treating an already absent path as success.
pub(super) async fn remove_temporary_file(path: &Path) -> Result<(), ArtifactError> {
    match tokio::fs::remove_file(path).await {
        Ok(()) => {
            sync_parent_directory(path)?;
            Ok(())
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

/// Renames a synced temporary file and durably commits every affected directory entry.
pub(super) async fn durable_rename(source: &Path, destination: &Path) -> Result<(), ArtifactError> {
    reject_symlink(destination)?;
    tokio::fs::rename(source, destination).await?;
    sync_parent_directory(destination)?;
    if source.parent() != destination.parent() {
        sync_parent_directory(source)?;
    }
    Ok(())
}

/// Creates a non-overwriting hard link and durably commits the destination directory entry.
pub(super) async fn durable_hard_link(
    source: &Path,
    destination: &Path,
) -> Result<(), std::io::Error> {
    tokio::fs::hard_link(source, destination).await?;
    sync_parent_directory_io(destination)?;
    Ok(())
}

/// Synchronizes the parent directory containing one changed artifact entry.
pub(super) fn sync_parent_directory(path: &Path) -> Result<(), ArtifactError> {
    sync_parent_directory_io(path).map_err(ArtifactError::Io)
}

/// Performs the directory synchronization used by artifact operations returning raw I/O errors.
pub(super) fn sync_parent_directory_io(path: &Path) -> Result<(), std::io::Error> {
    let parent = path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            format!("artifact path {} has no parent directory", path.display()),
        )
    })?;
    std::fs::File::open(parent)?.sync_all()
}

/// Creates every missing directory while rejecting symlink and non-directory components.
///
/// Each new entry is followed by a parent-directory `fsync`, so later durable file publication
/// cannot depend on an uncommitted directory chain.
pub(super) fn ensure_directory_tree(path: &Path) -> Result<(), ArtifactError> {
    let path = std::path::absolute(path)?;
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        match std::fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&current, &metadata)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                match std::fs::create_dir(&current) {
                    Ok(()) => sync_parent_directory(&current)?,
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                        let metadata = std::fs::symlink_metadata(&current)?;
                        validate_directory_component(&current, &metadata)?;
                    }
                    Err(error) => return Err(error.into()),
                }
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

/// Validates every existing directory component without following symbolic links.
pub(super) fn validate_directory_tree(
    path: &Path,
    allow_missing: bool,
) -> Result<(), ArtifactError> {
    let path = std::path::absolute(path)?;
    let mut current = PathBuf::new();
    let mut missing = false;
    for component in path.components() {
        current.push(component.as_os_str());
        if missing {
            continue;
        }
        match std::fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&current, &metadata)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound && allow_missing => {
                missing = true;
            }
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

/// Rejects one existing directory component when it is a symlink or another file type.
pub(super) fn validate_directory_component(
    path: &Path,
    metadata: &std::fs::Metadata,
) -> Result<(), ArtifactError> {
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(ArtifactError::Configuration(format!(
            "artifact directory component {} must be a directory and cannot be a symlink",
            path.display()
        )));
    }
    Ok(())
}

/// Opens one artifact source without following a symbolic-link leaf on Unix.
///
/// Every platform validates the opened handle's metadata, so special files are rejected after
/// the open. Non-Unix targets retain a pre-open symbolic-link check because they do not expose a
/// portable no-follow open flag.
pub(super) async fn open_regular_file(path: &Path) -> Result<tokio::fs::File, ArtifactError> {
    #[cfg(not(unix))]
    require_regular_file(path)?;
    let mut options = tokio::fs::OpenOptions::new();
    options.read(true);
    add_no_follow_flag(&mut options);
    let file = options
        .open(path)
        .await
        .map_err(|error| artifact_open_error(path, error))?;
    if !file.metadata().await?.is_file() {
        return Err(ArtifactError::Configuration(format!(
            "artifact path {} must resolve to a regular file",
            path.display()
        )));
    }
    Ok(file)
}

/// Adds Unix leaf no-follow behavior and prevents a replaced special file from blocking open.
pub(super) fn add_no_follow_flag(options: &mut tokio::fs::OpenOptions) {
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    #[cfg(not(unix))]
    let _ = options;
}

/// Converts a no-follow rejection into a stable configuration error and preserves other I/O.
pub(super) fn artifact_open_error(path: &Path, error: std::io::Error) -> ArtifactError {
    #[cfg(unix)]
    if error.raw_os_error() == Some(libc::ELOOP) {
        return ArtifactError::Configuration(format!(
            "artifact path {} cannot be a symlink",
            path.display()
        ));
    }
    ArtifactError::Io(error)
}

/// Rejects a symlink or non-regular file before artifact bytes are opened on non-Unix targets.
#[cfg(not(unix))]
pub(super) fn require_regular_file(path: &Path) -> Result<(), ArtifactError> {
    let metadata = std::fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ArtifactError::Configuration(format!(
            "artifact path {} must be a regular file and cannot be a symlink",
            path.display()
        )));
    }
    Ok(())
}

/// Rejects an existing symbolic-link leaf while allowing absent or regular destinations.
pub(super) fn reject_symlink(path: &Path) -> Result<(), ArtifactError> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(ArtifactError::Configuration(
            format!("artifact path {} cannot be a symlink", path.display()),
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
    }
}

/// Creates a unique temporary sibling path without exposing caller-controlled names.
pub(super) fn temporary_path(path: &Path) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("artifact");
    path.with_file_name(format!(".{name}.{stamp}.partial"))
}

/// Creates a path-safe upload identity for one output attempt.
pub(super) fn new_upload_id() -> String {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("node-upload-{stamp}")
}

/// Validates one URL path segment against traversal and empty values.
pub(super) fn validate_segment(value: &str, field: &str) -> Result<(), ArtifactError> {
    if value.trim().is_empty() || value != value.trim() || value.contains(['/', '\\']) {
        return Err(ArtifactError::Configuration(format!(
            "{field} must be a nonblank path-safe identity"
        )));
    }
    Ok(())
}

/// Validates a static binding identity.
pub(super) fn validate_binding_identity(value: &str, field: &str) -> Result<(), ArtifactError> {
    validate_segment(value, field)
}

/// Resolves and validates a deployment-owned cache root.
pub(super) fn resolve_deployment_path(
    directory: &Path,
    path: &Path,
) -> Result<PathBuf, ArtifactError> {
    if path.as_os_str().is_empty() {
        return Err(ArtifactError::Configuration(
            "cache_directory must not be empty".to_string(),
        ));
    }
    let resolved = if path.is_absolute() {
        path.to_path_buf()
    } else {
        directory.join(path)
    };
    std::path::absolute(resolved).map_err(ArtifactError::Io)
}

/// Normalizes either a plain or sha256-prefixed digest to the canonical form.
pub(super) fn normalize_digest(value: &str) -> Result<String, ArtifactError> {
    let digest = value.strip_prefix("sha256:").unwrap_or(value);
    if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(ArtifactError::Configuration(
            "content digest must be 64 hexadecimal SHA-256 characters".to_string(),
        ));
    }
    let lowercase = digest.to_ascii_lowercase();
    if digest != lowercase {
        return Err(ArtifactError::Configuration(
            "content digest must use lowercase hexadecimal".to_string(),
        ));
    }
    Ok(format!("sha256:{lowercase}"))
}

/// Confirms an existing immutable manifest came from the same durable execution attempt.
///
/// Creation time is deliberately excluded: a process may restart after publication succeeded
/// but before the terminal journal fact was written.  Stable execution identity plus every other
/// immutable field proves that retry without weakening conflicts between distinct executions.
pub(super) fn is_same_publication_attempt(
    existing: &MapArtifactManifest,
    candidate: &MapArtifactManifest,
) -> bool {
    candidate.source_execution_id().is_some()
        && existing.artifact() == candidate.artifact()
        && existing.media_type() == candidate.media_type()
        && existing.format_name() == candidate.format_name()
        && existing.format_version() == candidate.format_version()
        && existing.producer_node_id() == candidate.producer_node_id()
        && existing.producer_local_system_id() == candidate.producer_local_system_id()
        && existing.source_mission_id() == candidate.source_mission_id()
        && existing.source_execution_id() == candidate.source_execution_id()
        && existing.source_task_ref() == candidate.source_task_ref()
        && existing.root_frame() == candidate.root_frame()
        && existing.coordinate_convention() == candidate.coordinate_convention()
        && existing.anchor_id() == candidate.anchor_id()
        && existing.resolution_meters() == candidate.resolution_meters()
        && existing.parent_revision_id() == candidate.parent_revision_id()
}

/// Parses one top-level catalog status value without consuming the manifest body.
pub(super) fn parse_revision_status_value(
    value: &Value,
) -> Result<MapRevisionStatus, ArtifactError> {
    let value = value.as_str().ok_or_else(|| {
        ArtifactError::ManifestMismatch("catalog revision status must be a string".to_string())
    })?;
    match value.to_ascii_lowercase().as_str() {
        "declared" => Ok(MapRevisionStatus::Declared),
        "published" => Ok(MapRevisionStatus::Published),
        _ => Err(ArtifactError::ManifestMismatch(format!(
            "unknown catalog revision status {value:?}"
        ))),
    }
}

/// Converts a non-success response into a stable error without consuming its body.
pub(super) fn ensure_success(
    response: &reqwest::Response,
    endpoint: &Url,
) -> Result<(), ArtifactError> {
    if response.status().is_success() {
        Ok(())
    } else {
        Err(ArtifactError::Status {
            status: response.status(),
            endpoint: endpoint.to_string(),
        })
    }
}

impl Display for StagedArtifact {
    /// Formats a concise staged artifact identity for logs and evidence.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{}/{} ({}, {} bytes)",
            self.map_id, self.revision_id, self.content_digest, self.byte_size
        )
    }
}
