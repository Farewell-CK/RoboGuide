//! Exact original-connection data reuse; no live quorum, TLS, byte or permission proof is cached.

use super::*;

/// Original private-connection counters, including rollback, foreign writes and DDL.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Witness {
    /// Includes original-connection writes even when they were subsequently rolled back.
    changes: u64,
    /// Detects commits made through any other actual SQLite connection.
    data_version: u64,
    /// Detects schema changes which do not increment total_changes.
    schema_version: u64,
    /// Retains the original old-writer generation independently of row counters.
    user_version: u64,
    /// Refuses to reuse a frame after the actual journal contract changes.
    journal_mode: String,
}

impl Witness {
    /// Samples actual SQL headers on the one original retained connection.
    fn sample(connection: &Connection) -> Result<Self, String> {
        Ok(Self {
            changes: connection.total_changes(),
            data_version: connection
                .query_row("PRAGMA data_version", [], |row| row.get(0))
                .map_err(|error| error.to_string())?,
            schema_version: connection
                .query_row("PRAGMA schema_version", [], |row| row.get(0))
                .map_err(|error| error.to_string())?,
            user_version: connection
                .query_row("PRAGMA user_version", [], |row| row.get(0))
                .map_err(|error| error.to_string())?,
            journal_mode: connection
                .query_row("PRAGMA journal_mode", [], |row| row.get(0))
                .map_err(|error| error.to_string())?,
        })
    }
}

/// One ephemeral successful frame, shared only by clones which own the same original connection.
#[derive(Default)]
pub(super) struct Cache {
    /// Missing, failed or transactional reads never leave reusable data evidence.
    original: Option<(Witness, ReplicaView)>,
}

/// Reads one consistent frame; every caller still independently checks all live authority and bytes.
pub(super) fn read(connection: &Connection, cache: &mut Cache) -> Result<ReplicaView, String> {
    if !connection.is_autocommit() {
        cache.original = None;
        return read_view(connection).map_err(|error| error.to_string());
    }
    let before = Witness::sample(connection)?;
    if let Some((witness, view)) = &cache.original
        && *witness == before
    {
        let result = view.clone();
        if Witness::sample(connection)? == before {
            return Ok(result);
        }
    }
    cache.original = None;
    let transaction = connection
        .unchecked_transaction()
        .map_err(|error| error.to_string())?;
    let view = read_view(&transaction).map_err(|error| error.to_string())?;
    transaction.commit().map_err(|error| error.to_string())?;
    let after = Witness::sample(connection)?;
    if before == after {
        cache.original = Some((after, view.clone()));
    }
    Ok(view)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Opens actual private SQLite storage without a Raft node, Controller, resource or device.
    fn store(path: &Path) -> SqliteRaftStore {
        SqliteRaftStore::open(
            path,
            "original-view-unit",
            "roboguide.controller-checkpoint/v60",
        )
        .unwrap()
    }

    /// Same-connection writes and a real rollback cannot lend another committed frame.
    #[test]
    fn original_sql_commit_and_rollback_never_reuse_another_frame() {
        let directory = tempfile::tempdir().unwrap();
        let store = store(&directory.path().join("replica.sqlite3"));
        assert_eq!(store.accepted_view().unwrap().revision, 0);
        {
            let sql = store.connection.lock().unwrap();
            write_meta(&sql, "revision", &7_u64).unwrap();
        }
        assert_eq!(store.accepted_view().unwrap().revision, 7);
        {
            let mut sql = store.connection.lock().unwrap();
            let transaction = sql.transaction().unwrap();
            write_meta(&transaction, "revision", &8_u64).unwrap();
            let mut cache = store.view_cache.lock().unwrap();
            assert_eq!(read(&transaction, &mut cache).unwrap().revision, 8);
            transaction.rollback().unwrap();
        }
        assert_eq!(store.accepted_view().unwrap().revision, 7);
    }

    /// An actual independent SQL writer cannot hide changed or corrupt metadata behind a cached view.
    #[test]
    fn foreign_sql_commit_and_corruption_invalidate_original_frame() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("replica.sqlite3");
        let store = store(&path);
        assert_eq!(store.accepted_view().unwrap().revision, 0);
        let foreign = Connection::open(&path).unwrap();
        write_meta(&foreign, "revision", &9_u64).unwrap();
        assert_eq!(store.accepted_view().unwrap().revision, 9);
        write_meta(&foreign, "checkpoint_digest", &"corrupt-original").unwrap();
        assert!(
            store
                .accepted_view()
                .unwrap_err()
                .contains("checkpoint digest differs")
        );
        assert!(store.view_cache.lock().unwrap().original.is_none());
    }

    /// Same-connection DDL changes the data interpretation even when total_changes stays fixed.
    #[test]
    fn schema_replacement_cannot_borrow_a_validated_frame() {
        let directory = tempfile::tempdir().unwrap();
        let store = store(&directory.path().join("replica.sqlite3"));
        store.accepted_view().unwrap();
        {
            let sql = store.connection.lock().unwrap();
            let changes = sql.total_changes();
            sql.execute_batch("ALTER TABLE replica_v1_events RENAME TO hidden_original_events;")
                .unwrap();
            assert_eq!(sql.total_changes(), changes);
        }
        assert!(store.accepted_view().is_err());
        assert!(store.view_cache.lock().unwrap().original.is_none());
    }
}
