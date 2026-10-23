//! Reuses only a full validation of the unchanged original private SQLite connection.

use super::*;

/// Ephemeral original-connection counters; never serialized or imported as authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Witness {
    /// Includes original-connection writes even if the transaction was later rolled back.
    changes: u64,
    /// Changes committed by other actual SQLite connections.
    data_version: u64,
    /// Includes same-connection DDL which does not increment total_changes.
    schema_version: u64,
    /// Explicit old-writer generation remains independently sampled.
    user_version: i64,
    /// Original DELETE journal cannot silently switch to another locking contract.
    journal_mode: String,
}

impl Witness {
    /// Samples actual current SQLite state without substituting a cached ready flag or caller evidence.
    fn sample(connection: &Connection) -> Result<Self, String> {
        Ok(Self {
            changes: connection.total_changes(),
            data_version: connection
                .query_row("PRAGMA data_version", [], |r| r.get(0))
                .map_err(|e| e.to_string())?,
            schema_version: connection
                .query_row("PRAGMA schema_version", [], |r| r.get(0))
                .map_err(|e| e.to_string())?,
            user_version: connection
                .query_row("PRAGMA user_version", [], |r| r.get(0))
                .map_err(|e| e.to_string())?,
            journal_mode: connection
                .query_row("PRAGMA journal_mode", [], |r| r.get(0))
                .map_err(|e| e.to_string())?,
        })
    }
}

/// Validates only the one connection retained by this actual inner owner; callers hold its mutex.
pub(super) fn validate(inner: &ContentInner, connection: &Connection) -> Result<(), String> {
    layout::within_budget(connection)?;
    let restored_head = inner
        .restore_binding
        .as_deref()
        .map(|digest| super::restore::validate_backing(&inner.root, &inner.identity, digest))
        .transpose()?;
    let version = if inner.restore_binding.is_some() {
        5
    } else if inner.streams {
        4
    } else if inner.producers {
        3
    } else {
        2
    };
    let mut witness = inner
        .validation_witness
        .lock()
        .map_err(|_| "original Root validation witness unavailable")?;
    if !connection.is_autocommit() {
        *witness = None;
        layout::validate_version(connection, &inner.identity, version)?;
        if let Some(expected) = restored_head
            && super::restore::validate_sql(connection, &inner.identity)? != expected
        {
            return Err("current transactional restored file/SQL binding differs".into());
        }
        return Ok(());
    }
    let before = Witness::sample(connection)?;
    if witness.as_ref() == Some(&before) {
        return Ok(());
    }
    *witness = None;
    let transaction = connection
        .unchecked_transaction()
        .map_err(|e| e.to_string())?;
    layout::validate_version(&transaction, &inner.identity, version)?;
    if let Some(expected) = restored_head
        && super::restore::validate_sql(&transaction, &inner.identity)? != expected
    {
        return Err("current restored file/SQL binding differs".into());
    }
    transaction.commit().map_err(|e| e.to_string())?;
    let after = Witness::sample(connection)?;
    if before != after {
        return Err("original Root changed during complete validation".into());
    }
    *witness = Some(after);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Creates one actual file/SQL Root and primes only its complete structural validation.
    fn root(path: &Path) -> ReplicaContentStore {
        let root = ReplicaContentStore::create_with_streams(path, &"a".repeat(64), 1).unwrap();
        assert!(
            root.reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
        root
    }

    /// An unmanaged actual SQL writer cannot borrow a previously validated complete head.
    #[test]
    fn external_sql_corruption_invalidates_original_validation() {
        let temporary = tempfile::tempdir().unwrap();
        let root = root(&temporary.path().join("root"));
        let sql = Connection::open(root.root().join("content.sqlite3")).unwrap();
        sql.execute("DELETE FROM metadata WHERE key='reader_ids'", [])
            .unwrap();
        assert!(root.reader_status("original-absent-reader").is_err());
        sql.execute(
            "INSERT INTO metadata(key,value) VALUES('reader_ids','[]')",
            [],
        )
        .unwrap();
        assert!(
            root.reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
    }

    /// Same-connection SQL writes and DDL must invalidate independently of external data_version.
    #[test]
    fn own_sql_and_schema_changes_never_borrow_previous_validation() {
        let temporary = tempfile::tempdir().unwrap();
        let root = root(&temporary.path().join("root"));
        {
            let guard = root.inner.connection.lock().unwrap();
            let sql = guard.as_ref().unwrap();
            sql.execute(
                "UPDATE metadata SET value='invalid' WHERE key='reader_ids'",
                [],
            )
            .unwrap();
        }
        assert!(root.reader_status("original-absent-reader").is_err());
        {
            let guard = root.inner.connection.lock().unwrap();
            guard
                .as_ref()
                .unwrap()
                .execute("UPDATE metadata SET value='[]' WHERE key='reader_ids'", [])
                .unwrap();
        }
        assert!(
            root.reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
        {
            let guard = root.inner.connection.lock().unwrap();
            guard
                .as_ref()
                .unwrap()
                .execute_batch("CREATE TABLE unaccounted_authority(original TEXT)")
                .unwrap();
        }
        assert!(root.reader_status("original-absent-reader").is_err());
    }

    /// A generation pragma and in-flight transaction cannot become an unchanged successful witness.
    #[test]
    fn version_and_uncommitted_changes_refuse_reuse() {
        let temporary = tempfile::tempdir().unwrap();
        let root = root(&temporary.path().join("root"));
        {
            let guard = root.inner.connection.lock().unwrap();
            let sql = guard.as_ref().unwrap();
            sql.execute_batch("PRAGMA user_version=3").unwrap();
        }
        assert!(root.reader_status("original-absent-reader").is_err());
        {
            let guard = root.inner.connection.lock().unwrap();
            let sql = guard.as_ref().unwrap();
            sql.execute_batch(
                "PRAGMA user_version=4; BEGIN; DELETE FROM metadata WHERE key='reader_ids'",
            )
            .unwrap();
            assert!(root.validate_connection(sql).is_err());
            sql.execute_batch("ROLLBACK").unwrap();
        }
        assert!(
            root.reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
        let path = root.root().to_path_buf();
        drop(root);
        let reopened = ReplicaContentStore::open_with_streams(&path, &"a".repeat(64), 1).unwrap();
        assert!(reopened.inner.validation_witness.lock().unwrap().is_none());
        assert!(
            reopened
                .reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
    }
}
