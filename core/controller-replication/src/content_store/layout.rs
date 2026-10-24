//! Actual original root/SQL lease, immutable storage identity and mandatory complete journal heads.

use super::*;
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};

/// Original immutable identity also fences every ordinary Artifact constructor.
const HEADER: &str = ".retention-owner.json";
/// Original creation authority remains mandatory rather than being regenerated after loss.
const CREATION: &str = ".content-creation.json";
/// Original actual metadata inode, separate from application State and payload files.
const DATABASE: &str = "content.sqlite3";
/// Maximum actual metadata database size; content payloads are not stored in these records.
const MAX_DATABASE_BYTES: u64 = 128 * 1024 * 1024;
/// Maximum one bounded original root identity or record.
const MAX_RECORD_BYTES: u64 = 16 * 1024;

/// Requires real absolute directories at every ancestor; no symlink or implicit parent traversal.
pub(super) fn directory(path: &Path) -> Result<(), String> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err("replica content requires an explicit real absolute root".into());
    }
    for ancestor in path.ancestors() {
        if !fs::symlink_metadata(ancestor)
            .map_err(|error| error.to_string())?
            .is_dir()
        {
            return Err("replica content ancestor is not an actual directory".into());
        }
    }
    Ok(())
}

/// Opens an actual regular leaf without following a link or creating replacement authority.
pub(super) fn regular(path: &Path, write: bool) -> Result<File, String> {
    directory(
        path.parent()
            .ok_or("replica content file parent is unavailable")?,
    )?;
    if !fs::symlink_metadata(path)
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("replica content requires an actual regular file".into());
    }
    let file = OpenOptions::new()
        .read(true)
        .write(write)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| error.to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("original replica content file changed type".into());
    }
    Ok(file)
}

/// Creates exactly one private new regular file; existing files are never truncated or followed.
pub(super) fn new_file(path: &Path) -> Result<File, String> {
    directory(
        path.parent()
            .ok_or("replica content new file parent is unavailable")?,
    )?;
    OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| error.to_string())
}

/// Durably publishes one new original identity file without replacing an accepted prior version.
fn publish(path: &Path, identity: &ReplicaContentIdentity) -> Result<(), String> {
    let raw = serde_json::to_vec(identity).map_err(|error| error.to_string())?;
    let mut file = new_file(path)?;
    file.write_all(&raw)
        .and_then(|()| file.sync_all())
        .map_err(|error| error.to_string())?;
    File::open(path.parent().ok_or("content identity parent unavailable")?)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())
}

/// Reads one bounded complete original identity before opening SQLite or accepting new work.
fn identity(path: &Path) -> Result<ReplicaContentIdentity, String> {
    let file = regular(path, false)?;
    if file.metadata().map_err(|error| error.to_string())?.len() > MAX_RECORD_BYTES {
        return Err("original replica content identity exceeds bound".into());
    }
    let mut raw = Vec::new();
    file.take(MAX_RECORD_BYTES + 1)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    if raw.len() as u64 > MAX_RECORD_BYTES {
        return Err("replica content identity grew beyond its bound".into());
    }
    serde_json::from_slice(&raw).map_err(|error| error.to_string())
}

/// Holds an original descriptor lease without claiming that a PID, Node or device stopped.
fn locked(file: File) -> Result<File, String> {
    file.try_lock()
        .map_err(|error| format!("original replica content writer is still active: {error}"))?;
    Ok(file)
}

/// Creates a private fresh store; partial or accepted evidence is never reset to empty authority.
pub(super) fn create(
    root: &Path,
    profile_identity: &str,
    member: u64,
    mode: ContentCreateMode,
) -> Result<ReplicaContentStore, String> {
    let reclamation = matches!(mode, ContentCreateMode::Reclamation);
    let snapshot_receives = matches!(
        mode,
        ContentCreateMode::Snapshots | ContentCreateMode::Reclamation
    );
    let streams = matches!(
        mode,
        ContentCreateMode::Streams | ContentCreateMode::Snapshots | ContentCreateMode::Reclamation
    );
    let producers = !matches!(mode, ContentCreateMode::Reader);
    if !crate::content::hex_digest(profile_identity) {
        return Err("replica content profile identity is invalid".into());
    }
    directory(
        root.parent()
            .ok_or("replica content root parent is unavailable")?,
    )?;
    fs::DirBuilder::new()
        .mode(0o700)
        .create(root)
        .map_err(|error| error.to_string())?;
    directory(root)?;
    let writer = locked(new_file(&root.join(".writer.lock"))?)?;
    let database = locked(new_file(&root.join(DATABASE))?)?;
    let root_info = fs::metadata(root).map_err(|error| error.to_string())?;
    let database_info = database.metadata().map_err(|error| error.to_string())?;
    let random = crate::supervision::new_process_instance()?;
    let original = ReplicaContentIdentity {
        schema: REPLICA_CONTENT_STORAGE_SCHEMA.into(),
        profile_identity: profile_identity.into(),
        member,
        store_owner: format!("replica-content:{:x}", Sha256::digest(random.as_bytes())),
        root_device: root_info.dev(),
        root_inode: root_info.ino(),
        database_device: database_info.dev(),
        database_inode: database_info.ino(),
    };
    publish(&root.join(CREATION), &original)?;
    let mut connection =
        Connection::open(root.join(DATABASE)).map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(1))
        .map_err(|error| error.to_string())?;
    connection
        .execute_batch("PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;")
        .map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    transaction.execute_batch("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE content_references(reference_id TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE content_operations(operation_id TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE content_objects(content_key TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE content_readers(reader_id TEXT PRIMARY KEY,record TEXT NOT NULL); PRAGMA user_version=2;").map_err(|error|error.to_string())?;
    transaction
        .execute(
            "INSERT INTO metadata VALUES ('identity',?1)",
            [serde_json::to_string(&original).map_err(|error| error.to_string())?],
        )
        .map_err(|error| error.to_string())?;
    transaction
        .execute(
            "INSERT INTO metadata VALUES ('layout',?1)",
            [if reclamation {
                REPLICA_CONTENT_GC_LAYOUT_SCHEMA
            } else if snapshot_receives {
                REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA
            } else if streams {
                REPLICA_CONTENT_STREAM_LAYOUT_SCHEMA
            } else if producers {
                REPLICA_CONTENT_PRODUCER_LAYOUT_SCHEMA
            } else {
                REPLICA_CONTENT_LAYOUT_SCHEMA
            }],
        )
        .map_err(|error| error.to_string())?;
    for name in [
        "reference_ids",
        "operation_ids",
        "content_keys",
        "reader_ids",
    ] {
        write_head(&transaction, name, &BTreeSet::new())?;
    }
    if producers {
        super::producers::initialize(&transaction, BTreeSet::new())?;
        transaction
            .execute_batch("PRAGMA user_version=3")
            .map_err(|error| error.to_string())?;
    }
    if streams {
        super::streams::initialize(&transaction)?;
        transaction
            .execute_batch("PRAGMA user_version=4")
            .map_err(|error| error.to_string())?;
    }
    if snapshot_receives {
        super::snapshot_receive::initialize(&transaction, false)?;
        transaction
            .execute_batch("PRAGMA user_version=6")
            .map_err(|e| e.to_string())?;
    }
    if reclamation {
        super::gc::initialize(&transaction)?;
        transaction
            .execute_batch("PRAGMA user_version=7")
            .map_err(|e| e.to_string())?;
    }
    transaction.commit().map_err(|error| error.to_string())?;
    for name in ["content-v1", "staging-v1"] {
        fs::DirBuilder::new()
            .mode(0o700)
            .create(root.join(name))
            .map_err(|error| error.to_string())?;
    }
    publish(&root.join("blobs"), &original)?;
    publish(&root.join(HEADER), &original)?;
    validate_version(
        &connection,
        &original,
        if reclamation {
            7
        } else if snapshot_receives {
            6
        } else if streams {
            4
        } else if producers {
            3
        } else {
            2
        },
    )?;
    Ok(ReplicaContentStore {
        inner: Arc::new(ContentInner {
            validation_witness: Mutex::new(None),
            snapshot_closes: Arc::new(super::close_queue::SnapshotCloseQueue::default()),
            root: root.into(),
            identity: original,
            connection: Mutex::new(Some(connection)),
            writer,
            database,
            producers,
            streams,
            snapshot_receives,
            reclamation,
            restore_binding: None,
            stream_capacity: Arc::new(tokio::sync::Semaphore::new(8)),
            stream_jobs: Arc::new(tokio::sync::Semaphore::new(8)),
            historical_catchup: Arc::new(tokio::sync::Semaphore::new(1)),
        }),
    })
}

/// Reopens only the original actual root/database; native SQL rollback follows both writer leases.
pub(super) fn open(
    root: &Path,
    profile_identity: &str,
    member: u64,
    mode: ContentOpenMode,
) -> Result<ReplicaContentStore, String> {
    open_bound(root, profile_identity, member, mode, None)
}

/// Separately admits explicitly restored physical backing; original open never borrows that contract.
pub(super) fn open_bound(
    root: &Path,
    profile_identity: &str,
    member: u64,
    mode: ContentOpenMode,
    restore_binding: Option<&str>,
) -> Result<ReplicaContentStore, String> {
    directory(root)?;
    let writer = locked(regular(&root.join(".writer.lock"), true)?)?;
    let database = locked(regular(&root.join(DATABASE), true)?)?;
    let original = identity(&root.join(HEADER))?;
    let root_info = fs::metadata(root).map_err(|error| error.to_string())?;
    let database_info = database.metadata().map_err(|error| error.to_string())?;
    if original.schema != REPLICA_CONTENT_STORAGE_SCHEMA
        || original.profile_identity != profile_identity
        || original.member != member
        || original
            .store_owner
            .strip_prefix("replica-content:")
            .is_none_or(|value| !crate::content::hex_digest(value))
        || (restore_binding.is_none()
            && (original.root_device != root_info.dev()
                || original.root_inode != root_info.ino()
                || original.database_device != database_info.dev()
                || original.database_inode != database_info.ino()))
        || database_info.len() > MAX_DATABASE_BYTES
        || identity(&root.join(CREATION))? != original
        || identity(&root.join("blobs"))? != original
    {
        return Err(
            "original replica content identity/profile/member/backing inode conflicts".into(),
        );
    }
    let restored_head = restore_binding
        .map(|digest| super::restore::validate_backing(root, &original, digest))
        .transpose()?;
    for name in ["content-v1", "staging-v1"] {
        directory(&root.join(name))?;
    }
    let mut header = [0_u8; 20];
    regular(&root.join(DATABASE), false)?
        .read_exact(&mut header)
        .map_err(|error| error.to_string())?;
    if &header[..16] != b"SQLite format 3\0"
        || header[18] != 1
        || header[19] != 1
        || root.join(format!("{DATABASE}-wal")).exists()
        || root.join(format!("{DATABASE}-shm")).exists()
    {
        return Err("original content SQL requires its native DELETE journal before open".into());
    }
    let mut connection = Connection::open_with_flags(
        root.join(DATABASE),
        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE,
    )
    .map_err(|error| error.to_string())?;
    connection
        .busy_timeout(std::time::Duration::from_secs(1))
        .map_err(|error| error.to_string())?;
    connection
        .execute_batch("PRAGMA synchronous=FULL;")
        .map_err(|error| error.to_string())?;
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if version == 1 {
        if !matches!(mode, ContentOpenMode::ReaderUpgrade) {
            return Err("original content v0.1 requires explicit reader-scope upgrade".into());
        }
        validate_version(&connection, &original, 1)?;
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .map_err(|error| {
                format!("original reader-scope connections are not quiescent: {error}")
            })?;
        transaction
            .execute(
                "INSERT INTO metadata VALUES ('layout',?1)",
                [REPLICA_CONTENT_LAYOUT_SCHEMA],
            )
            .map_err(|error| error.to_string())?;
        transaction
            .execute_batch("PRAGMA user_version=2")
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
    }
    let producers = matches!(
        mode,
        ContentOpenMode::Producers
            | ContentOpenMode::ProducerUpgrade
            | ContentOpenMode::Streams
            | ContentOpenMode::StreamUpgrade
            | ContentOpenMode::Snapshots
            | ContentOpenMode::SnapshotUpgrade
            | ContentOpenMode::Reclamation
            | ContentOpenMode::ReclamationUpgrade
    );
    let streams = matches!(
        mode,
        ContentOpenMode::Streams
            | ContentOpenMode::StreamUpgrade
            | ContentOpenMode::Snapshots
            | ContentOpenMode::SnapshotUpgrade
            | ContentOpenMode::Reclamation
            | ContentOpenMode::ReclamationUpgrade
    );
    let snapshot_receives = matches!(
        mode,
        ContentOpenMode::Snapshots
            | ContentOpenMode::SnapshotUpgrade
            | ContentOpenMode::Reclamation
            | ContentOpenMode::ReclamationUpgrade
    );
    let reclamation = matches!(
        mode,
        ContentOpenMode::Reclamation | ContentOpenMode::ReclamationUpgrade
    );
    if snapshot_receives && restore_binding.is_none() && root.join(super::restore::FILE).exists() {
        return Err("original Snapshot Root contains an undeclared restore binding".into());
    }
    if version == 2 && matches!(mode, ContentOpenMode::ProducerUpgrade) {
        validate_version(&connection, &original, 2)?;
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .map_err(|error| {
                format!("original producer-profile connections are not quiescent: {error}")
            })?;
        let mut legacy = BTreeSet::new();
        for id in head(&transaction, "reference_ids")? {
            let original = reference_record(&transaction, &id)?
                .ok_or("original legacy producer reference disappeared")?;
            if matches!(
                original.reference.kind,
                ReplicaContentReferenceKind::Log
                    | ReplicaContentReferenceKind::View
                    | ReplicaContentReferenceKind::Snapshot
            ) {
                legacy.insert(id);
            }
        }
        super::producers::initialize(&transaction, legacy)?;
        transaction
            .execute(
                "UPDATE metadata SET value=?1 WHERE key='layout'",
                [REPLICA_CONTENT_PRODUCER_LAYOUT_SCHEMA],
            )
            .map_err(|error| error.to_string())?;
        transaction
            .execute_batch("PRAGMA user_version=3")
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
    }
    if version == 3 && matches!(mode, ContentOpenMode::StreamUpgrade) {
        validate_version(&connection, &original, 3)?;
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .map_err(|error| {
                format!("original stream-profile connections are not quiescent: {error}")
            })?;
        super::streams::initialize(&transaction)?;
        transaction
            .execute(
                "UPDATE metadata SET value=?1 WHERE key='layout'",
                [REPLICA_CONTENT_STREAM_LAYOUT_SCHEMA],
            )
            .map_err(|error| error.to_string())?;
        transaction
            .execute_batch("PRAGMA user_version=4")
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
    }
    if matches!(mode, ContentOpenMode::SnapshotUpgrade) && matches!(version, 4 | 5) {
        validate_version(&connection, &original, version)?;
        if (version == 5) != restore_binding.is_some() {
            return Err("Snapshot upgrade changed original backing interpretation".into());
        }
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .map_err(|e| format!("original Snapshot connections are not quiescent: {e}"))?;
        super::snapshot_receive::initialize(&transaction, restore_binding.is_some())?;
        transaction
            .execute(
                "UPDATE metadata SET value=?1 WHERE key='layout'",
                [REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA],
            )
            .map_err(|e| e.to_string())?;
        transaction
            .execute_batch("PRAGMA user_version=6")
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
    }
    if matches!(mode, ContentOpenMode::ReclamationUpgrade) && version == 6 {
        validate_version(&connection, &original, 6)?;
        let backing: String = connection
            .query_row(
                "SELECT value FROM metadata WHERE key='backing_mode'",
                [],
                |row| row.get(0),
            )
            .map_err(|e| e.to_string())?;
        if backing
            != if restore_binding.is_some() {
                "restored"
            } else {
                "original"
            }
        {
            return Err("GC upgrade changed original backing interpretation".into());
        }
        let transaction = connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Exclusive)
            .map_err(|e| format!("original GC connections are not drained: {e}"))?;
        super::gc::initialize(&transaction)?;
        transaction
            .execute(
                "UPDATE metadata SET value=?1 WHERE key='layout'",
                [REPLICA_CONTENT_GC_LAYOUT_SCHEMA],
            )
            .map_err(|e| e.to_string())?;
        transaction
            .execute_batch("PRAGMA user_version=7")
            .map_err(|e| e.to_string())?;
        transaction.commit().map_err(|e| e.to_string())?;
    }
    validate_version(
        &connection,
        &original,
        if reclamation {
            7
        } else if snapshot_receives {
            6
        } else if restore_binding.is_some() {
            5
        } else if streams {
            4
        } else if producers {
            3
        } else {
            2
        },
    )?;
    if snapshot_receives {
        let backing: String = connection
            .query_row(
                "SELECT value FROM metadata WHERE key='backing_mode'",
                [],
                |row| row.get(0),
            )
            .map_err(|e| e.to_string())?;
        if backing
            != if restore_binding.is_some() {
                "restored"
            } else {
                "original"
            }
        {
            return Err(
                "Snapshot receiving backing differs from its explicit original/restored mode"
                    .into(),
            );
        }
    }
    if let Some(expected) = restored_head
        && super::restore::validate_sql(&connection, &original)? != expected
    {
        return Err("actual restored binding differs from original SQL head".into());
    }
    Ok(ReplicaContentStore {
        inner: Arc::new(ContentInner {
            validation_witness: Mutex::new(None),
            snapshot_closes: Arc::new(super::close_queue::SnapshotCloseQueue::default()),
            root: root.into(),
            identity: original,
            connection: Mutex::new(Some(connection)),
            writer,
            database,
            producers,
            streams,
            snapshot_receives,
            reclamation,
            restore_binding: restore_binding.map(str::to_owned),
            stream_capacity: Arc::new(tokio::sync::Semaphore::new(8)),
            stream_jobs: Arc::new(tokio::sync::Semaphore::new(8)),
            historical_catchup: Arc::new(tokio::sync::Semaphore::new(1)),
        }),
    })
}

/// Reads one exact mandatory complete head; a lost head never becomes an empty producer set.
pub(super) fn head(connection: &Connection, name: &str) -> Result<BTreeSet<String>, String> {
    let raw: String = connection
        .query_row("SELECT value FROM metadata WHERE key=?", [name], |row| {
            row.get(0)
        })
        .map_err(|error| {
            format!("original content mandatory head {name} is unavailable: {error}")
        })?;
    if raw.len() > 8 * 1024 * 1024 {
        return Err("original content inventory head exceeds bound".into());
    }
    let values: Vec<String> = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    if values.len() > MAX_REFERENCES
        || values.windows(2).any(|values| values[0] >= values[1])
        || values
            .iter()
            .any(|value| value.is_empty() || value.len() > 512)
    {
        return Err("original content inventory head is invalid".into());
    }
    Ok(values.into_iter().collect())
}

/// Writes the complete same-transaction head beside every new original journal row.
pub(super) fn write_head(
    connection: &Connection,
    name: &str,
    values: &BTreeSet<String>,
) -> Result<(), String> {
    let raw = serde_json::to_string(values).map_err(|error| error.to_string())?;
    if values.len() > MAX_REFERENCES || raw.len() > 8 * 1024 * 1024 {
        return Err("original content inventory reached its fixed bound".into());
    }
    connection.execute("INSERT INTO metadata(key,value) VALUES (?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",params![name,raw]).map_err(|error|error.to_string())?;
    Ok(())
}

/// Checks exact prior/current generation before an explicit original-writer-drained SQL transition.
pub(super) fn validate_version(
    connection: &Connection,
    original: &ReplicaContentIdentity,
    expected_version: i64,
) -> Result<(), String> {
    within_budget(connection)?;
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let mode: String = connection
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let mut statement = connection
        .prepare("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name")
        .map_err(|error| error.to_string())?;
    let actual = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    let mut expected = vec![
        ("content_objects".into(), "table".into()),
        ("content_operations".into(), "table".into()),
        ("content_readers".into(), "table".into()),
        ("content_references".into(), "table".into()),
        ("metadata".into(), "table".into()),
    ];
    if expected_version >= 3 {
        expected.insert(2, ("content_producers".into(), "table".into()));
    }
    if expected_version >= 4 {
        expected.insert(
            expected.len() - 1,
            ("content_streams".into(), "table".into()),
        );
    }
    if expected_version >= 6 {
        expected.push(("content_snapshot_receives".into(), "table".into()));
        expected.sort();
    }
    if expected_version == 7 {
        expected.extend([
            ("content_gc_commands".into(), "table".into()),
            ("content_tombstones".into(), "table".into()),
            ("content_stream_retirements".into(), "table".into()),
        ]);
        expected.sort();
    }
    if version != expected_version || mode != "delete" || actual != expected {
        return Err("original replica content SQL format/tables/triggers differ".into());
    }
    let mut tables = vec![
        ("metadata", "key"),
        ("content_references", "reference_id"),
        ("content_operations", "operation_id"),
        ("content_readers", "reader_id"),
        ("content_objects", "content_key"),
    ];
    if expected_version >= 3 {
        tables.push(("content_producers", "operation_id"));
    }
    if expected_version >= 4 {
        tables.push(("content_streams", "stream_id"));
    }
    if expected_version >= 6 {
        tables.push(("content_snapshot_receives", "operation_id"));
    }
    if expected_version == 7 {
        tables.extend([
            ("content_gc_commands", "operation_id"),
            ("content_tombstones", "content_key"),
            ("content_stream_retirements", "stream_id"),
        ]);
    }
    for (table, key) in tables {
        let mut statement = connection
            .prepare(&format!("PRAGMA table_info({table})"))
            .map_err(|error| error.to_string())?;
        let columns = statement
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, i64>(5)?,
                ))
            })
            .map_err(|error| error.to_string())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())?;
        let mut expected_columns = vec![
            (key.into(), "TEXT".into(), 0, 1),
            (
                (if table == "metadata" {
                    "value"
                } else {
                    "record"
                })
                .into(),
                "TEXT".into(),
                1,
                0,
            ),
        ];
        if table == "content_streams" {
            expected_columns.insert(1, ("request_digest".into(), "TEXT".into(), 1, 0));
        }
        if columns != expected_columns {
            return Err("original content SQL columns/keys differ".into());
        }
    }
    let raw: String = connection
        .query_row(
            "SELECT value FROM metadata WHERE key='identity'",
            [],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    if raw.len() as u64 > MAX_RECORD_BYTES
        || serde_json::from_str::<ReplicaContentIdentity>(&raw)
            .map_err(|error| error.to_string())?
            != *original
    {
        return Err("original replica content SQL identity differs".into());
    }
    let mut inventories = vec![
        ("reference_ids", "content_references", "reference_id"),
        ("operation_ids", "content_operations", "operation_id"),
        ("content_keys", "content_objects", "content_key"),
        ("reader_ids", "content_readers", "reader_id"),
    ];
    if expected_version >= 3 {
        inventories.push(("producer_ids", "content_producers", "operation_id"));
    }
    if expected_version >= 4 {
        inventories.push(("stream_ids", "content_streams", "stream_id"));
    }
    if expected_version >= 6 {
        inventories.push((
            "snapshot_receive_ids",
            "content_snapshot_receives",
            "operation_id",
        ));
    }
    if expected_version == 7 {
        inventories.extend([
            ("gc_ids", "content_gc_commands", "operation_id"),
            ("tombstone_keys", "content_tombstones", "content_key"),
            (
                "stream_retirement_ids",
                "content_stream_retirements",
                "stream_id",
            ),
        ]);
    }
    for (name, table, column) in inventories {
        let heads = head(connection, name)?;
        let mut statement = connection
            .prepare(&format!(
                "SELECT {column} FROM {table} ORDER BY {column} LIMIT 65537"
            ))
            .map_err(|error| error.to_string())?;
        let rows = statement
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|error| error.to_string())?
            .collect::<Result<BTreeSet<_>, _>>()
            .map_err(|error| error.to_string())?;
        if rows != heads {
            return Err("original content mandatory head lost or changed journal rows".into());
        }
        if name == "reference_ids" {
            for id in heads {
                let reference = reference_record(connection, &id)?
                    .ok_or("original content reference row missing")?;
                if reference.reference.kind == ReplicaContentReferenceKind::Stream
                    && expected_version < 4
                {
                    return Err("old content profile contains hidden stream authority".into());
                }
                if reference.reference.kind == ReplicaContentReferenceKind::SnapshotReceive
                    && expected_version < 6
                {
                    return Err(
                        "old content profile contains hidden Snapshot receive authority".into(),
                    );
                }
            }
        }
    }
    let count: i64 = connection
        .query_row("SELECT COUNT(*) FROM metadata", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if count
        != match expected_version {
            1 => 5,
            2 => 6,
            3 => 8,
            4 => 9,
            5 => 10,
            6 | 7 => {
                let mode: String = connection
                    .query_row(
                        "SELECT value FROM metadata WHERE key='backing_mode'",
                        [],
                        |row| row.get(0),
                    )
                    .map_err(|e| e.to_string())?;
                match mode.as_str() {
                    "original" => {
                        if expected_version == 7 {
                            14
                        } else {
                            11
                        }
                    }
                    "restored" => {
                        if expected_version == 7 {
                            15
                        } else {
                            12
                        }
                    }
                    _ => return Err("Snapshot backing mode is invalid".into()),
                }
            }
            _ => return Err("unknown original content storage generation".into()),
        }
    {
        return Err("original content mandatory metadata changed".into());
    }
    if expected_version != 1 {
        let layout: String = connection
            .query_row("SELECT value FROM metadata WHERE key='layout'", [], |row| {
                row.get(0)
            })
            .map_err(|error| error.to_string())?;
        if layout
            != if expected_version == 7 {
                REPLICA_CONTENT_GC_LAYOUT_SCHEMA
            } else if expected_version == 6 {
                REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA
            } else if expected_version == 5 {
                REPLICA_CONTENT_RESTORE_LAYOUT_SCHEMA
            } else if expected_version == 4 {
                REPLICA_CONTENT_STREAM_LAYOUT_SCHEMA
            } else if expected_version == 3 {
                REPLICA_CONTENT_PRODUCER_LAYOUT_SCHEMA
            } else {
                REPLICA_CONTENT_LAYOUT_SCHEMA
            }
        {
            return Err("original content reader-scope layout marker differs".into());
        }
    }
    super::copy::validate_records(connection, original)?;
    super::reader::validate_records(connection, original)?;
    if expected_version >= 3 {
        super::producers::validate_records(connection, original)?;
    }
    if expected_version >= 4 {
        super::streams::validate_records(connection, original)?;
    }
    if expected_version >= 6 {
        super::snapshot_receive::validate_records(connection, original)?;
    }
    if expected_version == 7 {
        super::gc::validate_records(connection, original)?;
        super::streams::validate_retirements(connection)?;
    }
    let restored_six: bool = expected_version >= 6
        && connection
            .query_row(
                "SELECT value FROM metadata WHERE key='backing_mode'",
                [],
                |row| row.get::<_, String>(0),
            )
            .map_err(|e| e.to_string())?
            == "restored";
    if expected_version == 5 || restored_six {
        super::restore::validate_sql(connection, original)?;
    }
    Ok(())
}

/// Refuses metadata growth in the original transaction before an over-bound state can commit.
pub(super) fn within_budget(connection: &Connection) -> Result<(), String> {
    let pages: u64 = connection
        .query_row("PRAGMA page_count", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let size: u64 = connection
        .query_row("PRAGMA page_size", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if size == 0
        || pages
            .checked_mul(size)
            .is_none_or(|bytes| bytes > MAX_DATABASE_BYTES)
    {
        return Err("original content metadata exceeds its fixed database bound".into());
    }
    Ok(())
}
