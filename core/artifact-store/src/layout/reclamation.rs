//! Explicit same-owner source GC journal; retained tombstones deny both old and new content effects.

use super::*;
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use std::collections::BTreeSet;

/// Original v3-to-v5 intent fences all earlier constructors before any header is changed.
pub(super) const UPGRADE_INTENT: &str = ".reclamation-upgrade.json";
/// Mandatory local GC database, protected by the original root writer rather than network grants.
const DATABASE: &str = "reclamation-v1/journal.sqlite3";
/// Explicit standalone original journal generation, independent of application checkpoint versions.
const SCHEMA: &str = "roboguide.artifact-gc-sql/v0.1";
/// Complete fixed SQLite schema; inventories are updated atomically with their original rows.
const SQL: &str = "CREATE TABLE gc_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE gc_commands(command_id TEXT PRIMARY KEY,intent_json TEXT NOT NULL,receipt_json TEXT);
CREATE TABLE gc_tombstones(content_digest TEXT PRIMARY KEY,first_command_id TEXT NOT NULL,
 tombstone_json TEXT NOT NULL,FOREIGN KEY(first_command_id) REFERENCES gc_commands(command_id));";

/// Converts actual SQLite failures without treating an unreadable original journal as empty.
fn sql_error(error: rusqlite::Error) -> ArtifactStoreError {
    io_error(
        "original Artifact reclamation journal",
        io::Error::other(error),
    )
}

/// Rejects unbounded or malformed original evidence before it can support a retained tombstone.
fn json(raw: &str) -> Result<serde_json::Value, ArtifactStoreError> {
    if raw.len() > 512 * 1024 {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original record exceeds bound",
        ));
    }
    let value: serde_json::Value = serde_json::from_str(raw)
        .map_err(|error| io_error("decode original Artifact GC", io::Error::other(error)))?;
    if !value.is_object() || serde_json::to_string(&value).ok().as_deref() != Some(raw) {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original record is not canonical",
        ));
    }
    Ok(value)
}

/// Reads a mandatory fixed metadata row; absence never bootstraps replacement GC authority.
fn metadata(connection: &Connection, key: &str) -> Result<String, ArtifactStoreError> {
    connection
        .query_row("SELECT value FROM gc_metadata WHERE key=?1", [key], |row| {
            row.get(0)
        })
        .map_err(sql_error)
}

/// Reads a bounded sorted complete inventory before checking rows for loss or orphan authority.
fn inventory(connection: &Connection, key: &str) -> Result<BTreeSet<String>, ArtifactStoreError> {
    let raw = metadata(connection, key)?;
    if raw.len() > 1024 * 1024 {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original inventory exceeds bound",
        ));
    }
    let values: Vec<String> = serde_json::from_str(&raw)
        .map_err(|error| io_error("decode original GC inventory", io::Error::other(error)))?;
    if values.len() > 4096 || values.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original inventory is incomplete or unsorted",
        ));
    }
    Ok(values.into_iter().collect())
}

/// Checks a closed JSON shape without accepting undeclared replacement authority fields.
fn keys(value: &serde_json::Value, expected: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == expected.len() && expected.iter().all(|key| object.contains_key(*key))
    })
}

/// Keeps original subjects/paths bounded and free of wildcard or control interpretation.
fn text(value: &serde_json::Value, maximum: usize) -> bool {
    value.as_str().is_some_and(|value| {
        !value.trim().is_empty()
            && value.len() <= maximum
            && !value.contains('*')
            && !value.chars().any(char::is_control)
    })
}

/// Recognizes only complete canonical content or evidence identities.
fn checksum(value: &serde_json::Value) -> bool {
    value
        .as_str()
        .is_some_and(|value| crate::normalize_digest(value).ok().as_deref() == Some(value))
}

/// Retains only actual regular-file descriptor identities, not a path or PID declaration alone.
fn inode(value: &serde_json::Value) -> bool {
    keys(value, &["device", "inode"])
        && value["device"].as_u64().is_some()
        && value["inode"].as_u64().is_some_and(|value| value > 0)
}

/// Validates complete retained OS/closure evidence without treating it as current deletion permission.
fn original_evidence(value: &serde_json::Value) -> bool {
    if !keys(
        value,
        &[
            "schema",
            "command_id",
            "component_id",
            "store_owner_id",
            "profile_digest",
            "content_digest",
            "byte_size",
            "reason",
            "kernel",
            "fence",
            "content_inode",
            "known_authorities",
            "closure",
            "original_tombstone",
        ],
    ) || !text(&value["component_id"], 128)
        || !text(&value["reason"], 1024)
        || !checksum(&value["profile_digest"])
        || (!value["content_inode"].is_null() && !inode(&value["content_inode"]))
        || (!value["original_tombstone"].is_null() && !text(&value["original_tombstone"], 128))
    {
        return false;
    }
    let kernel = &value["kernel"];
    if !keys(
        kernel,
        &["boot_id", "time_namespace", "admitted_ns", "deadline_ns"],
    ) || !text(&kernel["boot_id"], 128)
        || !text(&kernel["time_namespace"], 128)
        || kernel["admitted_ns"]
            .as_u64()
            .zip(kernel["deadline_ns"].as_u64())
            .is_none_or(|(start, end)| start >= end)
    {
        return false;
    }
    let fence = &value["fence"];
    if !keys(fence, &["pid", "start_ticks", "uid", "writers"])
        || !fence["pid"].as_u64().is_some_and(|id| id > 0)
        || !fence["start_ticks"].as_u64().is_some_and(|id| id > 0)
        || fence["uid"].as_u64().is_none()
    {
        return false;
    }
    let Some(writers) = fence["writers"].as_array() else {
        return false;
    };
    let mut seen = BTreeSet::new();
    if writers.is_empty()
        || writers.len() > 4096
        || writers.iter().any(|writer| {
            !keys(writer, &["component_id", "path", "device", "inode"])
                || !text(&writer["component_id"], 128)
                || !text(&writer["path"], 4096)
                || !writer["path"]
                    .as_str()
                    .is_some_and(|path| Path::new(path).is_absolute())
                || writer["device"].as_u64().is_none()
                || !writer["inode"].as_u64().is_some_and(|id| id > 0)
                || !seen.insert(writer["path"].as_str().unwrap_or(""))
        })
    {
        return false;
    }
    let (Some(known), Some(closure)) = (
        value["known_authorities"].as_array(),
        value["closure"].as_array(),
    ) else {
        return false;
    };
    if known.len() > 1024
        || known.len() != closure.len()
        || known
            .windows(2)
            .any(|pair| pair[0].as_str() >= pair[1].as_str())
    {
        return false;
    }
    known.iter().zip(closure).all(|(authority, proof)| {
        let actual = authority
            .as_str()
            .unwrap_or("")
            .strip_prefix("controller-store:")
            .unwrap_or("");
        actual.len() == 64
            && actual
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            && keys(
                proof,
                &[
                    "authority_id",
                    "component_id",
                    "event_sequence",
                    "checkpoint_digest",
                    "catalog_digest",
                ],
            )
            && proof["authority_id"] == *authority
            && text(&proof["component_id"], 128)
            && proof["event_sequence"].as_u64().is_some()
            && checksum(&proof["checkpoint_digest"])
            && checksum(&proof["catalog_digest"])
    })
}

/// Validates complete original heads, intents and tombstones under an already held root lease.
fn check(connection: &Connection, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let tables = connection
        .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .map_err(sql_error)?
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(sql_error)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(sql_error)?;
    if tables != ["gc_commands", "gc_metadata", "gc_tombstones"]
        || connection
            .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
            .map_err(sql_error)?
            != "delete"
        || metadata(connection, "schema")? != SCHEMA
        || metadata(connection, "store_owner_id")? != owner.owner_id
        || connection
            .query_row("SELECT COUNT(*) FROM gc_metadata", [], |row| {
                row.get::<_, u64>(0)
            })
            .map_err(sql_error)?
            != 4
        || connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('view','trigger')",
                [],
                |row| row.get::<_, u64>(0),
            )
            .map_err(sql_error)?
            != 0
    {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original SQL marker/tables/owner differ",
        ));
    }
    let commands = inventory(connection, "command_ids")?;
    let tombstones = inventory(connection, "tombstone_digests")?;
    let mut actual_commands = BTreeSet::new();
    let mut intents = std::collections::BTreeMap::new();
    let mut receipts = std::collections::BTreeMap::new();
    let mut size = 0usize;
    let mut query=connection.prepare("SELECT command_id,length(CAST(intent_json AS BLOB)),length(CAST(receipt_json AS BLOB)),intent_json,receipt_json FROM gc_commands LIMIT 4097").map_err(sql_error)?;
    let mut rows = query.query([]).map_err(sql_error)?;
    while let Some(row) = rows.next().map_err(sql_error)? {
        let length: usize = row.get(1).map_err(sql_error)?;
        let receipt_length: Option<usize> = row.get(2).map_err(sql_error)?;
        size = size
            .saturating_add(length)
            .saturating_add(receipt_length.unwrap_or(0));
        if length > 512 * 1024
            || receipt_length.is_some_and(|size| size > 512 * 1024)
            || size > 64 * 1024 * 1024
        {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original history exceeds bound",
            ));
        }
        let id: String = row.get(0).map_err(sql_error)?;
        let raw: String = row.get(3).map_err(sql_error)?;
        let intent = json(&raw)?;
        let digest = intent["content_digest"].as_str().unwrap_or("");
        if id.is_empty()
            || id.len() > 128
            || !id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
            || intent["schema"] != "roboguide.artifact-gc-intent/v0.1"
            || intent["command_id"] != id
            || intent["store_owner_id"] != owner.owner_id
            || crate::normalize_digest(digest).ok().as_deref() != Some(digest)
            || !intent["byte_size"]
                .as_u64()
                .is_some_and(|size| size <= 4 * 1024 * 1024 * 1024)
            || !commands.contains(&id)
            || !original_evidence(&intent)
        {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original command or owner/content changed",
            ));
        }
        if let Some(raw) = row.get::<_, Option<String>>(4).map_err(sql_error)? {
            let receipt = json(&raw)?;
            if receipt["schema"] != "roboguide.artifact-gc-receipt/v0.1"
                || receipt["command_id"] != id
                || receipt["intent_digest"]
                    != domain::artifact_adoption_digest(&intent).map_err(|error| {
                        io_error("validate Artifact GC", io::Error::other(error))
                    })?
                || !matches!(
                    receipt["result"].as_str(),
                    Some("deleted" | "already_missing")
                )
            {
                return Err(unsafe_path_error(
                    "validate Artifact GC",
                    "original GC receipt changed",
                ));
            }
            receipts.insert(id.clone(), receipt);
        }
        actual_commands.insert(id.clone());
        intents.insert(id, intent);
    }
    let mut actual_tombstones = BTreeSet::new();
    let mut original_tombstones = std::collections::BTreeMap::new();
    let mut query=connection.prepare("SELECT content_digest,first_command_id,length(CAST(tombstone_json AS BLOB)),tombstone_json FROM gc_tombstones LIMIT 4097").map_err(sql_error)?;
    let mut rows = query.query([]).map_err(sql_error)?;
    while let Some(row) = rows.next().map_err(sql_error)? {
        if row.get::<_, usize>(2).map_err(sql_error)? > 512 * 1024 {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original tombstone exceeds bound",
            ));
        }
        let digest: String = row.get(0).map_err(sql_error)?;
        let command: String = row.get(1).map_err(sql_error)?;
        let value = json(&row.get::<_, String>(3).map_err(sql_error)?)?;
        let intent = intents.get(&command).ok_or_else(|| {
            unsafe_path_error(
                "validate Artifact GC",
                "original tombstone command is absent",
            )
        })?;
        if !keys(
            &value,
            &[
                "schema",
                "store_owner_id",
                "content_digest",
                "byte_size",
                "first_command_id",
                "first_intent_digest",
            ],
        ) || value["schema"] != "roboguide.artifact-gc-tombstone/v0.1"
            || value["store_owner_id"] != owner.owner_id
            || value["content_digest"] != digest
            || value["byte_size"] != intent["byte_size"]
            || value["first_command_id"] != command
            || intent["content_digest"] != digest
            || value["first_intent_digest"]
                != domain::artifact_adoption_digest(intent)
                    .map_err(|error| io_error("validate Artifact GC", io::Error::other(error)))?
            || !tombstones.contains(&digest)
        {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original tombstone ownership changed",
            ));
        }
        actual_tombstones.insert(digest.clone());
        original_tombstones.insert(digest, value);
    }
    if actual_commands != commands || actual_tombstones != tombstones {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original mandatory command/tombstone inventory row is missing",
        ));
    }
    for (command, intent) in &intents {
        let tombstone = original_tombstones
            .get(intent["content_digest"].as_str().unwrap_or(""))
            .ok_or_else(|| {
                unsafe_path_error(
                    "validate Artifact GC",
                    "original command lost its atomic tombstone",
                )
            })?;
        if (!intent["original_tombstone"].is_null()
            && intent["original_tombstone"] != tombstone["first_command_id"])
            || (intent["original_tombstone"].is_null() && tombstone["first_command_id"] != *command)
        {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original predecessor tombstone changed",
            ));
        }
        if let Some(receipt) = receipts.get(command)
            && (!keys(
                receipt,
                &[
                    "schema",
                    "command_id",
                    "intent_digest",
                    "tombstone_digest",
                    "result",
                    "completed_ns",
                    "deleted_inode",
                ],
            ) || receipt["tombstone_digest"]
                != domain::artifact_adoption_digest(tombstone)
                    .map_err(|error| io_error("validate Artifact GC", io::Error::other(error)))?
                || !receipt["completed_ns"].as_u64().is_some_and(|time| {
                    intent["kernel"]["admitted_ns"]
                        .as_u64()
                        .is_some_and(|start| start <= time)
                        && intent["kernel"]["deadline_ns"]
                            .as_u64()
                            .is_some_and(|end| time < end)
                })
                || (receipt["result"] == "deleted"
                    && (intent["content_inode"].is_null()
                        || receipt["deleted_inode"] != intent["content_inode"]))
                || (receipt["result"] == "already_missing" && !receipt["deleted_inode"].is_null()))
        {
            return Err(unsafe_path_error(
                "validate Artifact GC",
                "original receipt lacks actual tombstone/effect proof",
            ));
        }
    }
    Ok(())
}

/// Opens only an existing regular bounded database, with no creation or replacement writer effects.
fn readonly(root: &Path) -> Result<Connection, ArtifactStoreError> {
    let path = root.join(DATABASE);
    for suffix in ["-wal", "-shm"] {
        if exists(&root.join(format!("{DATABASE}{suffix}")))? {
            return Err(unsafe_path_error(
                "observe original GC journal",
                "original GC SQL has unsupported WAL authority",
            ));
        }
    }
    validate_directory_tree(
        path.parent().expect("fixed database parent"),
        false,
        "observe original GC directory",
    )?;
    let mut file = open_regular_file(&path, "observe original GC journal")?;
    if file
        .metadata()
        .map_err(|error| io_error("observe original GC journal", error))?
        .len()
        > 128 * 1024 * 1024
    {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original database exceeds bound",
        ));
    }
    let mut header = [0u8; 20];
    file.read_exact(&mut header)
        .map_err(|error| io_error("read original GC SQL header", error))?;
    if &header[..16] != b"SQLite format 3\0" || header[18] != 1 || header[19] != 1 {
        return Err(unsafe_path_error(
            "observe original GC journal",
            "original SQL header requires explicit DELETE journal mode",
        ));
    }
    Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(sql_error)
}

/// Refuses missing or changed original SQL authority before a v5 store is accepted.
pub(super) fn validate(root: &Path, owner: &Owner) -> Result<(), ArtifactStoreError> {
    if read_owner_for(
        &root.join("reclamation-v1/identity.json"),
        ARTIFACT_GC_LAYOUT_SCHEMA,
    )? != *owner
    {
        return Err(unsafe_path_error(
            "validate Artifact GC",
            "original journal identity changed",
        ));
    }
    check(&readonly(root)?, owner)
}

/// Initialises only while the original upgrade intent is held, never for an accepted missing journal.
fn initialize(root: &Path, owner: &Owner) -> Result<(), ArtifactStoreError> {
    let directory = root.join("reclamation-v1");
    create_directory(&directory, "create original Artifact GC journal")?;
    references::create_record(&directory, "identity.json", owner)?;
    let path = root.join(DATABASE);
    if exists(&path)? {
        open_regular_file(&path, "inspect original GC SQL")?;
    }
    let mut connection = Connection::open(&path).map_err(sql_error)?;
    connection
        .pragma_update(None, "journal_mode", "DELETE")
        .map_err(sql_error)?;
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(sql_error)?;
    let tables: u64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'",
            [],
            |row| row.get(0),
        )
        .map_err(sql_error)?;
    if tables == 0 {
        let transaction = connection.transaction().map_err(sql_error)?;
        transaction.execute_batch(SQL).map_err(sql_error)?;
        for (key, value) in [
            ("schema", SCHEMA),
            ("store_owner_id", owner.owner_id.as_str()),
            ("command_ids", "[]"),
            ("tombstone_digests", "[]"),
        ] {
            transaction
                .execute("INSERT INTO gc_metadata VALUES(?1,?2)", [key, value])
                .map_err(sql_error)?;
        }
        transaction.commit().map_err(sql_error)?;
    }
    check(&connection, owner)?;
    connection.close().map_err(|(_, error)| sql_error(error))?;
    open_regular_file(&path, "sync original GC journal")?
        .sync_all()
        .map_err(|error| io_error("sync original GC journal", error))?;
    sync_directory(&directory, "sync original GC journal directory")
}

/// Migrates the same original owner without losing inventory/history or accepting missing final metadata.
pub(crate) fn open_reclamation(root: &Path) -> Result<OpenLayout, ArtifactStoreError> {
    let path = root.join(UPGRADE_INTENT);
    if !exists(&path)?
        && let Ok(owner) =
            read_owner_for(&root.join(ARTIFACT_OWNER_FILE), ARTIFACT_GC_LAYOUT_SCHEMA)
    {
        inspect_retained(root)?;
        return Ok(OpenLayout {
            blob_directory: RETAINED_BLOB_DIRECTORY,
            owner_id: Some(owner.owner_id),
        });
    }
    let owner = if exists(&path)? {
        read_owner_for(&path, ARTIFACT_GC_LAYOUT_SCHEMA)?
    } else {
        authorities::open_authorities(root)?;
        let mut owner = read_owner_for(
            &root.join(ARTIFACT_OWNER_FILE),
            ARTIFACT_ARCHIVE_LAYOUT_SCHEMA,
        )?;
        owner.schema = ARTIFACT_GC_LAYOUT_SCHEMA.into();
        references::create_record(root, UPGRADE_INTENT, &owner)?;
        owner
    };
    initialize(root, &owner)?;
    for (directory, name) in [
        (root.join("maintenance-v1"), "identity.json"),
        (root.join("reference-authorities-v1"), "identity.json"),
        (root.to_path_buf(), BLOB_DIRECTORY),
        (root.to_path_buf(), ARTIFACT_OWNER_FILE),
    ] {
        references::replace_owner(&directory, name, &owner)?;
    }
    references::validate_journal(root, &owner)?;
    authorities::validate(root, &owner)?;
    validate(root, &owner)?;
    fs::remove_file(path)
        .map_err(|error| io_error("complete original Artifact GC upgrade", error))?;
    sync_directory(root, "sync original Artifact GC upgrade")?;
    Ok(OpenLayout {
        blob_directory: RETAINED_BLOB_DIRECTORY,
        owner_id: Some(owner.owner_id),
    })
}

/// Refuses a tombstoned digest even if old bytes remain or somebody supplies a fresh upload identifier.
pub(crate) fn require_live_content(root: &Path, digest: &str) -> Result<(), ArtifactStoreError> {
    let owner = read_owner_for(&root.join(ARTIFACT_OWNER_FILE), ARTIFACT_GC_LAYOUT_SCHEMA)?;
    let connection = readonly(root)?;
    check(&connection, &owner)?;
    let found = connection
        .query_row(
            "SELECT 1 FROM gc_tombstones WHERE content_digest=?1",
            [digest],
            |row| row.get::<_, u64>(0),
        )
        .optional()
        .map_err(sql_error)?;
    if found.is_some() {
        return Err(ArtifactStoreError::ArtifactNotFound {
            digest: digest.into(),
        });
    }
    Ok(())
}
