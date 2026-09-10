#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Filesystem content-addressed storage for immutable execution artifacts.
//!
//! This crate implements the transport-neutral `ports::ArtifactBlobStore`
//! contract. It stores opaque bytes and leaves map, execution, and ownership
//! policy to their respective architecture layers.

mod digest;
mod path_validation;
mod port_adapter;
mod store;
mod types;
mod upload;

pub use digest::{digest_bytes, digest_reader, normalize_digest};
pub use types::{ArtifactStoreError, ArtifactUpload, FileSystemArtifactStore, StoredArtifact};

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
