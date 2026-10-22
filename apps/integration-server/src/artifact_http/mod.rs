//! Independent streaming HTTP data plane for immutable Spatial Memory artifacts.
//!
//! This module owns HTTP parsing and transport only.  The Artifact Store owns bytes, while the
//! `MapCatalogProjection` owns rebuildable manifest/replica metadata.  No endpoint starts a
//! mission, mutates a TaskExecution, or selects an active map.

use artifact_store::{ArtifactStoreError as CasError, ArtifactUpload, FileSystemArtifactStore};
use domain::{
    EventPayload, MapArtifactManifest, MapRevisionSelector, MemoryArtifactManifest, MemoryId,
    MemoryOwner, MemoryRevisionId, MemorySelector, MissionId, NodeId, SpatialAnchorId, TimestampMs,
};
use ports::{
    EventSink, MapCatalogReader, MapCatalogWriter, MemoryCatalogReader, MemoryCatalogWriter,
};
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use serde::Deserialize;
use sha2::{Digest as _, Sha256};
use state::{MapCatalogProjection, MemoryCatalogProjection, PersistedCheckpoint, SqliteEventLog};
use std::collections::BTreeMap;
use std::io::BufReader;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
#[cfg(test)]
use tokio::net::TcpStream;
use tokio::time::{MissedTickBehavior, interval, timeout};
use tokio_rustls::TlsAcceptor;

/// Maximum HTTP header block accepted by the artifact listener.
const MAX_HEADER_BYTES: usize = 64 * 1024;
/// Maximum JSON request body accepted by catalog and upload-control endpoints.
const MAX_JSON_BODY_BYTES: u64 = 1024 * 1024;
/// Maximum artifact body accepted by this v0 listener.
const MAX_ARTIFACT_BYTES: u64 = 4 * 1024 * 1024 * 1024;
/// Maximum number of upload identities, including requests currently streaming or finalizing.
const MAX_ACTIVE_UPLOADS: usize = 32;
/// Maximum aggregate staged and reserved bytes across all active upload identities.
const MAX_ACTIVE_UPLOAD_BYTES: u64 = 8 * 1024 * 1024 * 1024;
/// Maximum idle time between successful mutations of one staged upload.
const UPLOAD_IDLE_TTL: Duration = Duration::from_secs(15 * 60);
/// Maximum wall time allowed for one declared artifact request body to arrive.
const UPLOAD_BODY_TIMEOUT: Duration = Duration::from_secs(5 * 60);
/// Bounded body read size; one authority recheck covers each accepted write window.
const UPLOAD_STREAM_CHUNK_BYTES: usize = 4 * 1024 * 1024;
/// Frequency at which idle sessions are removed even when no HTTP request arrives.
const UPLOAD_SWEEP_INTERVAL: Duration = Duration::from_secs(60);

mod authorization;
mod catalog;
pub use authorization::ArtifactPrincipal;
use authorization::*;
mod access;
mod adoption;
mod authenticated;
mod protocol;
mod retention;
pub use retention::ArtifactPublicationSource;
mod server;
mod upload_authority;
mod upload_registry;
mod upload_retirement;
pub(crate) use access::map_authorization_manifest;
use access::*;

pub use catalog::{
    ArtifactCatalog, LocalizationEvidenceObserver, MemoryProviderAdmission,
    MemoryPublicationIdentity,
};
use protocol::*;
pub use server::configured_artifact_tls;
pub use server::serve_artifact_http;
use upload_registry::*;

#[cfg(test)]
mod tests;
