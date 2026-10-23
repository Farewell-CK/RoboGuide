#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Filesystem content-addressed storage for immutable execution artifacts.
//!
//! This crate implements the transport-neutral `ports::ArtifactBlobStore`
//! contract. It stores opaque bytes and leaves map, execution, and ownership
//! policy to their respective architecture layers.

mod digest;
mod layout;
mod path_validation;
mod port_adapter;
mod prepared_source;
mod readonly;
mod store;
mod types;
mod upload;

pub use digest::{digest_bytes, digest_reader, normalize_digest};
pub use layout::{
    ARTIFACT_ARCHIVE_LAYOUT_SCHEMA, ARTIFACT_GC_LAYOUT_SCHEMA, ARTIFACT_LAYOUT_SCHEMA,
    ARTIFACT_OWNER_FILE, ARTIFACT_REFERENCE_LAYOUT_SCHEMA, RETAINED_BLOB_DIRECTORY,
};
pub use prepared_source::{ArtifactReplicationSource, PreparedArtifactSourceGuard};
pub use readonly::ArtifactReadOnlyStore;
pub use types::{
    ArtifactStoreError, ArtifactUpload, ArtifactWriterIdentity, FileSystemArtifactStore,
    StoredArtifact,
};

#[cfg(test)]
#[path = "tests.rs"]
mod tests;

#[cfg(test)]
#[path = "layout_tests.rs"]
mod layout_tests;
