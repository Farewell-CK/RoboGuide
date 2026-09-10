//! Node-local artifact staging, verification, and publication facade.

use super::*;

/// High-level node-side staging facade for static input/output bindings.
#[derive(Clone)]
pub struct ArtifactStager {
    /// HTTP artifact client.
    pub(super) client: ArtifactClient,
    /// Deployment-owned cache root.
    cache_directory: PathBuf,
}

impl ArtifactStager {
    /// Builds a stager from one startup configuration relative to its file directory.
    pub fn from_config(
        config: &ArtifactServiceConfig,
        config_directory: &Path,
    ) -> Result<Self, ArtifactError> {
        let cache_directory = resolve_deployment_path(config_directory, &config.cache_directory)?;
        validate_directory_tree(&cache_directory, true)?;
        let client = ArtifactClient::new(
            &config.endpoint,
            config.chunk_size_bytes,
            config.max_artifact_bytes,
            config.connect_timeout_ms,
            config.read_timeout_ms,
        )?;
        Ok(Self {
            client,
            cache_directory,
        })
    }

    /// Downloads and verifies a generic Memory artifact into the node-owned cache.
    pub async fn stage_memory_input(
        &self,
        manifest: &MemoryArtifactManifest,
        destination: &Path,
    ) -> Result<(), ArtifactError> {
        let artifact = manifest.artifact().ok_or_else(|| {
            ArtifactError::Configuration("metadata-only Memory cannot be staged".to_string())
        })?;
        self.client
            .download_digest(
                artifact.content_digest().as_str(),
                destination,
                artifact.byte_size(),
            )
            .await
    }

    /// Returns a provider-independent cache path for one immutable Memory selector.
    pub fn memory_cache_path(&self, selector: &MemorySelector) -> PathBuf {
        self.cache_directory
            .join("memory")
            .join(selector.memory_id().as_str())
            .join(format!("{}.blob", selector.revision_id().as_str()))
    }

    /// Publishes generic Memory metadata using the active Node Protocol session identity.
    pub async fn publish_memory(
        &self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        session_id: &str,
    ) -> Result<(), ArtifactError> {
        self.client
            .publish_memory_manifest(manifest, node_id, session_id)
            .await
    }

    /// Uploads provider-produced opaque bytes and verifies the declared Memory reference.
    pub async fn upload_memory_output(
        &self,
        manifest: &MemoryArtifactManifest,
        source: &Path,
    ) -> Result<(), ArtifactError> {
        let artifact = manifest.artifact().ok_or_else(|| {
            ArtifactError::Configuration(
                "exchangeable Memory lacks an artifact reference".to_string(),
            )
        })?;
        self.client.upload_memory_file(source, artifact).await
    }

    /// Records generic Memory replica evidence using the active Node Protocol session identity.
    pub async fn record_memory_replica(
        &self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        session_id: &str,
        consumer_provider_id: &str,
        status: &str,
        reason: Option<&str>,
    ) -> Result<(), ArtifactError> {
        self.client
            .record_memory_replica(
                manifest,
                node_id,
                session_id,
                consumer_provider_id,
                status,
                reason,
            )
            .await
    }

    /// Builds a stager from a startup-validated compiled artifact service.
    pub fn from_compiled(config: &CompiledArtifactService) -> Result<Self, ArtifactError> {
        let cache_directory = std::path::absolute(config.cache_directory())?;
        validate_directory_tree(&cache_directory, true)?;
        let client = ArtifactClient::new(
            config.endpoint(),
            config.chunk_size_bytes(),
            config.max_artifact_bytes(),
            config.connect_timeout_ms(),
            config.read_timeout_ms(),
        )?;
        Ok(Self {
            client,
            cache_directory,
        })
    }

    /// Returns the deployment-owned cache root.
    pub fn cache_directory(&self) -> &Path {
        &self.cache_directory
    }

    /// Resolves one configured binding path below the deployment-owned cache root.
    pub fn resolve_path(&self, path: &Path) -> Result<PathBuf, ArtifactError> {
        self.target_path(path)
    }

    /// Creates the configured output parent before a producing workflow starts.
    ///
    /// The path itself remains controlled by deployment configuration; this method never creates
    /// or truncates the output file and therefore cannot destroy a prior prepared artifact.
    pub async fn prepare_output_path(
        &self,
        binding: &ArtifactOutputBindingConfig,
    ) -> Result<PathBuf, ArtifactError> {
        let path = self.target_path(&binding.source_path)?;
        if let Some(parent) = path.parent() {
            ensure_directory_tree(parent)?;
        }
        Ok(path)
    }

    /// Freezes a completed producer output into a content-addressed read-only local copy.
    ///
    /// The returned manifest is built exactly once from producer execution and Task provenance.
    /// Later publication must use [`Self::publish_prepared`] and never reread the mutable source.
    pub async fn freeze_output(
        &self,
        binding: &ArtifactOutputBindingConfig,
        provenance: &ArtifactProvenance,
    ) -> Result<PreparedArtifact, ArtifactError> {
        validate_output_binding(binding)?;
        if provenance.source_execution_id.is_none() || provenance.source_task_ref.is_none() {
            return Err(ArtifactError::Configuration(
                "prepared output requires build execution and Task provenance".to_string(),
            ));
        }
        let source = self.target_path(&binding.source_path)?;
        let source_file = open_regular_file(&source).await?;
        let prepared_root = self.cache_directory.join("prepared");
        let (digest, byte_size, snapshot_path) = snapshot_open_file(
            source_file,
            &prepared_root,
            self.client.chunk_size_bytes,
            self.client.max_artifact_bytes,
        )
        .await?;
        let raw_digest = digest
            .strip_prefix("sha256:")
            .expect("snapshot_open_file always returns a canonical SHA-256 digest");
        let frozen_path = prepared_root
            .join("sha256")
            .join(&raw_digest[..2])
            .join(raw_digest);
        if let Some(parent) = frozen_path.parent() {
            ensure_directory_tree(parent)?;
        }
        let selected_path = if verified_file(
            &frozen_path,
            &digest,
            byte_size,
            self.client.max_artifact_bytes,
        )
        .await?
        {
            remove_temporary_file(&snapshot_path).await?;
            frozen_path
        } else {
            match durable_hard_link(&snapshot_path, &frozen_path).await {
                Ok(()) => {
                    remove_temporary_file(&snapshot_path).await?;
                    frozen_path
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    if verified_file(
                        &frozen_path,
                        &digest,
                        byte_size,
                        self.client.max_artifact_bytes,
                    )
                    .await?
                    {
                        remove_temporary_file(&snapshot_path).await?;
                        frozen_path
                    } else {
                        remove_temporary_file(&snapshot_path).await?;
                        return Err(ArtifactError::ManifestMismatch(
                            "prepared digest path conflicts with different bytes".to_string(),
                        ));
                    }
                }
                Err(error) => {
                    remove_temporary_file(&snapshot_path).await?;
                    return Err(error.into());
                }
            }
        };
        if !verified_file(
            &selected_path,
            &digest,
            byte_size,
            self.client.max_artifact_bytes,
        )
        .await?
        {
            return Err(ArtifactError::ManifestMismatch(
                "prepared artifact snapshot failed post-publication verification".to_string(),
            ));
        }
        seal_local_artifact(&selected_path).await?;
        let manifest = build_output_manifest(binding, &digest, byte_size, provenance)?;
        Ok(PreparedArtifact {
            binding_id: binding.id.clone(),
            path: selected_path,
            manifest,
        })
    }

    /// Publishes an exact frozen output and its build-time manifest idempotently.
    ///
    /// The frozen file is verified before every remote lookup, including an idempotent retry that
    /// finds an identical Published manifest. Only then may an existing publication avoid upload.
    pub async fn publish_prepared(
        &self,
        binding: &ArtifactOutputBindingConfig,
        prepared: &PreparedArtifact,
    ) -> Result<(), ArtifactError> {
        self.verify_prepared(binding, prepared).await?;
        if self
            .client
            .publication_already_exists(&prepared.manifest)
            .await?
        {
            return Ok(());
        }
        let expected_digest =
            normalize_digest(prepared.manifest.artifact().content_digest().as_str())?;
        let expected_size = prepared.manifest.artifact().byte_size();
        let output = self.client.upload_file(binding, &prepared.path).await?;
        if output.content_digest != expected_digest {
            return Err(ArtifactError::DigestMismatch {
                expected: expected_digest,
                actual: output.content_digest,
            });
        }
        if output.byte_size != expected_size {
            return Err(ArtifactError::SizeMismatch {
                expected: expected_size,
                actual: output.byte_size,
            });
        }
        self.publish_manifest(&prepared.manifest).await
    }

    /// Verifies a durable prepared record against static metadata and its immutable local bytes.
    pub async fn verify_prepared(
        &self,
        binding: &ArtifactOutputBindingConfig,
        prepared: &PreparedArtifact,
    ) -> Result<(), ArtifactError> {
        self.validate_prepared_metadata(binding, prepared)?;
        let expected_digest =
            normalize_digest(prepared.manifest.artifact().content_digest().as_str())?;
        if verified_file(
            &prepared.path,
            &expected_digest,
            prepared.manifest.artifact().byte_size(),
            self.client.max_artifact_bytes,
        )
        .await?
        {
            Ok(())
        } else {
            Err(ArtifactError::ManifestMismatch(
                "prepared artifact copy is unavailable or mutated".to_string(),
            ))
        }
    }

    /// Validates durable prepared metadata before local verification or remote publication.
    pub(super) fn validate_prepared_metadata(
        &self,
        binding: &ArtifactOutputBindingConfig,
        prepared: &PreparedArtifact,
    ) -> Result<(), ArtifactError> {
        validate_output_binding(binding)?;
        if prepared.binding_id != binding.id {
            return Err(ArtifactError::ManifestMismatch(
                "prepared artifact binding differs from publication binding".to_string(),
            ));
        }
        validate_manifest_binding(&prepared.manifest, binding)?;
        let prepared_root = self.cache_directory.join("prepared");
        if !prepared.path.starts_with(&prepared_root) {
            return Err(ArtifactError::Configuration(
                "prepared artifact path is outside the node-owned prepared directory".to_string(),
            ));
        }
        Ok(())
    }

    /// Stages and verifies one statically configured map input.
    pub async fn stage_input(
        &self,
        binding: &ArtifactInputBindingConfig,
    ) -> Result<StagedArtifact, ArtifactError> {
        validate_binding_identity(&binding.id, "input binding id")?;
        validate_binding_identity(&binding.map_id, "input map_id")?;
        validate_binding_identity(&binding.revision_id, "input revision_id")?;
        let target = self.target_path(&binding.target_path)?;
        let manifest = self
            .client
            .fetch_manifest(&binding.map_id, &binding.revision_id)
            .await?;
        if !manifest.is_published() {
            return Err(ArtifactError::ManifestMismatch(
                "catalog revision is not Published".to_string(),
            ));
        }
        if manifest.manifest.selector().map_id().as_str() != binding.map_id
            || manifest.manifest.selector().revision_id().as_str() != binding.revision_id
        {
            return Err(ArtifactError::ManifestMismatch(
                "map/revision selector differs from binding".to_string(),
            ));
        }
        let digest = manifest.normalized_digest()?;
        let byte_size = manifest.manifest.artifact().byte_size();
        if byte_size > self.client.max_artifact_bytes {
            return Err(ArtifactError::Configuration(format!(
                "manifest artifact size {} exceeds configured limit {}",
                byte_size, self.client.max_artifact_bytes
            )));
        }
        if let Some(expected) = &binding.content_digest
            && normalize_digest(expected)? != digest
        {
            return Err(ArtifactError::ManifestMismatch(
                "configured content digest differs from catalog".to_string(),
            ));
        }
        let blob_path = self.blob_path(&digest)?;
        if !verified_file(
            &blob_path,
            &digest,
            byte_size,
            self.client.max_artifact_bytes,
        )
        .await?
        {
            self.client
                .download_digest(&digest, &blob_path, byte_size)
                .await?;
        }
        if let Some(parent) = target.parent() {
            ensure_directory_tree(parent)?;
        }
        copy_verified_atomic(
            &blob_path,
            &target,
            &digest,
            byte_size,
            self.client.max_artifact_bytes,
        )
        .await?;
        Ok(StagedArtifact {
            binding_id: binding.id.clone(),
            map_id: binding.map_id.clone(),
            revision_id: binding.revision_id.clone(),
            content_digest: digest,
            byte_size,
            path: target,
            manifest: manifest.manifest,
        })
    }

    /// Re-proves that an exact published input remains staged under its static local binding.
    ///
    /// This check is required before replica evidence, including after an explicit crash-recovery
    /// retry. Missing, symlinked, non-regular, size-mismatched, or digest-mismatched bytes fail
    /// closed and no evidence may be emitted by the caller.
    pub async fn verify_staged_input(
        &self,
        binding: &ArtifactInputBindingConfig,
        manifest: &MapArtifactManifest,
    ) -> Result<(), ArtifactError> {
        validate_binding_identity(&binding.id, "input binding id")?;
        validate_binding_identity(&binding.map_id, "input map_id")?;
        validate_binding_identity(&binding.revision_id, "input revision_id")?;
        if manifest.selector().map_id().as_str() != binding.map_id
            || manifest.selector().revision_id().as_str() != binding.revision_id
        {
            return Err(ArtifactError::ManifestMismatch(
                "staged input selector differs from binding".to_string(),
            ));
        }
        let digest = normalize_digest(manifest.artifact().content_digest().as_str())?;
        if let Some(expected) = &binding.content_digest
            && normalize_digest(expected)? != digest
        {
            return Err(ArtifactError::ManifestMismatch(
                "configured content digest differs from staged manifest".to_string(),
            ));
        }
        let target = self.target_path(&binding.target_path)?;
        if verified_file(
            &target,
            &digest,
            manifest.artifact().byte_size(),
            self.client.max_artifact_bytes,
        )
        .await?
        {
            Ok(())
        } else {
            Err(ArtifactError::ManifestMismatch(
                "staged input copy is unavailable or mutated".to_string(),
            ))
        }
    }

    /// Reports a node-local replica transition for an already validated manifest.
    pub async fn record_replica(
        &self,
        manifest: &MapArtifactManifest,
        node_id: &NodeId,
        mission_id: &MissionId,
        status: ReplicaEvidenceStatus,
    ) -> Result<(), ArtifactError> {
        self.client
            .record_replica(manifest, node_id, mission_id, status)
            .await
    }

    /// Reports one complete strong localization result for an already validated artifact.
    pub async fn record_localization_evidence(
        &self,
        evidence: &LocalizationVerificationEvidence,
        session_id: &str,
    ) -> Result<(), ArtifactError> {
        self.client
            .record_localization_evidence(evidence, session_id)
            .await
    }

    /// Fetches and validates the exact published manifest selected by one input binding.
    pub async fn published_input_manifest(
        &self,
        binding: &ArtifactInputBindingConfig,
    ) -> Result<MapArtifactManifest, ArtifactError> {
        validate_binding_identity(&binding.id, "input binding id")?;
        validate_binding_identity(&binding.map_id, "input map_id")?;
        validate_binding_identity(&binding.revision_id, "input revision_id")?;
        let envelope = self
            .client
            .fetch_manifest(&binding.map_id, &binding.revision_id)
            .await?;
        if !envelope.is_published() {
            return Err(ArtifactError::ManifestMismatch(
                "catalog revision is not Published".to_string(),
            ));
        }
        if envelope.manifest.selector().map_id().as_str() != binding.map_id
            || envelope.manifest.selector().revision_id().as_str() != binding.revision_id
        {
            return Err(ArtifactError::ManifestMismatch(
                "map/revision selector differs from binding".to_string(),
            ));
        }
        if envelope.manifest.artifact().byte_size() > self.client.max_artifact_bytes {
            return Err(ArtifactError::Configuration(format!(
                "manifest artifact size {} exceeds configured limit {}",
                envelope.manifest.artifact().byte_size(),
                self.client.max_artifact_bytes
            )));
        }
        let digest = envelope.normalized_digest()?;
        if let Some(expected) = &binding.content_digest
            && normalize_digest(expected)? != digest
        {
            return Err(ArtifactError::ManifestMismatch(
                "configured content digest differs from catalog".to_string(),
            ));
        }
        Ok(envelope.manifest)
    }

    /// Uploads one fixed output path after streaming and hashing its bytes.
    ///
    /// The returned bytes are finalized in CAS but are not assigned provenance or published in
    /// the logical catalog.  Use [`Self::publish_output_for_execution`] when the execution
    /// identity is available.
    pub async fn publish_output(
        &self,
        binding: &ArtifactOutputBindingConfig,
    ) -> Result<ArtifactOutput, ArtifactError> {
        validate_binding_identity(&binding.id, "output binding id")?;
        validate_binding_identity(&binding.map_id, "output map_id")?;
        validate_binding_identity(&binding.revision_id, "output revision_id")?;
        let source = self.target_path(&binding.source_path)?;
        self.client.upload_file(binding, &source).await
    }

    /// Publishes one typed manifest through the artifact catalog.
    pub async fn publish_manifest(
        &self,
        manifest: &MapArtifactManifest,
    ) -> Result<(), ArtifactError> {
        self.client.publish_manifest(manifest).await
    }

    /// Uploads one output and publishes its typed manifest with execution provenance.
    ///
    /// The upload and catalog transition remain two explicit operations.  If publication fails,
    /// the immutable bytes remain available for retry and no partially published manifest is
    /// reported to the caller.
    pub async fn publish_output_with_provenance(
        &self,
        binding: &ArtifactOutputBindingConfig,
        provenance: &ArtifactProvenance,
    ) -> Result<(ArtifactOutput, MapArtifactManifest), ArtifactError> {
        let output = self.publish_output(binding).await?;
        let manifest = build_output_manifest(
            binding,
            &output.content_digest,
            output.byte_size,
            provenance,
        )?;
        self.publish_manifest(&manifest).await?;
        Ok((output, manifest))
    }

    /// Uploads and publishes one fixed output with explicit Mission/Node provenance.
    #[allow(clippy::too_many_arguments)]
    pub async fn publish_output_for_execution(
        &self,
        binding: &ArtifactOutputBindingConfig,
        producer_node_id: &str,
        source_mission_id: &str,
        source_execution_id: Option<&str>,
        source_task_id: Option<&str>,
        created_at_ms: u64,
    ) -> Result<ArtifactOutput, ArtifactError> {
        let mission = MissionId::new(source_mission_id.to_string())?;
        let task_ref = source_task_id
            .filter(|value| !value.trim().is_empty())
            .map(|task_id| {
                TaskId::new(task_id.to_string()).map(|task| TaskRef::new(mission.clone(), task))
            })
            .transpose()?;
        let provenance = ArtifactProvenance {
            producer_node_id: NodeId::new(producer_node_id.to_string())?,
            producer_local_system_id: None,
            source_mission_id: mission,
            source_execution_id: source_execution_id.map(str::to_string),
            source_task_ref: task_ref,
            created_at: TimestampMs::new(created_at_ms),
            parent_revision_id: None,
        };
        let (output, _manifest) = self
            .publish_output_with_provenance(binding, &provenance)
            .await?;
        Ok(output)
    }

    /// Resolves one deployment-owned relative path below the cache root.
    pub(super) fn target_path(&self, path: &Path) -> Result<PathBuf, ArtifactError> {
        if path.as_os_str().is_empty()
            || path.is_absolute()
            || path.components().any(|component| {
                matches!(
                    component,
                    Component::CurDir
                        | Component::ParentDir
                        | Component::RootDir
                        | Component::Prefix(_)
                )
            })
        {
            return Err(ArtifactError::Configuration(format!(
                "artifact path {} must be relative and cannot escape cache_directory",
                path.display()
            )));
        }
        validate_directory_tree(&self.cache_directory, true)?;
        let target = self.cache_directory.join(path);
        if let Some(parent) = target.parent() {
            validate_directory_tree(parent, true)?;
        }
        reject_symlink(&target)?;
        Ok(target)
    }

    /// Returns the content-addressed cache path with the public `sha256:` prefix removed.
    pub(super) fn blob_path(&self, digest: &str) -> Result<PathBuf, ArtifactError> {
        let digest = normalize_digest(digest)?;
        let raw = digest.strip_prefix("sha256:").unwrap_or(&digest);
        Ok(self
            .cache_directory
            .join("blobs")
            .join("sha256")
            .join(&raw[..2])
            .join(raw))
    }
}
