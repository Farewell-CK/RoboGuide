//! Transport-neutral ports for immutable Spatial Memory artifacts and catalog evidence.
//!
//! The blob interfaces deliberately expose bounded chunks rather than whole-artifact byte
//! vectors.  Implementations may be synchronous or adapt these methods behind an asynchronous
//! transport boundary; the core domain remains independent of HTTP, gRPC, or a filesystem.

use domain::{
    ContentDigest, EventPayload, EventRecord, MapReplicaSnapshot, MapRevisionSelector,
    MapRevisionSnapshot,
};
use std::fmt::{Display, Formatter};

/// Failures exposed by a content-addressed artifact store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ArtifactStoreError {
    /// The caller supplied an empty or otherwise invalid upload identity.
    InvalidUploadId,
    /// The requested digest is not present in the store.
    NotFound(ContentDigest),
    /// Finalization found bytes whose digest differs from the declared digest.
    DigestMismatch {
        /// Digest declared by the caller.
        expected: ContentDigest,
        /// Digest computed by the store.
        actual: ContentDigest,
    },
    /// Finalization found a byte count different from the declared size.
    SizeMismatch {
        /// Byte count declared by the caller.
        expected: u64,
        /// Byte count observed by the store.
        actual: u64,
    },
    /// A manifest or digest conflicts with an immutable object already present.
    Conflict(String),
    /// The backing store could not complete an operation.
    Backend(String),
}

impl Display for ArtifactStoreError {
    /// Formats a transport-neutral artifact-store failure.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidUploadId => formatter.write_str("artifact upload id is invalid"),
            Self::NotFound(digest) => write!(formatter, "artifact {digest} was not found"),
            Self::DigestMismatch { expected, actual } => {
                write!(
                    formatter,
                    "artifact digest mismatch: expected {expected}, got {actual}"
                )
            }
            Self::SizeMismatch { expected, actual } => {
                write!(
                    formatter,
                    "artifact size mismatch: expected {expected}, got {actual}"
                )
            }
            Self::Conflict(reason) => write!(formatter, "artifact conflict: {reason}"),
            Self::Backend(reason) => write!(formatter, "artifact store backend failure: {reason}"),
        }
    }
}

impl std::error::Error for ArtifactStoreError {}

/// Bounded-chunk writer for one temporary artifact upload.
pub trait ArtifactBlobWriter: Send {
    /// Appends one bounded chunk to the temporary upload.
    fn write_chunk(&mut self, chunk: &[u8]) -> Result<(), ArtifactStoreError>;

    /// Verifies and atomically publishes the temporary upload under its content digest.
    fn finalize(
        &mut self,
        expected_digest: &ContentDigest,
        expected_size: u64,
    ) -> Result<(), ArtifactStoreError>;

    /// Aborts the temporary upload and releases its staging resources.
    fn abort(&mut self) -> Result<(), ArtifactStoreError>;
}

/// Bounded-chunk reader for one immutable artifact.
pub trait ArtifactBlobReader: Send {
    /// Returns the exact byte size of the immutable artifact.
    fn content_length(&self) -> u64;

    /// Reads the next chunk into a caller-owned bounded buffer.
    fn read_chunk(&mut self, buffer: &mut [u8]) -> Result<usize, ArtifactStoreError>;
}

/// Content-addressed blob storage without a transport or filesystem assumption.
pub trait ArtifactBlobStore: Send {
    /// Begins a temporary upload identified by a caller-generated operation id.
    fn begin_upload(
        &mut self,
        upload_id: &str,
    ) -> Result<Box<dyn ArtifactBlobWriter>, ArtifactStoreError>;

    /// Opens an immutable artifact by its SHA-256 content digest.
    fn open_blob(
        &self,
        digest: &ContentDigest,
    ) -> Result<Box<dyn ArtifactBlobReader>, ArtifactStoreError>;
}

/// Failures raised while applying immutable map catalog evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MapCatalogError {
    /// A revision event referenced a different immutable manifest for the same selector.
    RevisionConflict(String),
    /// A replica event referenced a revision that has not been declared or published.
    UnknownRevision(MapRevisionSelector),
    /// An event attempted to move a replica backwards or after rejection.
    InvalidReplicaTransition(String),
    /// An event payload was unrelated to Spatial Memory and cannot be projected here.
    UnsupportedEvent,
}

impl Display for MapCatalogError {
    /// Formats a deterministic catalog projection failure.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::RevisionConflict(reason) => write!(formatter, "map revision conflict: {reason}"),
            Self::UnknownRevision(selector) => write!(formatter, "unknown map revision {selector}"),
            Self::InvalidReplicaTransition(reason) => {
                write!(formatter, "invalid map replica transition: {reason}")
            }
            Self::UnsupportedEvent => formatter.write_str("event is not Spatial Memory evidence"),
        }
    }
}

impl std::error::Error for MapCatalogError {}

/// Read-only access to the rebuildable Spatial Memory catalog projection.
pub trait MapCatalogReader {
    /// Returns one revision snapshot by logical map/revision selector.
    fn revision(&self, selector: &MapRevisionSelector) -> Option<MapRevisionSnapshot>;

    /// Returns all known node replicas for one revision in deterministic node order.
    fn replicas(&self, selector: &MapRevisionSelector) -> Vec<MapReplicaSnapshot>;

    /// Returns all known revisions in deterministic map/revision order.
    fn revisions(&self) -> Vec<MapRevisionSnapshot>;
}

/// Event-sourced write boundary for the Spatial Memory catalog projection.
pub trait MapCatalogWriter {
    /// Applies one immutable event and rejects conflicting or invalid transitions.
    fn apply_event(&mut self, event: &EventRecord) -> Result<(), MapCatalogError>;

    /// Applies a payload with an explicit event timestamp when an envelope is not available.
    fn apply_payload(
        &mut self,
        timestamp: domain::TimestampMs,
        payload: &EventPayload,
    ) -> Result<(), MapCatalogError>;
}
