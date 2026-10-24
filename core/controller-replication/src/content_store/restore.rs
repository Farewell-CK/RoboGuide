//! Explicit restored physical backing, separate from immutable original operational identities.

use super::*;
use sha2::{Digest as _, Sha256};
use std::io::Read;
use std::os::unix::fs::MetadataExt;

/// Mandatory exact current binding file; copying original headers alone never grants open authority.
pub(super) const FILE: &str = ".restore-binding.json";
/// Bounded immutable restore chain, independent of original operation inventory limits.
const MAX_RESTORES: usize = 64;
/// Maximum one complete closed binding; the full bounded history remains below one MiB.
const MAX_BINDING_BYTES: u64 = 16 * 1024;

/// One actual restored directory/database pair, not an original process or device receipt.
#[derive(Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Backing {
    /// Actual destination directory device.
    root_device: u64,
    /// Actual destination directory inode, stable across private-tree publication rename.
    root_inode: u64,
    /// Actual destination SQLite device.
    database_device: u64,
    /// Actual destination SQLite inode, never replaced after binding publication.
    database_inode: u64,
    /// Actual paired destination Raft SQL device, checked through its held writer descriptor.
    parent_device: u64,
    /// Actual paired destination Raft SQL inode; historical producers never borrow another parent.
    parent_inode: u64,
}

/// Pinned original source and explicit new backing, without rewriting original byte/process evidence.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Binding {
    /// Closed independent local restore interpretation.
    schema: String,
    /// Original operator restore command; retries cannot create another binding.
    command_id: String,
    /// Exact independently pinned source bundle manifest.
    manifest_digest: String,
    /// Exact original bundled SQL file digest before adding this new binding.
    source_database_digest: String,
    /// Original copied storage generation, never inferred from missing journals.
    source_root_version: u64,
    /// Actual paired source parent SQL device retained as original provenance.
    source_parent_device: u64,
    /// Actual paired source parent SQL inode before explicit complete-bundle relocation.
    source_parent_inode: u64,
    /// Original logical receiver identity retained in every historical operation.
    original_identity: ReplicaContentIdentity,
    /// Consecutive original restore sequence, bounded independently of operation history.
    ordinal: usize,
    /// Exact raw previous binding digest; absent only for a genuine original Root4 source.
    previous_binding_digest: Option<String>,
    /// Independently observed actual destination inode pair.
    backing: Backing,
}

/// Hashes exact retained binding bytes; JSON serialization in another language cannot alter identity.
fn digest(raw: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(raw.as_bytes()))
}

/// Freezes this new worker's current physical backing without rewriting logical receiver provenance.
pub(super) fn stream_backing(inner: &ContentInner) -> Result<(u64, u64, u64, u64), String> {
    if let Some(expected) = &inner.restore_binding {
        let raw = validate_backing(&inner.root, &inner.identity, expected)?;
        let binding: Binding = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
        Ok((
            binding.backing.root_device,
            binding.backing.root_inode,
            binding.backing.database_device,
            binding.backing.database_inode,
        ))
    } else {
        let original = &inner.identity;
        Ok((
            original.root_device,
            original.root_inode,
            original.database_device,
            original.database_inode,
        ))
    }
}

/// Requires a full explicit SHA-256 identity rather than a caller's readiness boolean.
fn checksum(value: &str) -> bool {
    value
        .strip_prefix("sha256:")
        .is_some_and(crate::content::hex_digest)
}

/// Parses a bounded closed binding and verifies original immutable source/sequence semantics.
fn parse(raw: &str, original: &ReplicaContentIdentity, ordinal: usize) -> Result<Binding, String> {
    if raw.len() as u64 > MAX_BINDING_BYTES {
        return Err("original restore binding exceeds its byte bound".into());
    }
    let binding: Binding = serde_json::from_str(raw).map_err(|e| e.to_string())?;
    if !matches!((binding.schema.as_str(),binding.source_root_version),
        ("roboguide.replica-content-restore-binding/v0.1",4) if ordinal==1)
        && !matches!((binding.schema.as_str(),binding.source_root_version),
            ("roboguide.replica-content-restore-binding/v0.1",5) if ordinal>1)
        && !matches!(
            (binding.schema.as_str(), binding.source_root_version),
            ("roboguide.replica-content-restore-binding/v0.2", 6)
        )
        && !matches!(
            (binding.schema.as_str(), binding.source_root_version),
            ("roboguide.replica-content-restore-binding/v0.3", 7)
        )
        && !matches!(
            (binding.schema.as_str(), binding.source_root_version),
            ("roboguide.replica-content-restore-binding/v0.4", 8)
        )
    {
        return Err(
            "original restore binding has an unsupported explicit source generation".into(),
        );
    }
    if binding.command_id.is_empty()
        || binding.command_id.len() > 128
        || !checksum(&binding.manifest_digest)
        || !checksum(&binding.source_database_digest)
        || binding.original_identity != *original
        || binding.ordinal != ordinal
        || !(1..=MAX_RESTORES).contains(&ordinal)
        || (ordinal == 1) != binding.previous_binding_digest.is_none()
        || binding
            .previous_binding_digest
            .as_deref()
            .is_some_and(|v| !checksum(v))
        || binding.backing.root_inode == 0
        || binding.backing.database_inode == 0
        || binding.backing.parent_inode == 0
        || binding.source_parent_inode == 0
    {
        return Err("original restore binding source/identity/sequence differs".into());
    }
    Ok(binding)
}

/// Checks actual current named backing and the exact configured binding before any Root5 use.
pub(super) fn validate_backing(
    root: &Path,
    original: &ReplicaContentIdentity,
    expected: &str,
) -> Result<String, String> {
    let file = layout::regular(&root.join(FILE), false)?;
    if file.metadata().map_err(|e| e.to_string())?.len() > MAX_BINDING_BYTES {
        return Err("current restore binding exceeds bound".into());
    }
    let mut raw = String::new();
    file.take(MAX_BINDING_BYTES + 1)
        .read_to_string(&mut raw)
        .map_err(|e| e.to_string())?;
    if !checksum(expected) || digest(&raw) != expected {
        return Err("current restore binding differs from explicitly configured digest".into());
    }
    let untrusted: Binding = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    let binding = parse(&raw, original, untrusted.ordinal)?;
    let directory = std::fs::symlink_metadata(root).map_err(|e| e.to_string())?;
    let database = layout::regular(&root.join("content.sqlite3"), false)?
        .metadata()
        .map_err(|e| e.to_string())?;
    if !directory.is_dir()
        || (binding.backing.root_device, binding.backing.root_inode)
            != (directory.dev(), directory.ino())
        || (
            binding.backing.database_device,
            binding.backing.database_inode,
        ) != (database.dev(), database.ino())
    {
        return Err("restored content actual backing inode differs from original binding".into());
    }
    Ok(raw)
}

/// Verifies complete immutable prior bindings and their exact mandatory current SQL head.
pub(super) fn validate_sql(
    connection: &Connection,
    original: &ReplicaContentIdentity,
) -> Result<String, String> {
    let raw: String = connection
        .query_row(
            "SELECT value FROM metadata WHERE key='restore_bindings'",
            [],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    if raw.len() > MAX_RESTORES * MAX_BINDING_BYTES as usize + 32 * 1024 {
        return Err("original restore history exceeds fixed bound".into());
    }
    let history: Vec<String> = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    if history.is_empty() || history.len() > MAX_RESTORES {
        return Err("original restore history is absent or exceeds fixed bound".into());
    }
    let mut previous = None;
    let mut previous_parent = None;
    let mut snapshot_history = 0;
    for (index, raw) in history.iter().enumerate() {
        let binding = parse(raw, original, index + 1)?;
        if snapshot_history > binding.source_root_version {
            return Err(
                "original Snapshot restore history regressed its backing generation".into(),
            );
        }
        if binding.source_root_version >= 6 {
            snapshot_history = binding.source_root_version;
        }
        if binding.previous_binding_digest != previous {
            return Err("original restore history chain differs".into());
        }
        if previous_parent.is_some_and(|expected| {
            expected != (binding.source_parent_device, binding.source_parent_inode)
        }) {
            return Err("original restore history changed its paired parent chain".into());
        }
        previous_parent = Some((binding.backing.parent_device, binding.backing.parent_inode));
        previous = Some(digest(raw));
    }
    history
        .last()
        .cloned()
        .ok_or("original restore head unavailable".into())
}

/// Maps historical producer provenance only through the complete pinned chain to the actual parent.
pub(super) fn matches_parent(
    inner: &ContentInner,
    connection: &Connection,
    actual: (u64, u64),
    original: (u64, u64),
) -> Result<bool, String> {
    Ok(parent_sources(inner, connection, actual)?.contains(&original))
}

/// Reads one complete immutable parent chain per bounded inventory scan, never once per producer row.
pub(super) fn parent_sources(
    inner: &ContentInner,
    connection: &Connection,
    actual: (u64, u64),
) -> Result<std::collections::BTreeSet<(u64, u64)>, String> {
    if inner.restore_binding.is_none() {
        return Ok(std::collections::BTreeSet::from([actual]));
    }
    let raw = validate_sql(connection, &inner.identity)?;
    let head: Binding = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    if actual != (head.backing.parent_device, head.backing.parent_inode) {
        return Err("restored content is attached to another actual parent inode".into());
    }
    let raw: String = connection
        .query_row(
            "SELECT value FROM metadata WHERE key='restore_bindings'",
            [],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    let history: Vec<String> = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    let mut sources = std::collections::BTreeSet::from([actual]);
    for raw in history {
        let binding: Binding = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
        sources.insert((binding.source_parent_device, binding.source_parent_inode));
    }
    Ok(sources)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Copies actual quiescent test files without preserving their original physical inode numbers.
    fn copy_tree(source: &Path, target: &Path) {
        std::fs::create_dir(target).unwrap();
        for entry in std::fs::read_dir(source).unwrap() {
            let entry = entry.unwrap();
            let next = target.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() {
                copy_tree(&entry.path(), &next);
            } else {
                std::fs::copy(entry.path(), next).unwrap();
            }
        }
    }

    /// Explicit binding admits new backing without changing original Unknown, byte or receiver history.
    #[test]
    fn restored_backing_preserves_original_history_and_refuses_implicit_or_foreign_open() {
        let directory = tempfile::tempdir().unwrap();
        let profile = "a".repeat(64);
        let source_path = directory.path().join("source");
        std::fs::create_dir(&source_path).unwrap();
        let (_log, source, key) = crate::content_application_tests::original(&source_path);
        let original_path = directory.path().join("original");
        let original =
            ReplicaContentStore::create_with_streams(&original_path, &profile, 1).unwrap();
        original
            .copy_from_artifact(&source, &key, "original-copy", &"b".repeat(64), 30000)
            .unwrap();
        drop(
            original
                .open_reader(&key, "original-unknown-reader", &"c".repeat(64), 30000)
                .unwrap(),
        );
        let before = original
            .reader_status("original-unknown-reader")
            .unwrap()
            .unwrap();
        assert!(before.receipt.is_none());
        let identity = original.identity().clone();
        drop(original);
        let target = directory.path().join("restored");
        copy_tree(&original_path, &target);
        let root = std::fs::metadata(&target).unwrap();
        let sql = std::fs::metadata(target.join("content.sqlite3")).unwrap();
        let parent = directory.path().join("explicit-parent-fixture");
        std::fs::write(&parent, b"contract-only original parent fixture").unwrap();
        let parent = std::fs::metadata(parent).unwrap();
        let binding = Binding {
            schema: "roboguide.replica-content-restore-binding/v0.1".into(),
            command_id: "contract-restoration".into(),
            manifest_digest: format!("sha256:{}", "d".repeat(64)),
            source_database_digest: format!(
                "sha256:{:x}",
                Sha256::digest(std::fs::read(original_path.join("content.sqlite3")).unwrap())
            ),
            source_root_version: 4,
            source_parent_device: parent.dev(),
            source_parent_inode: parent.ino(),
            original_identity: identity.clone(),
            ordinal: 1,
            previous_binding_digest: None,
            backing: Backing {
                root_device: root.dev(),
                root_inode: root.ino(),
                database_device: sql.dev(),
                database_inode: sql.ino(),
                parent_device: parent.dev(),
                parent_inode: parent.ino(),
            },
        };
        let raw = serde_json::to_string(&binding).unwrap();
        let expected = digest(&raw);
        let connection = Connection::open(target.join("content.sqlite3")).unwrap();
        connection
            .execute(
                "UPDATE metadata SET value=? WHERE key='layout'",
                [REPLICA_CONTENT_RESTORE_LAYOUT_SCHEMA],
            )
            .unwrap();
        connection
            .execute(
                "INSERT INTO metadata VALUES ('restore_bindings',?)",
                [serde_json::to_string(&vec![&raw]).unwrap()],
            )
            .unwrap();
        connection.execute_batch("PRAGMA user_version=5").unwrap();
        drop(connection);
        std::fs::write(target.join(FILE), &raw).unwrap();
        assert!(ReplicaContentStore::open_with_streams(&target, &profile, 1).is_err());
        assert!(
            ReplicaContentStore::open_restored_streams(
                &target,
                &profile,
                1,
                &format!("sha256:{}", "e".repeat(64))
            )
            .is_err()
        );
        let restored =
            ReplicaContentStore::open_restored_streams(&target, &profile, 1, &expected).unwrap();
        assert_eq!(restored.identity(), &identity);
        assert_ne!(identity.root_inode, root.ino());
        assert_eq!(
            restored
                .reader_status("original-unknown-reader")
                .unwrap()
                .unwrap(),
            before
        );
        restored.verify_content(&key).unwrap();
        let stream_target = crate::ContentQuorumTarget {
            profile_identity: profile.clone(),
            epoch: crate::ControllerEpoch {
                term: 1,
                replica_id: 1,
            },
            revision: 1,
            checkpoint_digest: "a".repeat(64),
            batch_digest: "b".repeat(64),
            membership: openraft::StoredMembership::new(
                Some(openraft::LogId::new(
                    openraft::CommittedLeaderId::new(1, 1),
                    1,
                )),
                openraft::Membership::new(
                    vec![std::collections::BTreeSet::from([1])],
                    std::collections::BTreeMap::from([(
                        1,
                        crate::ReplicaMember::with_content_store(
                            "127.0.0.1:1",
                            identity.store_owner.clone(),
                        )
                        .unwrap(),
                    )]),
                ),
            ),
            member_stores: std::collections::BTreeMap::from([(1, identity.store_owner.clone())]),
            inputs: vec![key.clone()],
        };
        let new_stream = restored
            .copy_artifact_stream(
                &source,
                stream_target,
                &key,
                "actual-new-restored-stream",
                30000,
            )
            .unwrap();
        assert!(new_stream.receipt.is_some());
        assert_eq!(
            restored
                .reader_status("original-unknown-reader")
                .unwrap()
                .unwrap(),
            before
        );
        assert!(
            restored
                .open_reader(&key, "original-unknown-reader", &"c".repeat(64), 30000)
                .is_err()
        );
        {
            let guard = restored.inner.connection.lock().unwrap();
            let connection = guard.as_ref().unwrap();
            assert!(
                matches_parent(
                    &restored.inner,
                    connection,
                    (parent.dev(), parent.ino()),
                    (parent.dev(), parent.ino())
                )
                .unwrap()
            );
            assert!(
                matches_parent(
                    &restored.inner,
                    connection,
                    (parent.dev(), parent.ino() + 1),
                    (parent.dev(), parent.ino())
                )
                .is_err()
            );
        }
        std::fs::write(target.join(FILE), raw + " ").unwrap();
        assert!(restored.reader_status("original-unknown-reader").is_err());
    }
}
