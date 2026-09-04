//! Generic Spatial Memory artifact staging for the node-side service.
//!
//! This module knows only the versioned map manifest envelope and the independent HTTP
//! artifact data plane. It does not parse map bytes, choose an active map, or alter Node
//! Protocol execution messages.

use crate::{
    ArtifactInputBindingConfig, ArtifactOutputBindingConfig, ArtifactServiceConfig,
    CompiledArtifactService,
};
use bytes::Bytes;
use domain::{
    ContentDigest, LocalizationVerificationEvidence, MapArtifactManifest, MapArtifactRef, MapId,
    MapRevisionId, MapRevisionSelector, MapRevisionStatus, MemoryArtifactManifest, MemorySelector,
    MissionId, NodeId, SpatialAnchorId, TaskId, TaskRef, TimestampMs,
};
use reqwest::{Client, StatusCode, Url};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fmt::{Display, Formatter};
use std::io::SeekFrom;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

/// A transport-neutral manifest envelope returned by the artifact catalog.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactManifestEnvelope {
    /// Exact typed immutable manifest returned by the catalog.
    pub manifest: MapArtifactManifest,
    /// Current catalog lifecycle for the selected revision.
    pub status: Option<MapRevisionStatus>,
}

impl ArtifactManifestEnvelope {
    /// Returns the canonical digest after validation.
    pub fn normalized_digest(&self) -> Result<String, ArtifactError> {
        normalize_digest(self.manifest.artifact().content_digest().as_str())
    }

    /// Returns whether the catalog explicitly marked this revision as published.
    pub fn is_published(&self) -> bool {
        self.status == Some(MapRevisionStatus::Published)
    }
}

/// Replica evidence that the node may report after local artifact handling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReplicaEvidenceStatus {
    /// Digest-verified bytes were staged under the deployment-owned cache root.
    Staged,
    /// The local import workflow completed successfully.
    Imported,
    /// The local localization workflow verified the manifest's fixed anchor.
    Verified,
}

impl ReplicaEvidenceStatus {
    /// Returns the artifact HTTP wire spelling for this evidence transition.
    const fn as_str(self) -> &'static str {
        match self {
            Self::Staged => "staged",
            Self::Imported => "imported",
            Self::Verified => "verified",
        }
    }
}

/// Provenance supplied when a node publishes a locally produced map revision.
///
/// The bytes and map metadata are supplied by [`ArtifactOutputBindingConfig`].  This value
/// carries the execution identities that belong to Mission/Runtime evidence and therefore keeps
/// them separate from the transport upload itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactProvenance {
    /// Node that produced the artifact.
    pub producer_node_id: NodeId,
    /// Optional local embodied system that produced the artifact.
    pub producer_local_system_id: Option<domain::LocalSystemId>,
    /// Mission that produced the artifact.
    pub source_mission_id: MissionId,
    /// Optional execution identity associated with production.
    pub source_execution_id: Option<String>,
    /// Optional source task associated with production.
    pub source_task_ref: Option<TaskRef>,
    /// RoboGuide-local creation timestamp for the manifest.
    pub created_at: TimestampMs,
    /// Optional immutable parent revision for lineage.
    pub parent_revision_id: Option<MapRevisionId>,
}

/// A successfully staged immutable map artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StagedArtifact {
    /// Deployment-local input binding identity.
    pub binding_id: String,
    /// Logical map identity.
    pub map_id: String,
    /// Immutable revision identity.
    pub revision_id: String,
    /// Verified canonical SHA-256 digest (`sha256:<64 lowercase hex>`).
    pub content_digest: String,
    /// Verified artifact size.
    pub byte_size: u64,
    /// Controlled local path supplied to the local workflow.
    pub path: PathBuf,
    /// Exact catalog manifest used to verify and stage the bytes.
    pub manifest: MapArtifactManifest,
}

/// Result of publishing one fixed local output artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactOutput {
    /// Deployment-local output binding identity.
    pub binding_id: String,
    /// Logical map identity.
    pub map_id: String,
    /// Immutable revision identity.
    pub revision_id: String,
    /// Verified canonical SHA-256 digest (`sha256:<64 lowercase hex>`).
    pub content_digest: String,
    /// Uploaded artifact size.
    pub byte_size: u64,
    /// Server-side upload identity used during finalization.
    pub upload_id: String,
}

/// Immutable node-local output frozen after its producing workflow completed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedArtifact {
    /// Deployment-local output binding identity.
    pub binding_id: String,
    /// Content-addressed read-only copy used by every later publication attempt.
    pub path: PathBuf,
    /// Typed immutable manifest carrying the producing execution and Task provenance.
    pub manifest: MapArtifactManifest,
}

/// Errors raised while validating, downloading, or publishing an artifact.
#[derive(Debug, thiserror::Error)]
pub enum ArtifactError {
    /// Deployment configuration violates a fixed-path or endpoint invariant.
    #[error("invalid artifact configuration: {0}")]
    Configuration(String),
    /// HTTP transport failed before a response was received.
    #[error("artifact HTTP request failed: {0}")]
    Http(#[from] reqwest::Error),
    /// A remote write may have committed even though no conclusive response was received.
    #[error("artifact remote outcome is unknown during {operation}: {source}")]
    RemoteOutcomeUnknown {
        /// Remote write phase whose acknowledgement was lost.
        operation: &'static str,
        /// Underlying transport failure.
        #[source]
        source: reqwest::Error,
    },
    /// The artifact service returned a non-success status.
    #[error("artifact service returned HTTP {status} for {endpoint}")]
    Status {
        /// Returned HTTP status.
        status: StatusCode,
        /// Requested endpoint.
        endpoint: String,
    },
    /// A response body was not a valid manifest or upload acknowledgement.
    #[error("invalid artifact response: {0}")]
    Json(#[from] serde_json::Error),
    /// Local cache I/O failed.
    #[error("artifact cache I/O failed: {0}")]
    Io(#[from] std::io::Error),
    /// The advertised digest did not match downloaded or uploaded bytes.
    #[error("artifact digest mismatch: expected {expected}, got {actual}")]
    DigestMismatch {
        /// Expected canonical `sha256:<64 lowercase hex>` digest.
        expected: String,
        /// Computed canonical `sha256:<64 lowercase hex>` digest.
        actual: String,
    },
    /// The advertised byte size did not match transferred bytes.
    #[error("artifact size mismatch: expected {expected}, got {actual}")]
    SizeMismatch {
        /// Expected byte count.
        expected: u64,
        /// Computed byte count.
        actual: u64,
    },
    /// A manifest did not match its statically configured binding.
    #[error("artifact manifest does not match binding: {0}")]
    ManifestMismatch(String),
    /// A required upload identifier was missing from the server response.
    #[error("artifact upload response did not contain an upload id")]
    MissingUploadId,
    /// A typed domain manifest violated a spatial-memory invariant.
    #[error("invalid artifact manifest: {0}")]
    Domain(#[from] domain::DomainError),
}

impl ArtifactError {
    /// Returns whether replay requires explicit recovery authority because a write may exist.
    pub const fn outcome_unknown(&self) -> bool {
        matches!(self, Self::RemoteOutcomeUnknown { .. })
    }

    /// Wraps a transport failure from a request that may already have changed remote state.
    fn remote_outcome_unknown(operation: &'static str, source: reqwest::Error) -> Self {
        Self::RemoteOutcomeUnknown { operation, source }
    }
}

/// HTTP client for the independent artifact data plane.
#[derive(Clone)]
pub struct ArtifactClient {
    /// Reusable HTTP client with no map-specific protocol state.
    client: Client,
    /// Absolute artifact service endpoint.
    endpoint: Url,
    /// Bounded transfer chunk size.
    chunk_size_bytes: usize,
    /// Maximum accepted artifact size.
    max_artifact_bytes: u64,
}

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
    async fn memory_publication_already_exists(
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
    async fn publication_already_exists(
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
    async fn upload_blob(&self, source: &Path) -> Result<(String, u64, String), ArtifactError> {
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
    async fn upload_open_blob(
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
    fn path(&self, segments: &[&str]) -> Result<Url, ArtifactError> {
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
    async fn download_response(
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
    fn stream_file(&self, file: tokio::fs::File) -> reqwest::Body {
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

/// High-level node-side staging facade for static input/output bindings.
#[derive(Clone)]
pub struct ArtifactStager {
    /// HTTP artifact client.
    client: ArtifactClient,
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
    fn validate_prepared_metadata(
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
    ) -> Result<(), ArtifactError> {
        self.client.record_localization_evidence(evidence).await
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
    fn target_path(&self, path: &Path) -> Result<PathBuf, ArtifactError> {
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
    fn blob_path(&self, digest: &str) -> Result<PathBuf, ArtifactError> {
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

/// Validates the static identity and metadata required to build an output manifest.
fn validate_output_binding(binding: &ArtifactOutputBindingConfig) -> Result<(), ArtifactError> {
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
fn build_output_manifest(
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
fn validate_manifest_binding(
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
async fn digest_open_file(
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
async fn snapshot_open_file(
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
async fn verified_file(
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
async fn copy_verified_atomic(
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
async fn seal_local_artifact(path: &Path) -> Result<(), ArtifactError> {
    let file = open_regular_file(path).await?;
    let mut permissions = file.metadata().await?.permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions).await?;
    file.sync_all().await?;
    Ok(())
}

/// Removes a temporary artifact path while treating an already absent path as success.
async fn remove_temporary_file(path: &Path) -> Result<(), ArtifactError> {
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
async fn durable_rename(source: &Path, destination: &Path) -> Result<(), ArtifactError> {
    reject_symlink(destination)?;
    tokio::fs::rename(source, destination).await?;
    sync_parent_directory(destination)?;
    if source.parent() != destination.parent() {
        sync_parent_directory(source)?;
    }
    Ok(())
}

/// Creates a non-overwriting hard link and durably commits the destination directory entry.
async fn durable_hard_link(source: &Path, destination: &Path) -> Result<(), std::io::Error> {
    tokio::fs::hard_link(source, destination).await?;
    sync_parent_directory_io(destination)?;
    Ok(())
}

/// Synchronizes the parent directory containing one changed artifact entry.
fn sync_parent_directory(path: &Path) -> Result<(), ArtifactError> {
    sync_parent_directory_io(path).map_err(ArtifactError::Io)
}

/// Performs the directory synchronization used by artifact operations returning raw I/O errors.
fn sync_parent_directory_io(path: &Path) -> Result<(), std::io::Error> {
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
fn ensure_directory_tree(path: &Path) -> Result<(), ArtifactError> {
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
fn validate_directory_tree(path: &Path, allow_missing: bool) -> Result<(), ArtifactError> {
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
fn validate_directory_component(
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
async fn open_regular_file(path: &Path) -> Result<tokio::fs::File, ArtifactError> {
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
fn add_no_follow_flag(options: &mut tokio::fs::OpenOptions) {
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    #[cfg(not(unix))]
    let _ = options;
}

/// Converts a no-follow rejection into a stable configuration error and preserves other I/O.
fn artifact_open_error(path: &Path, error: std::io::Error) -> ArtifactError {
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
fn require_regular_file(path: &Path) -> Result<(), ArtifactError> {
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
fn reject_symlink(path: &Path) -> Result<(), ArtifactError> {
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
fn temporary_path(path: &Path) -> PathBuf {
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
fn new_upload_id() -> String {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!("node-upload-{stamp}")
}

/// Validates one URL path segment against traversal and empty values.
fn validate_segment(value: &str, field: &str) -> Result<(), ArtifactError> {
    if value.trim().is_empty() || value != value.trim() || value.contains(['/', '\\']) {
        return Err(ArtifactError::Configuration(format!(
            "{field} must be a nonblank path-safe identity"
        )));
    }
    Ok(())
}

/// Validates a static binding identity.
fn validate_binding_identity(value: &str, field: &str) -> Result<(), ArtifactError> {
    validate_segment(value, field)
}

/// Resolves and validates a deployment-owned cache root.
fn resolve_deployment_path(directory: &Path, path: &Path) -> Result<PathBuf, ArtifactError> {
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
fn normalize_digest(value: &str) -> Result<String, ArtifactError> {
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
fn is_same_publication_attempt(
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
fn parse_revision_status_value(value: &Value) -> Result<MapRevisionStatus, ArtifactError> {
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
fn ensure_success(response: &reqwest::Response, endpoint: &Url) -> Result<(), ArtifactError> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// Builds one typed manifest for transport-envelope unit tests.
    fn manifest(digest: &str) -> MapArtifactManifest {
        manifest_at(digest, 1)
    }

    /// Builds one typed publication attempt with a caller-controlled creation timestamp.
    fn manifest_at(digest: &str, created_at_ms: u64) -> MapArtifactManifest {
        let artifact = MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("lab").expect("map id"),
                MapRevisionId::new("r1").expect("revision id"),
            ),
            ContentDigest::new(digest).expect("digest"),
            4,
        );
        MapArtifactManifest::new(
            artifact,
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id"),
            None,
            MissionId::new("mission-a").expect("mission id"),
            Some("execution-a".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("anchor-lab").expect("anchor"),
            Some(0.05),
            TimestampMs::new(created_at_ms),
            None,
        )
        .expect("manifest")
    }

    /// Constructs a valid artifact client for local unit tests.
    fn client(directory: &Path) -> ArtifactStager {
        client_at(directory, "http://127.0.0.1:18080")
    }

    /// Constructs a valid artifact client against one test-owned endpoint.
    fn client_at(directory: &Path, endpoint: &str) -> ArtifactStager {
        ArtifactStager::from_config(
            &ArtifactServiceConfig {
                endpoint: endpoint.to_string(),
                cache_directory: PathBuf::from("cache"),
                max_artifact_bytes: 1024,
                chunk_size_bytes: 4,
                connect_timeout_ms: 5_000,
                read_timeout_ms: 30_000,
                input_bindings: Vec::new(),
                output_bindings: Vec::new(),
            },
            directory,
        )
        .expect("stager config is valid")
    }

    /// Builds one complete static output binding for local freeze tests.
    fn output_binding() -> ArtifactOutputBindingConfig {
        ArtifactOutputBindingConfig {
            id: "lab-r1-output".to_string(),
            map_id: "lab".to_string(),
            revision_id: "r1".to_string(),
            source_path: PathBuf::from("outputs/lab-r1.bundle"),
            media_type: "application/octet-stream".to_string(),
            format_name: "grid".to_string(),
            format_version: "v1".to_string(),
            root_frame: "map".to_string(),
            coordinate_convention: "enu".to_string(),
            spatial_anchor_id: "anchor-lab".to_string(),
            resolution_meters: Some(0.05),
        }
    }

    /// Builds exact provenance for the execution that produced a freeze-test artifact.
    fn build_provenance() -> ArtifactProvenance {
        let mission = MissionId::new("mission-a").expect("mission id");
        ArtifactProvenance {
            producer_node_id: NodeId::new("dog-a").expect("node id"),
            producer_local_system_id: Some(
                domain::LocalSystemId::new("mapping-runtime").expect("local system id"),
            ),
            source_mission_id: mission.clone(),
            source_execution_id: Some("build-execution-a".to_string()),
            source_task_ref: Some(TaskRef::new(
                mission,
                TaskId::new("build-map").expect("task id"),
            )),
            created_at: TimestampMs::new(7),
            parent_revision_id: None,
        }
    }

    /// Digest normalization accepts both contract spellings and rejects malformed values.
    #[test]
    fn normalizes_digest_prefix() {
        let plain = "a".repeat(64);
        assert_eq!(
            normalize_digest(&plain).expect("plain digest"),
            format!("sha256:{plain}")
        );
        assert_eq!(
            normalize_digest(&format!("sha256:{plain}")).expect("prefixed digest"),
            format!("sha256:{plain}")
        );
        assert!(normalize_digest(&"A".repeat(64)).is_err());
    }

    /// Binding paths cannot escape the deployment-owned cache root.
    #[test]
    fn rejects_path_traversal() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let stager = client(directory.path());
        let binding = ArtifactInputBindingConfig {
            id: "map".to_string(),
            map_id: "lab".to_string(),
            revision_id: "r1".to_string(),
            content_digest: None,
            target_path: PathBuf::from("../outside.map"),
        };
        let result = stager.target_path(&binding.target_path);
        assert!(matches!(result, Err(ArtifactError::Configuration(_))));
    }

    /// A verified file is reused only when both size and digest match.
    #[tokio::test]
    async fn verifies_cached_file_identity() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("map.bin");
        let mut file = std::fs::File::create(&path).expect("file creates");
        file.write_all(b"map-bytes").expect("fixture writes");
        let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));
        assert!(
            verified_file(&path, &digest, 9, 1024)
                .await
                .expect("verify runs")
        );
        assert!(
            !verified_file(&path, &digest, 8, 1024)
                .await
                .expect("size differs")
        );
    }

    /// Input staging exposes only verified read-only bytes and removes rejected temporary copies.
    #[tokio::test]
    async fn copies_only_verified_bytes_to_the_input_target() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let source = directory.path().join("source.bin");
        let target = directory.path().join("target.bin");
        tokio::fs::write(&source, b"wrong-map")
            .await
            .expect("invalid source writes");
        let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));

        assert!(matches!(
            copy_verified_atomic(&source, &target, &digest, 9, 1024).await,
            Err(ArtifactError::ManifestMismatch(_))
        ));
        assert!(!target.exists());

        tokio::fs::write(&source, b"map-bytes")
            .await
            .expect("valid source writes");
        copy_verified_atomic(&source, &target, &digest, 9, 1024)
            .await
            .expect("verified input stages");
        assert_eq!(
            tokio::fs::read(&target).await.expect("target reads"),
            b"map-bytes"
        );
        assert!(
            tokio::fs::metadata(&target)
                .await
                .expect("target metadata reads")
                .permissions()
                .readonly()
        );
    }

    /// Replica completion requires the exact regular staged file and rejects later mutation.
    #[tokio::test]
    async fn verifies_staged_input_against_immutable_manifest() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let stager = client(directory.path());
        let digest = format!("sha256:{:x}", Sha256::digest(b"maps"));
        let binding = ArtifactInputBindingConfig {
            id: "lab-r1-input".to_string(),
            map_id: "lab".to_string(),
            revision_id: "r1".to_string(),
            content_digest: Some(digest.clone()),
            target_path: PathBuf::from("inputs/lab-r1.bundle"),
        };
        let manifest = manifest(&digest);
        let target = stager
            .target_path(&binding.target_path)
            .expect("target path validates");
        ensure_directory_tree(target.parent().expect("target has parent"))
            .expect("target directories exist");

        assert!(matches!(
            stager.verify_staged_input(&binding, &manifest).await,
            Err(ArtifactError::ManifestMismatch(_))
        ));
        tokio::fs::write(&target, b"maps")
            .await
            .expect("staged bytes write");
        stager
            .verify_staged_input(&binding, &manifest)
            .await
            .expect("exact staged bytes verify");
        tokio::fs::write(&target, b"evil")
            .await
            .expect("staged bytes mutate");
        assert!(matches!(
            stager.verify_staged_input(&binding, &manifest).await,
            Err(ArtifactError::ManifestMismatch(_))
        ));
    }

    /// Cache roots and binding parents cannot traverse symbolic-link directory components.
    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_cache_and_binding_directory_components() {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir().expect("temporary directory");
        let outside = directory.path().join("outside");
        std::fs::create_dir(&outside).expect("outside directory creates");
        let linked_cache = directory.path().join("linked-cache");
        symlink(&outside, &linked_cache).expect("cache symlink creates");
        let config = ArtifactServiceConfig {
            endpoint: "http://127.0.0.1:18080".to_string(),
            cache_directory: PathBuf::from("linked-cache"),
            max_artifact_bytes: 1024,
            chunk_size_bytes: 4,
            connect_timeout_ms: 5_000,
            read_timeout_ms: 30_000,
            input_bindings: Vec::new(),
            output_bindings: Vec::new(),
        };
        assert!(matches!(
            ArtifactStager::from_config(&config, directory.path()),
            Err(ArtifactError::Configuration(_))
        ));

        let stager = client(directory.path());
        ensure_directory_tree(stager.cache_directory()).expect("cache directory creates");
        symlink(&outside, stager.cache_directory().join("inputs"))
            .expect("binding parent symlink creates");
        assert!(matches!(
            stager.resolve_path(Path::new("inputs/lab-r1.bundle")),
            Err(ArtifactError::Configuration(_))
        ));
    }

    /// Symbolic-link leaves fail closed before verification, freezing, or upload transport.
    #[cfg(unix)]
    #[tokio::test]
    async fn rejects_symlinked_artifact_source_leaves() {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir().expect("temporary directory");
        let outside = directory.path().join("outside-map.bin");
        tokio::fs::write(&outside, b"map-bytes")
            .await
            .expect("outside fixture writes");
        let linked = directory.path().join("linked-map.bin");
        symlink(&outside, &linked).expect("source symlink creates");
        let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));

        assert!(matches!(
            verified_file(&linked, &digest, 9, 1024).await,
            Err(ArtifactError::Configuration(_))
        ));

        let stager = client(directory.path());
        assert!(matches!(
            stager.client.upload_file(&output_binding(), &linked).await,
            Err(ArtifactError::Configuration(_))
        ));

        let binding = output_binding();
        let output_path = stager
            .prepare_output_path(&binding)
            .await
            .expect("output parent prepares");
        symlink(&outside, &output_path).expect("configured output symlink creates");
        assert!(matches!(
            stager.freeze_output(&binding, &build_provenance()).await,
            Err(ArtifactError::Configuration(_))
        ));
    }

    /// Open descriptor reads remain pinned to their inode after the source path is replaced.
    #[cfg(unix)]
    #[tokio::test]
    async fn digest_and_snapshot_use_the_already_opened_regular_file() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let source = directory.path().join("source.bin");
        let replacement = directory.path().join("replacement.bin");
        tokio::fs::write(&source, b"original-map")
            .await
            .expect("original fixture writes");
        let mut digest_source = open_regular_file(&source)
            .await
            .expect("original source opens safely");
        tokio::fs::write(&replacement, b"replacement-map")
            .await
            .expect("replacement fixture writes");
        tokio::fs::rename(&replacement, &source)
            .await
            .expect("source path is replaced");

        let (digest, size) = digest_open_file(&mut digest_source, 4, 1024)
            .await
            .expect("open descriptor hashes");
        assert_eq!(
            digest,
            format!("sha256:{:x}", Sha256::digest(b"original-map"))
        );
        assert_eq!(size, 12);

        let snapshot_source = open_regular_file(&source)
            .await
            .expect("replacement source opens safely");
        tokio::fs::write(&replacement, b"latest-map")
            .await
            .expect("latest fixture writes");
        tokio::fs::rename(&replacement, &source)
            .await
            .expect("source path is replaced again");
        let (snapshot_digest, snapshot_size, snapshot_path) =
            snapshot_open_file(snapshot_source, &directory.path().join("prepared"), 4, 1024)
                .await
                .expect("open descriptor snapshots");
        assert_eq!(
            snapshot_digest,
            format!("sha256:{:x}", Sha256::digest(b"replacement-map"))
        );
        assert_eq!(snapshot_size, 15);
        assert_eq!(
            tokio::fs::read(snapshot_path)
                .await
                .expect("snapshot reads"),
            b"replacement-map"
        );
    }

    /// Freezing preserves exact completed bytes and build provenance after the source changes.
    #[tokio::test]
    async fn freezes_output_before_later_publication_can_observe_mutable_source() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let stager = client(directory.path());
        let binding = output_binding();
        let source = stager
            .prepare_output_path(&binding)
            .await
            .expect("output parent prepares");
        tokio::fs::write(&source, b"map-bytes")
            .await
            .expect("producer output writes");

        let prepared = stager
            .freeze_output(&binding, &build_provenance())
            .await
            .expect("output freezes");
        tokio::fs::write(&source, b"changed-after-completion")
            .await
            .expect("mutable source changes");

        assert_eq!(
            tokio::fs::read(&prepared.path)
                .await
                .expect("frozen bytes read"),
            b"map-bytes"
        );
        assert!(
            tokio::fs::metadata(&prepared.path)
                .await
                .expect("frozen metadata reads")
                .permissions()
                .readonly()
        );
        assert_eq!(
            prepared.manifest.source_execution_id(),
            Some("build-execution-a")
        );
        assert_eq!(
            prepared
                .manifest
                .source_task_ref()
                .map(|task| task.task_id().as_str()),
            Some("build-map")
        );
        assert_eq!(prepared.manifest.artifact().byte_size(), 9);
        assert_eq!(prepared.manifest.anchor_id().as_str(), "anchor-lab");
    }

    /// An idempotent Published retry still requires its durable frozen bytes to exist locally.
    #[tokio::test]
    async fn published_retry_rejects_missing_frozen_copy() {
        use tokio::io::AsyncWriteExt;

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("catalog listener binds");
        let endpoint = format!(
            "http://{}",
            listener.local_addr().expect("catalog address reads")
        );
        let directory = tempfile::tempdir().expect("temporary directory");
        let stager = client_at(directory.path(), &endpoint);
        let binding = output_binding();
        let source = stager
            .prepare_output_path(&binding)
            .await
            .expect("output parent prepares");
        tokio::fs::write(&source, b"map-bytes")
            .await
            .expect("producer output writes");
        let prepared = stager
            .freeze_output(&binding, &build_provenance())
            .await
            .expect("output freezes");
        let response_body = serde_json::to_vec(&serde_json::json!({
            "status": "published",
            "manifest": prepared.manifest,
        }))
        .expect("published response serializes");
        let catalog = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.expect("catalog request accepts");
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                response_body.len()
            );
            socket
                .write_all(response.as_bytes())
                .await
                .expect("response headers write");
            socket
                .write_all(&response_body)
                .await
                .expect("response body writes");
        });
        tokio::fs::remove_file(&prepared.path)
            .await
            .expect("frozen copy is removed");

        assert!(matches!(
            stager.publish_prepared(&binding, &prepared).await,
            Err(ArtifactError::ManifestMismatch(_))
        ));
        catalog.abort();
    }

    /// Manifest envelopes retain typed immutable fields and the catalog lifecycle separately.
    #[test]
    fn parses_revision_status_without_losing_manifest_fields() {
        let digest = "a".repeat(64);
        let envelope = ArtifactManifestEnvelope {
            manifest: manifest(&digest),
            status: Some(MapRevisionStatus::Published),
        };
        assert!(envelope.is_published());
        assert_eq!(
            envelope.normalized_digest().expect("digest parses"),
            format!("sha256:{digest}")
        );
    }

    /// A restart retry may change observation time but must retain all durable publication IDs.
    #[test]
    fn publication_attempt_identity_ignores_only_creation_time() {
        let digest = "a".repeat(64);
        let existing = manifest_at(&digest, 1);
        let retried = manifest_at(&digest, 2);
        assert!(is_same_publication_attempt(&existing, &retried));

        let conflicting = manifest_at(&"b".repeat(64), 2);
        assert!(!is_same_publication_attempt(&existing, &conflicting));
    }

    /// Content-addressed cache paths omit the public digest algorithm prefix.
    #[test]
    fn blob_path_strips_digest_prefix() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let stager = client(directory.path());
        let raw = "b".repeat(64);
        let digest = format!("sha256:{raw}");
        let path = stager.blob_path(&digest).expect("digest path");
        assert_eq!(
            path.file_name().and_then(|name| name.to_str()),
            Some(raw.as_str())
        );
        assert_eq!(
            path.parent()
                .and_then(Path::file_name)
                .and_then(|name| name.to_str()),
            Some("bb")
        );
    }
}
