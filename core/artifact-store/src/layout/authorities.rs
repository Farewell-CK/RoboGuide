//! Original source-consumer inventory and same-owner v2-to-v3 old-tool fence.

use super::*;

/// A partial upgrade retains its original identity and never accepts v2 as final publication.
pub(super) const UPGRADE_INTENT: &str = ".archive-reference-upgrade.json";
/// Mandatory complete known-consumer inventory, independent of live per-command pins.
const DIRECTORY: &str = "reference-authorities-v1";

/// Exact known-consumer head; missing original records cannot look like an empty directory.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Head {
    /// Explicit original inventory semantics.
    schema: String,
    /// Actual immutable CAS owner, independent of its configured filesystem path.
    store_owner_id: String,
    /// Complete sorted original known consumers, never shrunk or inferred from current liveness.
    authority_ids: Vec<String>,
}

/// Reads the mandatory original head before any row can disappear through a directory scan.
fn read_head(directory: &Path, owner: &Owner) -> Result<Head, ArtifactStoreError> {
    let mut raw = Vec::new();
    open_regular_file(
        &directory.join("inventory.json"),
        "read original Artifact inventory head",
    )?
    .take(128 * 1024 + 1)
    .read_to_end(&mut raw)
    .map_err(|error| io_error("read original Artifact inventory head", error))?;
    if raw.len() > 128 * 1024 {
        return Err(unsafe_path_error(
            "read Artifact inventory head",
            "original head exceeds bound",
        ));
    }
    let head: Head = serde_json::from_slice(&raw).map_err(|error| {
        io_error(
            "decode original Artifact inventory head",
            io::Error::other(error),
        )
    })?;
    if head.schema != "roboguide.artifact-reference-inventory/v0.1"
        || head.store_owner_id != owner.owner_id
        || head.authority_ids.len() > 1024
        || head.authority_ids.windows(2).any(|pair| pair[0] >= pair[1])
        || head.authority_ids.iter().any(|id| {
            let hex = id.strip_prefix("controller-store:").unwrap_or("");
            hex.len() != 64
                || !hex
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
    {
        return Err(unsafe_path_error(
            "validate Artifact inventory head",
            "original owner or known consumers changed",
        ));
    }
    Ok(head)
}

/// Publishes only a nonshrinking complete head after original pending binding durability.
fn write_head(directory: &Path, head: &Head, initial: bool) -> Result<(), ArtifactStoreError> {
    let raw = serde_json::to_vec(head)
        .map_err(|error| io_error("encode Artifact inventory head", io::Error::other(error)))?;
    let temporary = directory.join(".inventory.pending");
    if exists(&temporary)? {
        let mut original = Vec::new();
        open_regular_file(&temporary, "read pending Artifact inventory head")?
            .take(128 * 1024 + 1)
            .read_to_end(&mut original)
            .map_err(|error| io_error("read pending Artifact inventory head", error))?;
        if original != raw {
            return Err(unsafe_path_error(
                "publish Artifact inventory head",
                "original pending head differs",
            ));
        }
    } else {
        let mut options = OpenOptions::new();
        options.create_new(true).write(true);
        add_no_follow_flag(&mut options);
        let mut file = options
            .open(&temporary)
            .map_err(|error| io_error("retain Artifact inventory head", error))?;
        file.write_all(&raw)
            .and_then(|()| file.sync_all())
            .map_err(|error| io_error("sync Artifact inventory head", error))?;
    }
    if initial {
        fs::hard_link(&temporary, directory.join("inventory.json"))
            .map_err(|error| io_error("publish original Artifact inventory", error))?;
        sync_directory(directory, "sync original Artifact inventory")?;
        fs::remove_file(temporary)
            .map_err(|error| io_error("complete original Artifact inventory", error))?;
    } else {
        fs::rename(temporary, directory.join("inventory.json"))
            .map_err(|error| io_error("publish original Artifact inventory head", error))?;
    }
    sync_directory(directory, "sync original Artifact inventory head")
}

/// Rejects missing original inventory files before an accepted owner may be opened or backed up.
pub(super) fn validate(root: &Path, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let directory = root.join(DIRECTORY);
    validate_directory_tree(&directory, false, "validate Artifact reference authorities")?;
    validate_directory_tree(
        &directory.join("controllers"),
        false,
        "validate original Controller inventory",
    )?;
    if read_owner_for(
        &directory.join("identity.json"),
        ARTIFACT_ARCHIVE_LAYOUT_SCHEMA,
    )? != *owner
    {
        return Err(unsafe_path_error(
            "validate Artifact reference authorities",
            "original owner conflicts",
        ));
    }
    let mut seen = std::collections::BTreeSet::new();
    let head = read_head(&directory, owner)?;
    let known = head
        .authority_ids
        .iter()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    for (index, entry) in fs::read_dir(directory.join("controllers"))
        .map_err(|error| io_error("read Artifact authority inventory", error))?
        .enumerate()
    {
        if index >= 2048 {
            return Err(unsafe_path_error(
                "validate Artifact reference authorities",
                "original inventory exceeds bounds",
            ));
        }
        let entry = entry.map_err(|error| io_error("read original Artifact authority", error))?;
        let name = entry.file_name().into_string().map_err(|_| {
            unsafe_path_error(
                "validate Artifact reference authorities",
                "original filename is invalid",
            )
        })?;
        let hex = name
            .strip_suffix(".json")
            .or_else(|| {
                name.strip_prefix('.')
                    .and_then(|name| name.strip_suffix(".pending"))
            })
            .unwrap_or("");
        if hex.len() != 64
            || !hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(unsafe_path_error(
                "validate Artifact reference authorities",
                "unknown original binding record",
            ));
        }
        let mut raw = Vec::new();
        open_regular_file(&entry.path(), "read original Artifact authority")?
            .take(MAX_OWNER_BYTES + 1)
            .read_to_end(&mut raw)
            .map_err(|error| io_error("read original Artifact authority", error))?;
        let expected = serde_json::json!({"schema":"roboguide.artifact-reference-authority/v0.1","authority_id":format!("controller-store:{hex}"),"store_owner_id":owner.owner_id});
        if serde_json::from_slice::<serde_json::Value>(&raw).map_err(|error| {
            io_error(
                "decode original Artifact authority",
                io::Error::other(error),
            )
        })? != expected
        {
            return Err(unsafe_path_error(
                "validate Artifact reference authorities",
                "original binding is incomplete or changed",
            ));
        }
        seen.insert(hex.to_string());
        if name.ends_with(".json") && !known.contains(&format!("controller-store:{hex}")) {
            return Err(unsafe_path_error(
                "validate Artifact reference authorities",
                "original final consumer is absent from the complete head",
            ));
        }
    }
    if seen.len() > 1024 {
        return Err(unsafe_path_error(
            "validate Artifact reference authorities",
            "original authority inventory is full",
        ));
    }
    if known
        .iter()
        .any(|id| !seen.contains(id.strip_prefix("controller-store:").unwrap_or("")))
    {
        return Err(unsafe_path_error(
            "validate Artifact reference authorities",
            "original known consumer record is missing",
        ));
    }
    let pending = directory.join(".inventory.pending");
    if exists(&pending)? {
        let mut raw = Vec::new();
        open_regular_file(&pending, "read original pending inventory head")?
            .take(128 * 1024 + 1)
            .read_to_end(&mut raw)
            .map_err(|error| io_error("read original pending inventory head", error))?;
        let candidate: Head = serde_json::from_slice(&raw).map_err(|error| {
            io_error(
                "decode original pending inventory head",
                io::Error::other(error),
            )
        })?;
        if candidate.schema != head.schema
            || candidate.store_owner_id != head.store_owner_id
            || candidate.authority_ids.len() > 1024
            || candidate
                .authority_ids
                .windows(2)
                .any(|pair| pair[0] >= pair[1])
            || known.iter().any(|id| !candidate.authority_ids.contains(id))
            || candidate
                .authority_ids
                .iter()
                .any(|id| !seen.contains(id.strip_prefix("controller-store:").unwrap_or("")))
        {
            return Err(unsafe_path_error(
                "validate pending Artifact inventory head",
                "original known consumers conflict",
            ));
        }
    }
    Ok(())
}

/// Completes only the original same-owner migration, preserving all prior maintenance history.
pub(crate) fn open_authorities(root: &Path) -> Result<OpenLayout, ArtifactStoreError> {
    let path = root.join(UPGRADE_INTENT);
    let owner_path = root.join(ARTIFACT_OWNER_FILE);
    if !exists(&path)? && exists(&owner_path)? {
        let candidate = read_owner_for(&owner_path, ARTIFACT_ARCHIVE_LAYOUT_SCHEMA);
        if let Ok(owner) = candidate {
            if read_owner_for(&root.join(BLOB_DIRECTORY), ARTIFACT_ARCHIVE_LAYOUT_SCHEMA)? != owner
            {
                return Err(unsafe_path_error(
                    "open Artifact v3",
                    "original fence conflicts",
                ));
            }
            references::validate_journal(root, &owner)?;
            validate(root, &owner)?;
            validate_directory_tree(
                &root.join(RETAINED_BLOB_DIRECTORY),
                false,
                "validate original Artifact bytes",
            )?;
            return Ok(OpenLayout {
                blob_directory: RETAINED_BLOB_DIRECTORY,
                owner_id: Some(owner.owner_id),
            });
        }
    }
    let owner = if exists(&path)? {
        read_owner_for(&path, ARTIFACT_ARCHIVE_LAYOUT_SCHEMA)?
    } else {
        references::open_references(root)?;
        let mut owner = read_owner_for(&owner_path, ARTIFACT_REFERENCE_LAYOUT_SCHEMA)?;
        owner.schema = ARTIFACT_ARCHIVE_LAYOUT_SCHEMA.into();
        references::create_record(root, UPGRADE_INTENT, &owner)?;
        owner
    };
    let directory = root.join(DIRECTORY);
    create_directory(&directory, "create original Artifact consumer inventory")?;
    references::create_record(&directory, "identity.json", &owner)?;
    create_directory(
        &directory.join("controllers"),
        "create original Controller inventory",
    )?;
    if !exists(&directory.join("inventory.json"))? {
        write_head(
            &directory,
            &Head {
                schema: "roboguide.artifact-reference-inventory/v0.1".into(),
                store_owner_id: owner.owner_id.clone(),
                authority_ids: Vec::new(),
            },
            true,
        )?;
    }
    let pending_head = directory.join(".inventory.pending");
    if exists(&pending_head)? {
        let mut original = Vec::new();
        let mut pending = Vec::new();
        open_regular_file(
            &directory.join("inventory.json"),
            "read original upgrade inventory",
        )?
        .take(128 * 1024 + 1)
        .read_to_end(&mut original)
        .map_err(|error| io_error("read original upgrade inventory", error))?;
        open_regular_file(&pending_head, "read pending upgrade inventory")?
            .take(128 * 1024 + 1)
            .read_to_end(&mut pending)
            .map_err(|error| io_error("read pending upgrade inventory", error))?;
        if original != pending {
            return Err(unsafe_path_error(
                "resume original inventory upgrade",
                "original pending head changed",
            ));
        }
        fs::remove_file(pending_head)
            .map_err(|error| io_error("complete original inventory upgrade", error))?;
        sync_directory(&directory, "sync original inventory upgrade completion")?;
    }
    validate(root, &owner)?;
    references::replace_owner(
        &root.join(references::JOURNAL_DIRECTORY),
        references::JOURNAL_IDENTITY,
        &owner,
    )?;
    references::replace_owner(root, BLOB_DIRECTORY, &owner)?;
    references::replace_owner(root, ARTIFACT_OWNER_FILE, &owner)?;
    references::validate_journal(root, &owner)?;
    fs::remove_file(path)
        .map_err(|error| io_error("complete original Artifact v3 upgrade", error))?;
    sync_directory(root, "sync original Artifact v3 upgrade")?;
    Ok(OpenLayout {
        blob_directory: RETAINED_BLOB_DIRECTORY,
        owner_id: Some(owner.owner_id),
    })
}

/// Adds one actual immutable Controller software-store identity without granting content access.
pub(crate) fn bind_authority(root: &Path, id: &str) -> Result<(), ArtifactStoreError> {
    let hex = id.strip_prefix("controller-store:").unwrap_or("");
    if hex.len() != 64
        || !hex
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(unsafe_path_error(
            "bind Artifact reference authority",
            "actual original Controller identity is invalid",
        ));
    }
    let owner = read_owner_for(
        &root.join(ARTIFACT_OWNER_FILE),
        ARTIFACT_ARCHIVE_LAYOUT_SCHEMA,
    )?;
    validate(root, &owner)?;
    let directory = root.join(DIRECTORY).join("controllers");
    let path = directory.join(format!("{hex}.json"));
    let bytes=serde_json::to_vec(&serde_json::json!({"schema":"roboguide.artifact-reference-authority/v0.1","authority_id":id,"store_owner_id":owner.owner_id})).map_err(|error|io_error("encode Artifact authority",io::Error::other(error)))?;
    if exists(&path)? {
        let mut original = Vec::new();
        open_regular_file(&path, "read original Artifact authority")?
            .take(MAX_OWNER_BYTES + 1)
            .read_to_end(&mut original)
            .map_err(|error| io_error("read original Artifact authority", error))?;
        if original != bytes {
            return Err(unsafe_path_error(
                "bind Artifact reference authority",
                "original Controller binding changed",
            ));
        }
        return Ok(());
    }
    let count = fs::read_dir(&directory)
        .map_err(|error| io_error("bound original Artifact authorities", error))?
        .take(1025)
        .count();
    if count >= 1024 {
        return Err(unsafe_path_error(
            "bind Artifact reference authority",
            "original Controller inventory is full",
        ));
    }
    let pending = directory.join(format!(".{hex}.pending"));
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    add_no_follow_flag(&mut options);
    if exists(&pending)? {
        let mut original = Vec::new();
        open_regular_file(&pending, "read original pending Artifact authority")?
            .take(MAX_OWNER_BYTES + 1)
            .read_to_end(&mut original)
            .map_err(|error| io_error("read original pending Artifact authority", error))?;
        if original != bytes {
            return Err(unsafe_path_error(
                "resume Artifact reference authority",
                "original pending binding changed",
            ));
        }
    } else {
        let mut file = options
            .open(&pending)
            .map_err(|error| io_error("retain original Artifact authority", error))?;
        file.write_all(&bytes)
            .and_then(|()| file.sync_all())
            .map_err(|error| io_error("sync original Artifact authority", error))?;
    }
    let inventory = root.join(DIRECTORY);
    let mut head = read_head(&inventory, &owner)?;
    if !head.authority_ids.iter().any(|authority| authority == id) {
        head.authority_ids.push(id.into());
        head.authority_ids.sort();
        write_head(&inventory, &head, false)?;
    }
    fs::hard_link(&pending, &path)
        .map_err(|error| io_error("publish original Artifact authority", error))?;
    sync_directory(&directory, "sync original Artifact authority")?;
    fs::remove_file(pending)
        .map_err(|error| io_error("complete original Artifact authority", error))?;
    sync_directory(&directory, "sync original Artifact authority completion")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ArtifactReadOnlyStore, FileSystemArtifactStore, digest_bytes};

    /// Real same-owner format upgrade fences old writers, and readonly observations create no files.
    #[test]
    fn original_authorities_and_readonly_lease_survive_exact_upgrade() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().join("cas");
        let old = FileSystemArtifactStore::new_with_reference_journal(&root).unwrap();
        let mut upload = old.begin_upload("opaque-original").unwrap();
        upload.write_chunk(b"original").unwrap();
        let blob = upload.finalize(&digest_bytes(b"original"), 8).unwrap();
        let actual_path = blob.path().to_path_buf();
        let owner = old.owner_id().unwrap().to_string();
        drop(upload);
        drop(old);
        let current = FileSystemArtifactStore::new_with_archive_references(&root).unwrap();
        assert_eq!(current.owner_id(), Some(owner.as_str()));
        assert_eq!(current.storage_schema(), ARTIFACT_ARCHIVE_LAYOUT_SCHEMA);
        assert_eq!(fs::read(&actual_path).unwrap(), b"original");
        let id = format!("controller-store:{}", "a".repeat(64));
        current.bind_reference_authority(&id).unwrap();
        current.bind_reference_authority(&id).unwrap();
        assert_eq!(
            fs::read_dir(root.join(DIRECTORY).join("controllers"))
                .unwrap()
                .count(),
            1
        );
        assert!(FileSystemArtifactStore::new_with_reference_journal(&root).is_err());
        drop(current);
        assert!(FileSystemArtifactStore::new_with_reference_journal(&root).is_err());
        let reader = ArtifactReadOnlyStore::open(&root).unwrap();
        assert_eq!(reader.owner_id(), owner);
        let mut bytes = Vec::new();
        reader
            .open_artifact(&digest_bytes(b"original"))
            .unwrap()
            .read_to_end(&mut bytes)
            .unwrap();
        assert_eq!(bytes, b"original");
        assert!(FileSystemArtifactStore::new_with_archive_references(&root).is_err());
        drop(reader);
        let binding = root
            .join(DIRECTORY)
            .join("controllers")
            .join(format!("{}.json", "a".repeat(64)));
        let original = fs::read(&binding).unwrap();
        fs::remove_file(&binding).unwrap();
        assert!(ArtifactReadOnlyStore::open(&root).is_err());
        assert!(FileSystemArtifactStore::new_with_archive_references(&root).is_err());
        fs::write(&binding, original).unwrap();
        fs::remove_dir_all(root.join(DIRECTORY).join("controllers")).unwrap();
        assert!(ArtifactReadOnlyStore::open(&root).is_err());
        assert!(FileSystemArtifactStore::new_with_archive_references(&root).is_err());
        assert!(!root.join(DIRECTORY).join("controllers").exists());
    }

    /// Every persisted migration prefix resumes its original owner instead of inventing a replacement.
    #[test]
    fn original_upgrade_intent_preserves_identity_at_each_durable_window() {
        for step in 0..=4 {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path().join("cas");
            let old = FileSystemArtifactStore::new_with_reference_journal(&root).unwrap();
            let id = old.owner_id().unwrap().to_string();
            drop(old);
            let mut owner = read_owner_for(
                &root.join(ARTIFACT_OWNER_FILE),
                ARTIFACT_REFERENCE_LAYOUT_SCHEMA,
            )
            .unwrap();
            owner.schema = ARTIFACT_ARCHIVE_LAYOUT_SCHEMA.into();
            references::create_record(&root, UPGRADE_INTENT, &owner).unwrap();
            if step >= 1 {
                let inventory = root.join(DIRECTORY);
                create_directory(&inventory, "unit upgrade inventory").unwrap();
                references::create_record(&inventory, "identity.json", &owner).unwrap();
                create_directory(&inventory.join("controllers"), "unit upgrade consumers").unwrap();
            }
            if step >= 2 {
                references::replace_owner(
                    &root.join(references::JOURNAL_DIRECTORY),
                    references::JOURNAL_IDENTITY,
                    &owner,
                )
                .unwrap();
            }
            if step >= 3 {
                references::replace_owner(&root, BLOB_DIRECTORY, &owner).unwrap();
            }
            if step >= 4 {
                references::replace_owner(&root, ARTIFACT_OWNER_FILE, &owner).unwrap();
            }
            let current = FileSystemArtifactStore::new_with_archive_references(&root).unwrap();
            assert_eq!(current.owner_id(), Some(id.as_str()));
            assert!(!root.join(UPGRADE_INTENT).exists());
        }
    }

    /// A partial original binding resumes exact bytes, while mutation or loss keeps a strict fence.
    #[test]
    fn original_binding_pending_is_retained_and_changed_authority_refuses_open() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().join("cas");
        let store = FileSystemArtifactStore::new_with_archive_references(&root).unwrap();
        let hex = "b".repeat(64);
        let id = format!("controller-store:{hex}");
        store.bind_reference_authority(&id).unwrap();
        let binding = root
            .join(DIRECTORY)
            .join("controllers")
            .join(format!("{hex}.json"));
        let pending = binding.parent().unwrap().join(format!(".{hex}.pending"));
        fs::rename(&binding, &pending).unwrap();
        store.bind_reference_authority(&id).unwrap();
        assert!(binding.is_file() && !pending.exists());
        drop(store);
        fs::write(&binding, b"{}").unwrap();
        assert!(FileSystemArtifactStore::new_with_archive_references(&root).is_err());
        assert_eq!(fs::read(&binding).unwrap(), b"{}");
        let missing = directory.path().join("missing");
        assert!(ArtifactReadOnlyStore::open(&missing).is_err());
        assert!(!missing.exists());
    }
}
