//! Same-owner v1-to-v2 upgrade under the original root writer; no maintenance effect is started.

use super::*;

/// Original immutable upgrade intent; its presence also fences current v1 constructors.
pub(super) const UPGRADE_INTENT: &str = ".maintenance-upgrade.json";
/// Bounded mandatory original-operation history, never a transient staging directory.
pub(crate) const JOURNAL_DIRECTORY: &str = "maintenance-v1";
/// Original software identity inside the journal, independently of its file path.
const JOURNAL_IDENTITY: &str = "identity.json";

/// Reads bounded original metadata before examining its version, without following a symlink.
fn metadata_value(path: &Path) -> Result<serde_json::Value, ArtifactStoreError> {
    let mut bytes = Vec::new();
    open_regular_file(path, "inspect Artifact original metadata")?
        .take(MAX_OWNER_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| io_error("read Artifact original metadata", error))?;
    if bytes.len() as u64 > MAX_OWNER_BYTES {
        return Err(unsafe_path_error(
            "read Artifact original metadata",
            "original metadata exceeds bound",
        ));
    }
    serde_json::from_slice(&bytes)
        .map_err(|error| io_error("decode Artifact original metadata", io::Error::other(error)))
}

/// Checks a closed same-owner journal header before any service can use the new format.
fn validate_journal(root: &Path, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let journal = root.join(JOURNAL_DIRECTORY);
    validate_directory_tree(&journal, false, "validate Artifact maintenance journal")?;
    validate_directory_tree(
        &journal.join("operations"),
        false,
        "validate original Artifact operation history",
    )?;
    if read_owner_for(
        &journal.join(JOURNAL_IDENTITY),
        ARTIFACT_REFERENCE_LAYOUT_SCHEMA,
    )? != *owner
    {
        return Err(unsafe_path_error(
            "validate Artifact maintenance journal",
            "original owner differs",
        ));
    }
    Ok(())
}

/// Publishes or resumes one exact metadata replacement; no original backend identity is generated.
fn replace_owner(root: &Path, name: &str, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let path = root.join(name);
    let existing = metadata_value(&path)?;
    let schema = existing
        .get("schema")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    if !matches!(
        schema,
        ARTIFACT_LAYOUT_SCHEMA | ARTIFACT_REFERENCE_LAYOUT_SCHEMA
    ) {
        return Err(unsafe_path_error(
            "upgrade Artifact owner",
            "original schema differs",
        ));
    }
    let mut original = read_owner_for(&path, schema)?;
    original.schema = ARTIFACT_REFERENCE_LAYOUT_SCHEMA.into();
    if original != *owner {
        return Err(unsafe_path_error(
            "upgrade Artifact owner",
            "original immutable owner differs",
        ));
    }
    if schema == ARTIFACT_REFERENCE_LAYOUT_SCHEMA {
        return Ok(());
    }
    let temporary = format!(".maintenance-replace-{name}.json");
    // A crash can leave only this exact original operation's deterministic file.
    let pending = root.join(&temporary);
    if exists(&pending)? {
        if read_owner_for(&pending, ARTIFACT_REFERENCE_LAYOUT_SCHEMA)? != *owner {
            return Err(unsafe_path_error(
                "resume Artifact owner",
                "replacement identity conflicts",
            ));
        }
    } else {
        let bytes = serde_json::to_vec(owner)
            .map_err(|error| io_error("encode Artifact owner", io::Error::other(error)))?;
        let mut options = OpenOptions::new();
        options.create_new(true).write(true);
        add_no_follow_flag(&mut options);
        let mut output = options
            .open(&pending)
            .map_err(|error| io_error("prepare Artifact owner replacement", error))?;
        output
            .write_all(&bytes)
            .map_err(|error| io_error("write Artifact owner replacement", error))?;
        output
            .sync_all()
            .map_err(|error| io_error("sync Artifact owner replacement", error))?;
        sync_directory(root, "sync Artifact owner replacement intent")?;
    }
    fs::rename(pending, path)
        .map_err(|error| io_error("publish Artifact owner replacement", error))?;
    sync_directory(root, "sync Artifact owner replacement")
}

/// Writes a new exact bounded immutable record; an existing file must match its original bytes.
fn create_record(root: &Path, name: &str, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let path = root.join(name);
    if exists(&path)? {
        if read_owner_for(&path, ARTIFACT_REFERENCE_LAYOUT_SCHEMA)? != *owner {
            return Err(unsafe_path_error(
                "initialize Artifact journal",
                "original identity conflicts",
            ));
        }
        return Ok(());
    }
    let bytes = serde_json::to_vec(owner)
        .map_err(|error| io_error("encode Artifact journal identity", io::Error::other(error)))?;
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    add_no_follow_flag(&mut options);
    let mut file = options
        .open(path)
        .map_err(|error| io_error("initialize Artifact journal identity", error))?;
    file.write_all(&bytes)
        .map_err(|error| io_error("write Artifact journal identity", error))?;
    file.sync_all()
        .map_err(|error| io_error("sync Artifact journal identity", error))?;
    sync_directory(root, "sync Artifact journal identity directory")
}

/// Completes only the original upgrade or validates all mandatory files of a previously accepted v2 root.
pub(crate) fn open_references(root: &Path) -> Result<OpenLayout, ArtifactStoreError> {
    let intent = root.join(UPGRADE_INTENT);
    let owner_path = root.join(ARTIFACT_OWNER_FILE);
    if !exists(&intent)? && exists(&owner_path)? {
        let value = metadata_value(&owner_path)?;
        if value.get("schema").and_then(serde_json::Value::as_str)
            == Some(ARTIFACT_REFERENCE_LAYOUT_SCHEMA)
        {
            let owner = read_owner_for(&owner_path, ARTIFACT_REFERENCE_LAYOUT_SCHEMA)?;
            if read_owner_for(&root.join(BLOB_DIRECTORY), ARTIFACT_REFERENCE_LAYOUT_SCHEMA)?
                != owner
            {
                return Err(unsafe_path_error(
                    "open Artifact v2",
                    "original fence differs",
                ));
            }
            validate_journal(root, &owner)?;
            validate_directory_tree(
                &root.join(RETAINED_BLOB_DIRECTORY),
                false,
                "validate retained Artifact directory",
            )?;
            return Ok(OpenLayout {
                blob_directory: RETAINED_BLOB_DIRECTORY,
                owner_id: Some(owner.owner_id),
            });
        }
    }
    let owner = if exists(&intent)? {
        read_owner_for(&intent, ARTIFACT_REFERENCE_LAYOUT_SCHEMA)?
    } else {
        super::open(root, true)?;
        let mut owner = read_owner(&owner_path)?;
        owner.schema = ARTIFACT_REFERENCE_LAYOUT_SCHEMA.into();
        create_record(root, UPGRADE_INTENT, &owner)?;
        owner
    };
    let journal = root.join(JOURNAL_DIRECTORY);
    create_directory(&journal, "create original Artifact journal")?;
    create_record(&journal, JOURNAL_IDENTITY, &owner)?;
    create_directory(
        &journal.join("operations"),
        "create original Artifact operation history",
    )?;
    validate_journal(root, &owner)?;
    replace_owner(root, BLOB_DIRECTORY, &owner)?;
    replace_owner(root, ARTIFACT_OWNER_FILE, &owner)?;
    fs::remove_file(intent)
        .map_err(|error| io_error("complete original Artifact upgrade", error))?;
    sync_directory(root, "sync original Artifact upgrade completion")?;
    Ok(OpenLayout {
        blob_directory: RETAINED_BLOB_DIRECTORY,
        owner_id: Some(owner.owner_id),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FileSystemArtifactStore, digest_bytes};

    /// Same-root v2 upgrade keeps original backend and actual content inode; old constructors refuse.
    #[test]
    fn upgrade_preserves_original_owner_and_requires_complete_journal() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().join("artifact");
        let old = FileSystemArtifactStore::new_with_retention(&root).unwrap();
        let owner = old.owner_id().unwrap().to_string();
        let bytes = b"original actual Artifact content";
        let mut upload = old.begin_upload("original").unwrap();
        upload.write_chunk(bytes).unwrap();
        upload
            .finalize(&digest_bytes(bytes), bytes.len() as u64)
            .unwrap();
        drop(upload);
        let inode = directory_identity(&root.join(RETAINED_BLOB_DIRECTORY)).unwrap();
        assert!(FileSystemArtifactStore::new_with_reference_journal(&root).is_err());
        drop(old);
        let current = FileSystemArtifactStore::new_with_reference_journal(&root).unwrap();
        assert_eq!(current.owner_id(), Some(owner.as_str()));
        assert_eq!(current.storage_schema(), ARTIFACT_REFERENCE_LAYOUT_SCHEMA);
        current
            .verify_artifact(&digest_bytes(bytes), bytes.len() as u64)
            .unwrap();
        assert_eq!(
            directory_identity(&root.join(RETAINED_BLOB_DIRECTORY)).unwrap(),
            inode
        );
        drop(current);
        assert!(FileSystemArtifactStore::new_with_retention(&root).is_err());
        assert!(FileSystemArtifactStore::new(&root).is_err());
        let header = root.join(JOURNAL_DIRECTORY).join(JOURNAL_IDENTITY);
        fs::remove_file(&header).unwrap();
        assert!(FileSystemArtifactStore::new_with_reference_journal(&root).is_err());
        assert!(!header.exists());
    }

    /// Every persisted original upgrade stage resumes without generating another owner or dropping content.
    #[test]
    fn original_upgrade_intent_survives_each_publication_window() {
        for window in 0..=3 {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path().join("artifact");
            let original = FileSystemArtifactStore::new_with_retention(&root).unwrap();
            let id = original.owner_id().unwrap().to_string();
            drop(original);
            let mut owner = read_owner(&root.join(ARTIFACT_OWNER_FILE)).unwrap();
            owner.schema = ARTIFACT_REFERENCE_LAYOUT_SCHEMA.into();
            create_record(&root, UPGRADE_INTENT, &owner).unwrap();
            if window > 0 {
                let journal = root.join(JOURNAL_DIRECTORY);
                create_directory(&journal, "fixture original journal directory").unwrap();
                create_record(&journal, JOURNAL_IDENTITY, &owner).unwrap();
            }
            if window > 1 {
                replace_owner(&root, BLOB_DIRECTORY, &owner).unwrap();
            }
            if window > 2 {
                replace_owner(&root, ARTIFACT_OWNER_FILE, &owner).unwrap();
            }
            assert!(FileSystemArtifactStore::new_with_retention(&root).is_err());
            let current = FileSystemArtifactStore::new_with_reference_journal(&root).unwrap();
            assert_eq!(current.owner_id(), Some(id.as_str()));
            assert!(!root.join(UPGRADE_INTENT).exists());
            validate_journal(&root, &owner).unwrap();
        }
    }
}
