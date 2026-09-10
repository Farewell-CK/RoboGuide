//! HTTP Artifact data-plane client operations.

use super::*;

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
        Ok(Self {
            client: Client::builder()
                .connect_timeout(Duration::from_millis(connect_timeout_ms))
                .read_timeout(Duration::from_millis(read_timeout_ms))
                .build()?,
            endpoint,
            chunk_size_bytes,
            max_artifact_bytes,
        })
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
        let response = self.client.get(endpoint.clone()).send().await?;
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
        let response = self.client.get(endpoint.clone()).send().await?;
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
            .client
            .post(endpoint.clone())
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

    /// Records generic Memory staged/imported/rejected evidence through the shared catalog.
    pub async fn record_memory_replica(
        &self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        session_id: &str,
        consumer_provider_id: &str,
        status: &str,
        reason: Option<&str>,
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
        });
        let response = self
            .client
            .post(endpoint.clone())
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
        let response = match self
            .client
            .post(endpoint.clone())
            .json(manifest)
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
            .client
            .post(endpoint.clone())
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
            .client
            .post(endpoint.clone())
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
        let response = self.client.get(endpoint.clone()).send().await?;
        ensure_success(&response, &endpoint)?;
        if let Some(parent) = destination.parent() {
            ensure_directory_tree(parent)?;
        }
        reject_symlink(destination)?;
        let temporary = temporary_path(destination);
        let result = self
            .download_response(response, &temporary, digest.clone(), expected_size)
            .await;
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
        self.upload_open_blob(source_file, preflight_digest, preflight_size)
            .await?;
        Ok(())
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
        source_file: tokio::fs::File,
        digest: String,
        byte_size: u64,
    ) -> Result<(String, u64, String), ArtifactError> {
        let create_endpoint = self.path(&["v1", "artifact-uploads"])?;
        let upload_id = new_upload_id();
        let create_payload = serde_json::json!({"upload_id": upload_id});
        let response = self
            .client
            .post(create_endpoint.clone())
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
        let body = self.stream_file(source_file);
        let response = self
            .client
            .post(content_endpoint.clone())
            .header(reqwest::header::CONTENT_TYPE, "application/octet-stream")
            .header(reqwest::header::CONTENT_LENGTH, byte_size.to_string())
            .body(body)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("upload content", error))?;
        ensure_success(&response, &content_endpoint)?;

        let finalize_endpoint = self.path(&["v1", "artifact-uploads", &upload_id, "finalize"])?;
        let finalize_payload = serde_json::json!({
            "content_digest": digest,
            "byte_size": byte_size,
        });
        let response = self
            .client
            .post(finalize_endpoint.clone())
            .json(&finalize_payload)
            .send()
            .await
            .map_err(|error| ArtifactError::remote_outcome_unknown("upload finalization", error))?;
        ensure_success(&response, &finalize_endpoint)?;
        Ok((digest, byte_size, upload_id))
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
            hasher.update(&chunk);
            file.write_all(&chunk).await?;
        }
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

    /// Creates a streaming request body backed by one already validated open file.
    pub(super) fn stream_file(&self, file: tokio::fs::File) -> reqwest::Body {
        let chunk_size = self.chunk_size_bytes;
        let (sender, receiver) = mpsc::channel::<Result<Bytes, std::io::Error>>(8);
        tokio::spawn(async move {
            let mut file = file;
            let mut buffer = vec![0_u8; chunk_size];
            loop {
                match file.read(&mut buffer).await {
                    Ok(0) => break,
                    Ok(length) => {
                        if sender
                            .send(Ok(Bytes::copy_from_slice(&buffer[..length])))
                            .await
                            .is_err()
                        {
                            break;
                        }
                    }
                    Err(error) => {
                        let _ = sender.send(Err(error)).await;
                        break;
                    }
                }
            }
        });
        reqwest::Body::wrap_stream(ReceiverStream::new(receiver))
    }
}
