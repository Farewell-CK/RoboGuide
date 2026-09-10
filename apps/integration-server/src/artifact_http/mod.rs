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
use serde::Deserialize;
use state::{MapCatalogProjection, MemoryCatalogProjection, PersistedCheckpoint, SqliteEventLog};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::time::{MissedTickBehavior, interval, timeout};

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
/// Frequency at which idle sessions are removed even when no HTTP request arrives.
const UPLOAD_SWEEP_INTERVAL: Duration = Duration::from_secs(60);

mod catalog;
mod protocol;
mod server;
mod upload_registry;

pub use catalog::{
    ArtifactCatalog, LocalizationEvidenceObserver, MemoryProviderAdmission,
    MemoryPublicationIdentity,
};
use protocol::*;
pub use server::serve_artifact_http;
use upload_registry::*;

#[cfg(test)]
mod tests;
