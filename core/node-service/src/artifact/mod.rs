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

mod client;
mod filesystem;
mod staging;

use filesystem::*;
pub use staging::ArtifactStager;

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

#[cfg(test)]
#[path = "../artifact_tests.rs"]
mod tests;
