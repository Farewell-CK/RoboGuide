//! Explicit Artifact backend identity, crash-consistent directory migration and old-writer fence.

use crate::ArtifactStoreError;
use crate::path_validation::{
    add_no_follow_flag, create_directory, io_error, open_regular_file, sync_directory,
    unsafe_path_error, validate_directory_tree,
};
use crate::store::BLOB_DIRECTORY;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::Path;

/// Explicit physical layout contract; this is software storage identity, never a read grant.
pub const ARTIFACT_LAYOUT_SCHEMA: &str = "roboguide.artifact-storage/v1";
/// Explicit format whose mandatory original maintenance journal fences v1 writers.
pub const ARTIFACT_REFERENCE_LAYOUT_SCHEMA: &str = "roboguide.artifact-storage/v2";
/// Explicit format retaining the inventory of original Controller reference authorities.
pub const ARTIFACT_ARCHIVE_LAYOUT_SCHEMA: &str = "roboguide.artifact-storage/v3";
/// Immutable original backend metadata required by retained-layout recovery profiles.
pub const ARTIFACT_OWNER_FILE: &str = ".retention-owner.json";
/// Finalized content tree whose old path is a regular-file format fence.
pub const RETAINED_BLOB_DIRECTORY: &str = "objects-v1";
/// Original persistent intent retained until the directory and old-writer fence are durable.
const INTENT_FILE: &str = ".retention-intent.json";
/// Maximum complete owner metadata accepted before allocation or deserialization.
const MAX_OWNER_BYTES: u64 = 4096;

mod authorities;
mod references;
pub(crate) use authorities::{bind_authority, open_authorities};
pub(crate) use references::open_references;

/// Inspects an already accepted retained root under a shared lease without initialization or repair.
pub(crate) fn inspect_retained(root: &Path) -> Result<(String, &'static str), ArtifactStoreError> {
    validate_directory_tree(root, false, "inspect retained Artifact root")?;
    if exists(&root.join(INTENT_FILE))?
        || exists(&root.join(references::UPGRADE_INTENT))?
        || exists(&root.join(authorities::UPGRADE_INTENT))?
    {
        return Err(unsafe_path_error(
            "inspect retained Artifact root",
            "original upgrade is incomplete",
        ));
    }
    let value: serde_json::Value = {
        let mut bytes = Vec::new();
        open_regular_file(
            &root.join(ARTIFACT_OWNER_FILE),
            "inspect retained Artifact owner",
        )?
        .take(MAX_OWNER_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("read retained Artifact owner", error))?;
        if bytes.len() as u64 > MAX_OWNER_BYTES {
            return Err(unsafe_path_error(
                "inspect retained Artifact owner",
                "owner exceeds bound",
            ));
        }
        serde_json::from_slice(&bytes)
            .map_err(|error| io_error("parse retained Artifact owner", io::Error::other(error)))?
    };
    let schema = match value.get("schema").and_then(serde_json::Value::as_str) {
        Some(ARTIFACT_LAYOUT_SCHEMA) => ARTIFACT_LAYOUT_SCHEMA,
        Some(ARTIFACT_REFERENCE_LAYOUT_SCHEMA) => ARTIFACT_REFERENCE_LAYOUT_SCHEMA,
        Some(ARTIFACT_ARCHIVE_LAYOUT_SCHEMA) => ARTIFACT_ARCHIVE_LAYOUT_SCHEMA,
        _ => {
            return Err(unsafe_path_error(
                "inspect retained Artifact owner",
                "original schema is unsupported",
            ));
        }
    };
    let owner = read_owner_for(&root.join(ARTIFACT_OWNER_FILE), schema)?;
    if read_owner_for(&root.join(BLOB_DIRECTORY), schema)? != owner {
        return Err(unsafe_path_error(
            "inspect retained Artifact owner",
            "original fence conflicts",
        ));
    }
    if matches!(
        schema,
        ARTIFACT_REFERENCE_LAYOUT_SCHEMA | ARTIFACT_ARCHIVE_LAYOUT_SCHEMA
    ) {
        references::validate_journal(root, &owner)?;
    }
    if schema == ARTIFACT_ARCHIVE_LAYOUT_SCHEMA {
        authorities::validate(root, &owner)?;
    }
    validate_directory_tree(
        &root.join(RETAINED_BLOB_DIRECTORY),
        false,
        "inspect retained Artifact bytes",
    )?;
    Ok((owner.owner_id, schema))
}

/// Frozen local directory-rename evidence, not a claim about a restored filesystem inode.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Owner {
    /// Exact layout format, never inferred from a caller-provided root name.
    schema: String,
    /// Original OS-random software backend identity.
    owner_id: String,
    /// Actual original blobs directory's filesystem device during migration.
    source_device: u64,
    /// Actual original blobs directory inode during migration.
    source_inode: u64,
}

/// Selected managed layout; its caller retains the original root writer lease.
pub(crate) struct OpenLayout {
    /// Finalized blob directory inside the original root.
    pub(crate) blob_directory: &'static str,
    /// Original persistent identity, absent for unchanged legacy storage.
    pub(crate) owner_id: Option<String>,
}

/// Checks existence without interpreting a symlink, including a dangling one, as absence.
fn exists(path: &Path) -> Result<bool, ArtifactStoreError> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(io_error("inspect artifact format path", error)),
    }
}

/// Parses one exact bounded regular-file record without following a mutable symlink leaf.
fn read_owner(path: &Path) -> Result<Owner, ArtifactStoreError> {
    read_owner_for(path, ARTIFACT_LAYOUT_SCHEMA)
}

/// Parses only the explicitly selected original layout, never upgrading an unknown stored identity.
fn read_owner_for(path: &Path, schema: &str) -> Result<Owner, ArtifactStoreError> {
    let mut file = open_regular_file(path, "read artifact owner")?;
    if file
        .metadata()
        .map_err(|error| io_error("inspect artifact owner", error))?
        .len()
        > MAX_OWNER_BYTES
    {
        return Err(unsafe_path_error(
            "read artifact owner",
            "owner metadata exceeds its bound",
        ));
    }
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_OWNER_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("read artifact owner", error))?;
    if bytes.len() as u64 > MAX_OWNER_BYTES {
        return Err(unsafe_path_error(
            "read artifact owner",
            "owner metadata exceeds its bound",
        ));
    }
    let owner: Owner = serde_json::from_slice(&bytes)
        .map_err(|error| io_error("decode artifact owner", io::Error::other(error)))?;
    let nonce = owner.owner_id.strip_prefix("artifact-store:").unwrap_or("");
    if owner.schema != schema
        || nonce.len() != 64
        || !nonce
            .bytes()
            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
        || owner.source_inode == 0
    {
        return Err(unsafe_path_error(
            "read artifact owner",
            "original owner identity is invalid",
        ));
    }
    Ok(owner)
}

/// Publishes one original immutable record or verifies its exact existing identity on retry.
fn publish_owner(root: &Path, name: &str, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let path = root.join(name);
    if exists(&path)? {
        if read_owner(&path)? != *owner {
            return Err(unsafe_path_error(
                "publish artifact owner",
                "original owner conflicts",
            ));
        }
        let file = open_regular_file(&path, "resync artifact owner")?;
        file.sync_all()
            .map_err(|error| io_error("resync artifact owner", error))?;
    } else {
        let bytes = serde_json::to_vec(owner)
            .map_err(|error| io_error("encode artifact owner", io::Error::other(error)))?;
        let mut options = OpenOptions::new();
        options.create_new(true).write(true);
        add_no_follow_flag(&mut options);
        let mut file = options
            .open(&path)
            .map_err(|error| io_error("publish artifact owner", error))?;
        file.write_all(&bytes)
            .map_err(|error| io_error("write artifact owner", error))?;
        file.sync_all()
            .map_err(|error| io_error("sync artifact owner", error))?;
    }
    sync_directory(root, "sync artifact format directory")
}

/// Reads actual original POSIX directory identity before permitting the immutable rename.
#[cfg(unix)]
fn directory_identity(path: &Path) -> Result<(u64, u64), ArtifactStoreError> {
    use std::os::unix::fs::MetadataExt;
    validate_directory_tree(path, false, "validate artifact migration directory")?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| io_error("inspect artifact migration directory", error))?;
    Ok((metadata.dev(), metadata.ino()))
}

/// Refuses unsupported platforms explicitly; legacy opaque storage keeps its original contract.
#[cfg(not(unix))]
fn directory_identity(_path: &Path) -> Result<(u64, u64), ArtifactStoreError> {
    Err(unsafe_path_error(
        "artifact retained layout",
        "original directory evidence requires POSIX",
    ))
}

/// Creates the original nonce before any rename; it never stands for physical or Node identity.
fn original_owner(path: &Path) -> Result<Owner, ArtifactStoreError> {
    let (source_device, source_inode) = directory_identity(path)?;
    let mut nonce = [0_u8; 32];
    getrandom::fill(&mut nonce).map_err(|error| {
        io_error(
            "create artifact backend identity",
            io::Error::other(error.to_string()),
        )
    })?;
    let encoded: String = nonce.iter().map(|value| format!("{value:02x}")).collect();
    Ok(Owner {
        schema: ARTIFACT_LAYOUT_SCHEMA.into(),
        owner_id: format!("artifact-store:{encoded}"),
        source_device,
        source_inode,
    })
}

/// Selects an explicit format under the caller's root writer, preserving every original nonce.
pub(crate) fn open(root: &Path, retained: bool) -> Result<OpenLayout, ArtifactStoreError> {
    let owner_path = root.join(ARTIFACT_OWNER_FILE);
    let intent_path = root.join(INTENT_FILE);
    let old = root.join(BLOB_DIRECTORY);
    let objects = root.join(RETAINED_BLOB_DIRECTORY);
    if exists(&root.join(references::UPGRADE_INTENT))?
        || exists(&root.join(references::JOURNAL_DIRECTORY))?
    {
        return Err(unsafe_path_error(
            "open artifact layout",
            "original maintenance journal requires explicit Artifact storage v2",
        ));
    }
    if !retained {
        if exists(&owner_path)? || exists(&intent_path)? || exists(&objects)? {
            return Err(unsafe_path_error(
                "open artifact layout",
                "retained layout requires explicit configuration",
            ));
        }
        create_directory(&old, "create artifact blob directory")?;
        return Ok(OpenLayout {
            blob_directory: BLOB_DIRECTORY,
            owner_id: None,
        });
    }
    if exists(&owner_path)? {
        if exists(&intent_path)? {
            return Err(unsafe_path_error(
                "open artifact layout",
                "published owner has conflicting pending intent",
            ));
        }
        let owner = read_owner(&owner_path)?;
        if read_owner(&old)? != owner {
            return Err(unsafe_path_error(
                "open artifact layout",
                "original format fence conflicts",
            ));
        }
        validate_directory_tree(&objects, false, "validate retained artifact directory")?;
        return Ok(OpenLayout {
            blob_directory: RETAINED_BLOB_DIRECTORY,
            owner_id: Some(owner.owner_id),
        });
    }
    let owner = if exists(&intent_path)? {
        read_owner(&intent_path)?
    } else {
        if exists(&objects)? {
            return Err(unsafe_path_error(
                "migrate artifact layout",
                "original owner intent is missing",
            ));
        }
        create_directory(&old, "create original artifact blob directory")?;
        let owner = original_owner(&old)?;
        publish_owner(root, INTENT_FILE, &owner)?;
        owner
    };
    if exists(&objects)? {
        if directory_identity(&objects)? != (owner.source_device, owner.source_inode) {
            return Err(unsafe_path_error(
                "migrate artifact layout",
                "original moved directory differs",
            ));
        }
    } else {
        if directory_identity(&old)? != (owner.source_device, owner.source_inode) {
            return Err(unsafe_path_error(
                "migrate artifact layout",
                "original source directory differs",
            ));
        }
        fs::rename(&old, &objects)
            .map_err(|error| io_error("rename original artifact content", error))?;
        sync_directory(root, "sync migrated artifact directory")?;
    }
    publish_owner(root, BLOB_DIRECTORY, &owner)?;
    fs::rename(&intent_path, &owner_path)
        .map_err(|error| io_error("publish original artifact owner", error))?;
    sync_directory(root, "sync original artifact owner")?;
    Ok(OpenLayout {
        blob_directory: RETAINED_BLOB_DIRECTORY,
        owner_id: Some(owner.owner_id),
    })
}
