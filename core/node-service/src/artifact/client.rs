//! HTTP Artifact data-plane client operations.

use super::*;

/// Maximum number of progress-aware retries for one upload chunk or finalization request.
const MAX_UPLOAD_RETRIES: u8 = 8;
/// Upper bound for one resumable upload request, independent of local digest chunking.
const MAX_UPLOAD_CHUNK_BYTES: usize = 16 * 1024 * 1024;

impl ArtifactClient {
    /// Creates a client after validating an absolute HTTP(S) endpoint and transfer limits.
    pub fn new(
        endpoint: impl AsRef<str>,
        chunk_size_bytes: usize,
        max_artifact_bytes: u64,
        connect_timeout_ms: u64,
        read_timeout_ms: u64,
    ) -> Result<Self, ArtifactError> {
        let endpoint = Url::parse(endpoint.as_ref())
            .map_err(|error| ArtifactError::Configuration(error.to_string()))?;
        if !matches!(endpoint.scheme(), "http" | "https") || endpoint.host().is_none() {
            return Err(ArtifactError::Configuration(
                "endpoint must be an absolute http(s) URL".to_string(),
            ));
        }
        if !endpoint.username().is_empty() || endpoint.password().is_some() {
            return Err(ArtifactError::Configuration(
                "endpoint must not contain inline credentials".to_string(),
            ));
        }
        if chunk_size_bytes == 0
            || max_artifact_bytes == 0
            || connect_timeout_ms == 0
            || read_timeout_ms == 0
        {
            return Err(ArtifactError::Configuration(
                "artifact size, chunk size, and timeout limits must be non-zero".to_string(),
            ));
        }
        let mut builder = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_millis(connect_timeout_ms))
            .read_timeout(Duration::from_millis(read_timeout_ms));
        let mut upload_prefix = None;
        if artifact_endpoint_requires_mtls(&endpoint) {
            if endpoint.scheme() != "https" {
                return Err(ArtifactError::Configuration(
                    "remote Artifact endpoint must use https for mTLS".to_string(),
                ));
            }
            let (certificate, key, ca) = artifact_client_tls_material()?;
            let certificate_pem = std::fs::read(&certificate)?;
            let leaf = rustls_pemfile::certs(&mut certificate_pem.as_slice())
                .next()
                .transpose()?
                .ok_or_else(|| {
                    ArtifactError::Configuration(
                        "Artifact client leaf certificate is missing".into(),
                    )
                })?;
            upload_prefix = Some(format!("cert-{:x}-", sha2::Sha256::digest(leaf.as_ref())));
            let identity = reqwest::Identity::from_pem(
                format!(
                    "{}\n{}\n",
                    std::fs::read_to_string(certificate)?,
                    std::fs::read_to_string(key)?
                )
                .as_bytes(),
            )
            .map_err(|error| ArtifactError::Configuration(error.to_string()))?;
            let root = reqwest::Certificate::from_pem(&std::fs::read(ca)?)
                .map_err(|error| ArtifactError::Configuration(error.to_string()))?;
            builder = builder.identity(identity).add_root_certificate(root);
        }
        Ok(Self {
            acquisition_guard: None,
            publication: None,
            memory_import_id: None,
            client: builder.build()?,
            endpoint,
            chunk_size_bytes,
            max_artifact_bytes,
            upload_prefix,
        })
    }

    /// Scopes all acquisition/evidence requests to one immutable original import without granting rights.
    pub(super) fn for_memory_import(&self, id: &str) -> Result<Self, ArtifactError> {
        if id.is_empty() || id.len() > 128 || id.chars().any(char::is_control) {
            return Err(ArtifactError::Configuration(
                "invalid original Memory import identity".into(),
            ));
        }
        let mut client = self.clone();
        client.memory_import_id = Some(id.into());
        Ok(client)
    }

    /// Carries current Node routing context for an exact publishing attempt; headers grant no authority.
    pub(super) fn for_publication(
        &self,
        node: &NodeId,
        session: &str,
        execution: &str,
    ) -> Result<Self, ArtifactError> {
        if [session, execution]
            .into_iter()
            .any(|id| id.is_empty() || id.len() > 1024 || id.chars().any(char::is_control))
        {
            return Err(ArtifactError::Configuration(
                "publication context is invalid".into(),
            ));
        }
        let mut client = self.clone();
        client.publication = Some((node.clone(), session.into(), execution.into()));
        Ok(client)
    }

    /// Carries an opaque original purpose through the existing mTLS client on every related request.
    fn request(&self, method: reqwest::Method, endpoint: Url) -> reqwest::RequestBuilder {
        let mut builder = self.client.request(method, endpoint);
        if let Some((node, session, _)) = &self.publication {
            builder = builder
                .header("X-RoboGuide-Node-Id", node.as_str())
                .header("X-RoboGuide-Session-Id", session);
        }
        if let Some(id) = &self.memory_import_id {
            builder.header("X-RoboGuide-Memory-Import-Id", id)
        } else {
            builder
        }
    }

    /// Fetches one immutable manifest and retains its catalog lifecycle status.
    ///
    /// Missing status is preserved as `None`; malformed status fails instead of being treated as
    /// published.  Eligibility for input staging is enforced separately by
    /// [`ArtifactStager::stage_input`].
    pub async fn fetch_manifest(
        &self,
        map_id: &str,
        revision_id: &str,
    ) -> Result<ArtifactManifestEnvelope, ArtifactError> {
        validate_segment(map_id, "map_id")?;
        validate_segment(revision_id, "revision_id")?;
        let endpoint = self.path(&["v1", "maps", map_id, "revisions", revision_id])?;
        let response = self
            .request(reqwest::Method::GET, endpoint.clone())
            .send()
            .await?;
        ensure_success(&response, &endpoint)?;
        let body = response.json::<Value>().await?;
        // The catalog wraps the typed manifest with its current lifecycle.  Keep that
        // lifecycle in the result; the staging policy is enforced by `stage_input`.
        let status = body
            .get("status")
            .map(parse_revision_status_value)
            .transpose()?;
        let manifest = body.get("manifest").unwrap_or(&body).clone();
        Ok(ArtifactManifestEnvelope {
            manifest: serde_json::from_value(manifest)?,
            status,
        })
    }

    /// Fetches one generic Memory manifest and replica evidence from the shared catalog.
    pub async fn fetch_memory_manifest(
        &self,
        selector: &MemorySelector,
    ) -> Result<(MemoryArtifactManifest, Value), ArtifactError> {
        validate_segment(selector.memory_id().as_str(), "memory_id")?;
        validate_segment(selector.revision_id().as_str(), "revision_id")?;
        let endpoint = self.path(&[
            "v1",
            "memories",
            selector.memory_id().as_str(),
            "revisions",
            selector.revision_id().as_str(),
        ])?;
        let response = self
            .request(reqwest::Method::GET, endpoint.clone())
            .send()
            .await?;
        ensure_success(&response, &endpoint)?;
        let body = response.json::<Value>().await?;
        let manifest = serde_json::from_value(
            body.get("manifest")
                .cloned()
                .unwrap_or_else(|| body.clone()),
        )?;
        Ok((manifest, body))
    }

    /// Publishes generic Memory metadata with the active Node/session identity.
    pub async fn publish_memory_manifest(
        &self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        session_id: &str,
    ) -> Result<(), ArtifactError> {
        let selector = manifest.selector();
        let endpoint = self.path(&[
            "v1",
            "memories",
            selector.memory_id().as_str(),
            "revisions",
            selector.revision_id().as_str(),
        ])?;
        if self.memory_publication_already_exists(manifest).await? {
            return Ok(());
        }
        let response = self
            .request(reqwest::Method::POST, endpoint.clone())
            .header("X-RoboGuide-Node-Id", node_id.as_str())
            .header("X-RoboGuide-Session-Id", session_id)
            .json(manifest)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("memory publication", error))?;
        if response.status() == StatusCode::CONFLICT {
            return if self.memory_publication_already_exists(manifest).await? {
                Ok(())
            } else {
                Err(ArtifactError::Status {
                    status: response.status(),
                    endpoint: endpoint.to_string(),
                })
            };
        }
        ensure_success(&response, &endpoint)
    }

    /// Checks whether a generic catalog selector already contains exactly this manifest.
    pub(super) async fn memory_publication_already_exists(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<bool, ArtifactError> {
        match self.fetch_memory_manifest(manifest.selector()).await {
            Ok((existing, _)) => Ok(existing == *manifest),
            Err(ArtifactError::Status {
                status: StatusCode::NOT_FOUND,
                ..
            }) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Reads exact provider-qualified Imported history without granting current availability.
    pub async fn memory_replica_was_imported(
        &self,
        selector: &MemorySelector,
        node: &NodeId,
        provider: &str,
    ) -> Result<bool, ArtifactError> {
        let (_, body) = self.fetch_memory_manifest(selector).await?;
        let replicas: Vec<domain::MemoryReplicaSnapshot> =
            serde_json::from_value(body.get("replicas").cloned().ok_or_else(|| {
                ArtifactError::Configuration("Memory catalog response lacks replica history".into())
            })?)?;
        Ok(replicas.iter().any(|r| {
            r.selector() == selector
                && r.node_id() == node
                && r.consumer_provider_id() == provider
                && r.status() == domain::MemoryReplicaStatus::Imported
        }))
    }

    /// Records generic Memory staged/imported/rejected evidence through the shared catalog.
    #[allow(clippy::too_many_arguments)]
    pub async fn record_memory_replica(
        &self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        session_id: &str,
        consumer_provider_id: &str,
        status: &str,
        reason: Option<&str>,
        provenance: Option<&domain::MemoryReplicaProvenance>,
    ) -> Result<(), ArtifactError> {
        let selector = manifest.selector();
        let endpoint = self.path(&[
            "v1",
            "memories",
            selector.memory_id().as_str(),
            "revisions",
            selector.revision_id().as_str(),
            "replicas",
        ])?;
        let payload = serde_json::json!({
            "manifest": manifest,
            "node_id": node_id.as_str(),
            "consumer_provider_id": consumer_provider_id,
            "status": status,
            "reason": reason,
            "provenance": provenance,
        });
        let response = self
            .request(reqwest::Method::POST, endpoint.clone())
            .header("X-RoboGuide-Node-Id", node_id.as_str())
            .header("X-RoboGuide-Session-Id", session_id)
            .json(&payload)
            .send()
            .await
            .map_err(|error| {
                ArtifactError::remote_outcome_unknown("memory replica evidence", error)
            })?;
        ensure_success(&response, &endpoint)
    }

    /// Publishes one typed immutable manifest after the artifact bytes are available.
    ///
    /// This is deliberately separate from [`Self::upload_file`]: uploading bytes only places
    /// them in the content store, while this call performs the catalog lifecycle transition.
    pub async fn publish_manifest(
        &self,
        manifest: &MapArtifactManifest,
    ) -> Result<(), ArtifactError> {
        let map_id = manifest.selector().map_id().as_str();
        let revision_id = manifest.selector().revision_id().as_str();
        validate_segment(map_id, "map_id")?;
        validate_segment(revision_id, "revision_id")?;
        if self.publication_already_exists(manifest).await? {
            return Ok(());
        }
        let endpoint = self.path(&["v1", "maps", map_id, "revisions", revision_id])?;
        let payload = if manifest.producer_provider_id().is_some() {
            let (_, _, execution) = self.publication.as_ref().ok_or_else(|| {
                ArtifactError::Configuration(
                    "provider-bound publication requires current Node session and attempt".into(),
                )
            })?;
            serde_json::json!({"schema": "roboguide.map-publication/v0.1", "manifest": manifest, "execution_id": execution})
        } else {
            serde_json::to_value(manifest)?
        };
        let response = match self
            .request(reqwest::Method::POST, endpoint.clone())
            .json(&payload)
            .send()
            .await
        {
            Ok(response) => response,
            Err(error) => match self.publication_already_exists(manifest).await {
                Ok(true) => return Ok(()),
                Ok(false) | Err(_) => {
                    return Err(ArtifactError::remote_outcome_unknown(
                        "manifest publication",
                        error,
                    ));
                }
            },
        };
        if response.status() == StatusCode::CONFLICT {
            return if self.publication_already_exists(manifest).await? {
                Ok(())
            } else {
                Err(ArtifactError::Status {
                    status: response.status(),
                    endpoint: endpoint.to_string(),
                })
            };
        }
        ensure_success(&response, &endpoint)
    }

    /// Checks whether the catalog already contains this durable publication attempt.
    pub(super) async fn publication_already_exists(
        &self,
        manifest: &MapArtifactManifest,
    ) -> Result<bool, ArtifactError> {
        let map_id = manifest.selector().map_id().as_str();
        let revision_id = manifest.selector().revision_id().as_str();
        match self.fetch_manifest(map_id, revision_id).await {
            Ok(existing) => Ok(existing.is_published()
                && is_same_publication_attempt(&existing.manifest, manifest)),
            Err(ArtifactError::Status {
                status: StatusCode::NOT_FOUND,
                ..
            }) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Records one node-local replica transition in the rebuildable Spatial Memory catalog.
    pub async fn record_replica(
        &self,
        manifest: &MapArtifactManifest,
        node_id: &NodeId,
        mission_id: &MissionId,
        status: ReplicaEvidenceStatus,
    ) -> Result<(), ArtifactError> {
        let map_id = manifest.selector().map_id().as_str();
        let revision_id = manifest.selector().revision_id().as_str();
        let endpoint = self.path(&["v1", "maps", map_id, "revisions", revision_id, "replicas"])?;
        let payload = serde_json::json!({
            "manifest": manifest,
            "node_id": node_id.as_str(),
            "mission_id": mission_id.as_str(),
            "status": status.as_str(),
            "anchor_id": match status {
                ReplicaEvidenceStatus::Verified => Some(manifest.anchor_id().as_str()),
                ReplicaEvidenceStatus::Staged | ReplicaEvidenceStatus::Imported => None,
            },
            "reason": null,
        });
        let response = self
            .request(reqwest::Method::POST, endpoint.clone())
            .json(&payload)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("replica evidence", error))?;
        ensure_success(&response, &endpoint)
    }

    /// Records one complete strong localization evidence envelope.
    pub async fn record_localization_evidence(
        &self,
        evidence: &LocalizationVerificationEvidence,
        session_id: &str,
    ) -> Result<(), ArtifactError> {
        let selector = evidence.artifact().selector();
        let endpoint = self.path(&[
            "v1",
            "maps",
            selector.map_id().as_str(),
            "revisions",
            selector.revision_id().as_str(),
            "localization-evidence",
        ])?;
        let response = self
            .request(reqwest::Method::POST, endpoint.clone())
            .header("X-RoboGuide-Node-Id", evidence.node_id().as_str())
            .header("X-RoboGuide-Session-Id", session_id)
            .json(evidence)
            .send()
            .await
            .map_err(|error| {
                ArtifactError::remote_outcome_unknown("localization evidence", error)
            })?;
        ensure_success(&response, &endpoint)
    }

    /// Downloads a digest-addressed blob into a temporary file and atomically renames it.
    pub async fn download_digest(
        &self,
        digest: &str,
        destination: &Path,
        expected_size: u64,
    ) -> Result<(), ArtifactError> {
        self.check_acquisition()?;
        let digest = normalize_digest(digest)?;
        if expected_size > self.max_artifact_bytes {
            return Err(ArtifactError::Configuration(format!(
                "artifact size {expected_size} exceeds configured limit {}",
                self.max_artifact_bytes
            )));
        }
        let raw_digest = digest
            .strip_prefix("sha256:")
            .expect("normalize_digest always returns a sha256-prefixed digest");
        let endpoint = self.path(&["v1", "artifacts", raw_digest])?;
        let response = self
            .request(reqwest::Method::GET, endpoint.clone())
            .send()
            .await?;
        ensure_success(&response, &endpoint)?;
        if let Some(parent) = destination.parent() {
            ensure_directory_tree(parent)?;
        }
        reject_symlink(destination)?;
        let temporary = temporary_path(destination);
        let result = self
            .download_response(response, &temporary, digest.clone(), expected_size)
            .await
            .and_then(|()| self.check_acquisition());
        if result.is_err() {
            let _ = remove_temporary_file(&temporary).await;
        }
        result?;
        if let Err(error) = durable_rename(&temporary, destination).await {
            let _ = remove_temporary_file(&temporary).await;
            return Err(error);
        }
        Ok(())
    }

    /// Streams one local file to a server-created upload and finalizes it.
    pub async fn upload_file(
        &self,
        metadata: &ArtifactOutputBindingConfig,
        source: &Path,
    ) -> Result<ArtifactOutput, ArtifactError> {
        let (digest, byte_size, upload_id) = self.upload_blob(source).await?;
        Ok(ArtifactOutput {
            binding_id: metadata.id.clone(),
            map_id: metadata.map_id.clone(),
            revision_id: metadata.revision_id.clone(),
            content_digest: digest,
            byte_size,
            upload_id,
        })
    }

    /// Uploads opaque bytes and verifies they match a Memory artifact reference.
    pub async fn upload_memory_file(
        &self,
        source: &Path,
        expected: &domain::MemoryArtifactRef,
    ) -> Result<(), ArtifactError> {
        let source_file = self.verify_memory_file(source, expected).await?;
        self.upload_open_blob(
            source_file,
            expected.content_digest().as_str().to_string(),
            expected.byte_size(),
        )
        .await?;
        Ok(())
    }

    /// Checks actual local bytes even when an exact immutable publication needs no repeated upload.
    pub(super) async fn verify_memory_file(
        &self,
        source: &Path,
        expected: &domain::MemoryArtifactRef,
    ) -> Result<tokio::fs::File, ArtifactError> {
        let mut source_file = open_regular_file(source).await?;
        let (preflight_digest, preflight_size) = digest_open_file(
            &mut source_file,
            self.chunk_size_bytes,
            self.max_artifact_bytes,
        )
        .await?;
        if preflight_digest != expected.content_digest().as_str() {
            return Err(ArtifactError::DigestMismatch {
                expected: expected.content_digest().as_str().to_string(),
                actual: preflight_digest,
            });
        }
        if preflight_size != expected.byte_size() {
            return Err(ArtifactError::SizeMismatch {
                expected: expected.byte_size(),
                actual: preflight_size,
            });
        }
        source_file.seek(SeekFrom::Start(0)).await?;
        Ok(source_file)
    }

    /// Streams one opaque local file into the content-addressed Artifact store.
    pub(super) async fn upload_blob(
        &self,
        source: &Path,
    ) -> Result<(String, u64, String), ArtifactError> {
        let mut source_file = open_regular_file(source).await?;
        let (digest, byte_size) = digest_open_file(
            &mut source_file,
            self.chunk_size_bytes,
            self.max_artifact_bytes,
        )
        .await?;
        source_file.seek(SeekFrom::Start(0)).await?;
        self.upload_open_blob(source_file, digest, byte_size).await
    }

    /// Uploads and finalizes bytes from an already hashed, rewound regular-file handle.
    pub(super) async fn upload_open_blob(
        &self,
        mut source_file: tokio::fs::File,
        digest: String,
        byte_size: u64,
    ) -> Result<(String, u64, String), ArtifactError> {
        let create_endpoint = self.path(&["v1", "artifact-uploads"])?;
        let upload_id = format!(
            "{}{}",
            self.upload_prefix.as_deref().unwrap_or_default(),
            new_upload_id()
        );
        let create_payload = serde_json::json!({"upload_id": upload_id});
        let response = self
            .request(reqwest::Method::POST, create_endpoint.clone())
            .json(&create_payload)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("upload creation", error))?;
        ensure_success(&response, &create_endpoint)?;
        let create_body = response.json::<Value>().await?;
        let upload_id = create_body
            .get("upload_id")
            .or_else(|| create_body.get("id"))
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or(ArtifactError::MissingUploadId)?
            .to_string();
        validate_segment(&upload_id, "upload_id")?;

        let content_endpoint = self.path(&["v1", "artifact-uploads", &upload_id, "content"])?;
        let mut offset = 0_u64;
        let upload_chunk_size = self
            .chunk_size_bytes
            .saturating_mul(8)
            .clamp(1, MAX_UPLOAD_CHUNK_BYTES);
        let mut buffer = vec![0_u8; upload_chunk_size];
        let mut attempts = 0_u8;
        while offset < byte_size {
            source_file.seek(SeekFrom::Start(offset)).await?;
            let expected = (byte_size - offset).min(buffer.len() as u64) as usize;
            source_file.read_exact(&mut buffer[..expected]).await?;
            let chunk = Bytes::copy_from_slice(&buffer[..expected]);
            let expected_end = offset + expected as u64;
            match self
                .append_upload_chunk(&content_endpoint, chunk.clone())
                .await
            {
                Ok(received) if received == expected_end => {
                    offset = received;
                    attempts = 0;
                }
                Ok(received) if received >= offset && received < expected_end => {
                    offset = received;
                    attempts = 0;
                }
                Ok(received) => {
                    return Err(ArtifactError::Configuration(format!(
                        "artifact upload server advanced to invalid offset {received}"
                    )));
                }
                Err(error) => {
                    if matches!(
                        &error,
                        ArtifactError::Status { status, .. }
                            if !status.is_server_error()
                                && *status != reqwest::StatusCode::REQUEST_TIMEOUT
                    ) {
                        return Err(error);
                    }
                    if attempts >= MAX_UPLOAD_RETRIES {
                        return Err(error);
                    }
                    attempts = attempts.saturating_add(1);
                    let observation = self.observe_upload(&upload_id).await?;
                    if observation
                        .get("record")
                        .and_then(|record| record.get("phase"))
                        .and_then(Value::as_str)
                        == Some("completed")
                    {
                        verify_completed_upload(&observation, &digest, byte_size)?;
                        return Ok((digest, byte_size, upload_id));
                    }
                    let received = observation
                        .get("received_bytes")
                        .and_then(Value::as_u64)
                        .ok_or_else(|| {
                            ArtifactError::Configuration(
                                "artifact upload observation lacks received_bytes".into(),
                            )
                        })?;
                    if received > byte_size {
                        return Err(ArtifactError::Configuration(
                            "artifact upload observation exceeds declared size".into(),
                        ));
                    }
                    if received < offset {
                        return Err(ArtifactError::Configuration(
                            "artifact upload observation moved backwards".into(),
                        ));
                    }
                    offset = received;
                }
            }
        }

        let finalize_endpoint = self.path(&["v1", "artifact-uploads", &upload_id, "finalize"])?;
        let finalize_payload = serde_json::json!({
            "content_digest": digest,
            "byte_size": byte_size,
        });
        let mut attempts = 0_u8;
        loop {
            let response = self
                .request(reqwest::Method::POST, finalize_endpoint.clone())
                .json(&finalize_payload)
                .send()
                .await;
            match response {
                Ok(response) if response.status().is_success() => {
                    return Ok((digest, byte_size, upload_id));
                }
                Ok(response) => {
                    let status = response.status();
                    if !(status.is_server_error() || status == reqwest::StatusCode::REQUEST_TIMEOUT)
                    {
                        return Err(ArtifactError::Status {
                            status,
                            endpoint: finalize_endpoint.to_string(),
                        });
                    }
                    if attempts >= MAX_UPLOAD_RETRIES {
                        return Err(ArtifactError::Status {
                            status,
                            endpoint: finalize_endpoint.to_string(),
                        });
                    }
                    attempts = attempts.saturating_add(1);
                    let observation = self.observe_upload(&upload_id).await?;
                    if observation
                        .get("record")
                        .and_then(|record| record.get("phase"))
                        .and_then(Value::as_str)
                        == Some("completed")
                    {
                        verify_completed_upload(&observation, &digest, byte_size)?;
                        return Ok((digest, byte_size, upload_id));
                    }
                }
                Err(error) => {
                    let unknown =
                        ArtifactError::remote_outcome_unknown("upload finalization", error);
                    if attempts >= MAX_UPLOAD_RETRIES {
                        return Err(unknown);
                    }
                    attempts = attempts.saturating_add(1);
                    let observation = self.observe_upload(&upload_id).await?;
                    if observation
                        .get("record")
                        .and_then(|record| record.get("phase"))
                        .and_then(Value::as_str)
                        == Some("completed")
                    {
                        verify_completed_upload(&observation, &digest, byte_size)?;
                        return Ok((digest, byte_size, upload_id));
                    }
                }
            }
        }
    }

    /// Appends one bounded chunk and returns the server's durable received-byte offset.
    async fn append_upload_chunk(
        &self,
        endpoint: &Url,
        chunk: Bytes,
    ) -> Result<u64, ArtifactError> {
        let response = self
            .request(reqwest::Method::POST, endpoint.clone())
            .header(reqwest::header::CONTENT_TYPE, "application/octet-stream")
            .header(reqwest::header::CONTENT_LENGTH, chunk.len().to_string())
            .body(chunk)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("upload content", error))?;
        ensure_success(&response, endpoint)?;
        let body = response.json::<Value>().await?;
        body.get("received_bytes")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                ArtifactError::Configuration("upload response lacks received_bytes".into())
            })
    }

    /// Reads the original upload observation without creating a second upload identity.
    async fn observe_upload(&self, upload_id: &str) -> Result<Value, ArtifactError> {
        let endpoint = self.path(&["v1", "artifact-uploads", upload_id])?;
        let response = self
            .request(reqwest::Method::GET, endpoint.clone())
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("upload observation", error))?;
        ensure_success(&response, &endpoint)?;
        Ok(response.json::<Value>().await?)
    }

    /// Builds a URL by appending escaped path segments to the configured endpoint.
    pub(super) fn path(&self, segments: &[&str]) -> Result<Url, ArtifactError> {
        let mut endpoint = self.endpoint.clone();
        let mut path = endpoint.path_segments_mut().map_err(|_| {
            ArtifactError::Configuration("endpoint cannot accept path segments".into())
        })?;
        path.pop_if_empty();
        for segment in segments {
            path.push(segment);
        }
        drop(path);
        Ok(endpoint)
    }

    /// Streams one response into a temporary destination while hashing and bounding it.
    pub(super) async fn download_response(
        &self,
        mut response: reqwest::Response,
        destination: &Path,
        expected_digest: String,
        expected_size: u64,
    ) -> Result<(), ArtifactError> {
        let mut file = tokio::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(destination)
            .await?;
        let mut hasher = Sha256::new();
        let mut size = 0_u64;
        while let Some(chunk) = response.chunk().await? {
            size = size.saturating_add(chunk.len() as u64);
            if size > self.max_artifact_bytes {
                return Err(ArtifactError::Configuration(
                    "download exceeds configured artifact limit".to_string(),
                ));
            }
            for part in chunk.chunks(self.chunk_size_bytes) {
                self.check_acquisition()?;
                hasher.update(part);
                file.write_all(part).await?;
            }
        }
        self.check_acquisition()?;
        file.flush().await?;
        if size != expected_size {
            return Err(ArtifactError::SizeMismatch {
                expected: expected_size,
                actual: size,
            });
        }
        let actual = format!("sha256:{:x}", hasher.finalize());
        if actual != expected_digest {
            return Err(ArtifactError::DigestMismatch {
                expected: expected_digest,
                actual,
            });
        }
        file.sync_all().await?;
        Ok(())
    }

    /// Rechecks original receiver authority without issuing requests or extending its lifetime.
    fn check_acquisition(&self) -> Result<(), ArtifactError> {
        if let Some(guard) = &self.acquisition_guard {
            guard()?;
        }
        Ok(())
    }
}

/// Verifies a completed upload observation before treating a lost finalization response as success.
fn verify_completed_upload(
    observation: &Value,
    expected_digest: &str,
    expected_size: u64,
) -> Result<(), ArtifactError> {
    let record = observation
        .get("record")
        .ok_or_else(|| ArtifactError::Configuration("upload observation lacks record".into()))?;
    if record.get("content_digest").and_then(Value::as_str) != Some(expected_digest)
        || record.get("byte_size").and_then(Value::as_u64) != Some(expected_size)
    {
        return Err(ArtifactError::Configuration(
            "completed upload observation differs from original byte proof".into(),
        ));
    }
    Ok(())
}

/// Return whether an Artifact endpoint is outside the trusted local development profile.
fn artifact_endpoint_requires_mtls(endpoint: &Url) -> bool {
    if endpoint.scheme() == "https" {
        return true;
    }
    endpoint.host_str().is_some_and(|host| {
        if host.eq_ignore_ascii_case("localhost") {
            return false;
        }
        host.parse::<std::net::IpAddr>()
            .map(|address| !address.is_loopback())
            .unwrap_or(true)
    })
}

/// Load a complete client certificate profile from deployment-owned environment variables.
fn artifact_client_tls_material()
-> Result<(std::path::PathBuf, std::path::PathBuf, std::path::PathBuf), ArtifactError> {
    let names = [
        "ROBOGUIDE_ARTIFACT_CLIENT_CERT",
        "ROBOGUIDE_ARTIFACT_CLIENT_KEY",
        "ROBOGUIDE_ARTIFACT_CLIENT_CA",
    ];
    let values = names.iter().map(std::env::var_os).collect::<Vec<_>>();
    let (Some(certificate), Some(key), Some(ca)) =
        (values[0].as_ref(), values[1].as_ref(), values[2].as_ref())
    else {
        return Err(ArtifactError::Configuration(
            "remote Artifact endpoint requires ROBOGUIDE_ARTIFACT_CLIENT_CERT, _KEY and _CA"
                .to_string(),
        ));
    };
    let certificate = std::path::PathBuf::from(certificate);
    let key = std::path::PathBuf::from(key);
    let ca = std::path::PathBuf::from(ca);
    for path in [&certificate, &key, &ca] {
        if !path.is_file() {
            return Err(ArtifactError::Configuration(format!(
                "Artifact client TLS file is unavailable: {}",
                path.display()
            )));
        }
    }
    Ok((certificate, key, ca))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Remote plaintext Artifact endpoints are rejected before a request can leave the process.
    #[test]
    fn remote_artifact_endpoint_requires_https() {
        let error = match ArtifactClient::new("http://artifact.example:8090", 1024, 4096, 100, 100)
        {
            Ok(_) => panic!("remote plaintext endpoint must fail closed"),
            Err(error) => error,
        };
        assert!(error.to_string().contains("must use https"));
    }
}
