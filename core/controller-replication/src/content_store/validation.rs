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

    /// Matches only the declared actual row writes, with all independent SQLite headers unchanged.
    fn after_reader_writes(&self, after: &Self, writes: u64) -> bool {
        self.changes.checked_add(writes) == Some(after.changes)
            && self.data_version == after.data_version
            && self.schema_version == after.schema_version
            && self.user_version == after.user_version
            && self.journal_mode == after.journal_mode
    }
}

/// Closed managed reader transactions; arbitrary writers cannot assert a successful validation delta.
pub(super) enum ReaderChange {
    /// One original reader/pin insert and both complete inventory updates.
    Admission,
    /// One original reader result and its exact independent release pin.
    Closure,
}

/// Single-use ephemeral old validation; error/rollback cannot leave a successful mutation witness.
pub(super) struct ReaderMutation {
    /// Complete validation of this exact original connection before the actual transaction.
    before: Option<Witness>,
    /// Exact declared statement effects, including all actual trigger/cascade changes.
    writes: u64,
}

impl ReaderMutation {
    /// Removes the old witness before effects, retaining it only for an exact unchanged connection.
    pub(super) fn begin(
        inner: &ContentInner,
        connection: &Connection,
        change: ReaderChange,
    ) -> Result<Self, String> {
        let actual = connection
            .is_autocommit()
            .then(|| Witness::sample(connection))
            .transpose()?;
        let previous = inner
            .validation_witness
            .lock()
            .map_err(|_| "original Root validation witness unavailable")?
            .take();
        Ok(Self {
            before: previous.filter(|before| Some(before) == actual.as_ref()),
            writes: match change {
                ReaderChange::Admission => 4,
                ReaderChange::Closure => 2,
            },
        })
    }

    /// Verifies actual post-Commit rows in one snapshot before extending only the prior full proof.
    pub(super) fn committed(
        self,
        inner: &ContentInner,
        connection: &Connection,
        verify: impl FnOnce(&Connection) -> Result<(), String>,
    ) -> Result<(), String> {
        if !connection.is_autocommit() {
            return Err(
                "managed reader validation requires its actual completed SQL Commit".into(),
            );
        }
        let observed = Witness::sample(connection)?;
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| error.to_string())?;
        verify(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        let after = Witness::sample(connection)?;
        if observed == after
            && self
                .before
                .is_some_and(|before| before.after_reader_writes(&after, self.writes))
        {
            let mut witness = inner
                .validation_witness
                .lock()
                .map_err(|_| "original Root validation witness unavailable")?;
            if witness.is_some() {
                return Err(
                    "original Root validation changed inside its managed reader write".into(),
                );
            }
            *witness = Some(after);
        }
        Ok(())
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
    let version = if inner.staging_gc {
        8
    } else if inner.reclamation {
        7
    } else if inner.snapshot_receives {
        6
    } else if inner.restore_binding.is_some() {
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

    /// Completes an actual copied-file reader before checking old-history corruption fences.
    fn root(path: &Path) -> (ReplicaContentStore, ArtifactContentKey) {
        let root = ReplicaContentStore::create_with_streams(path, &"a".repeat(64), 1).unwrap();
        let source_directory = path.with_extension("source");
        std::fs::create_dir(&source_directory).unwrap();
        let (_log, source, key) = crate::content_application_tests::original(&source_directory);
        root.copy_from_artifact(
            &source,
            &key,
            "original-validation-copy",
            &"a".repeat(64),
            30000,
        )
        .unwrap();
        let mut reader = root
            .open_reader(&key, "original-validation-reader", &"a".repeat(64), 30000)
            .unwrap();
        std::io::copy(&mut reader, &mut std::io::sink()).unwrap();
        assert!(reader.finish().unwrap().complete);
        assert!(
            root.reader_status("original-absent-reader")
                .unwrap()
                .is_none()
        );
        (root, key)
    }

    /// An unmanaged actual SQL writer cannot borrow a previously validated complete head.
    #[test]
    fn external_sql_corruption_invalidates_original_validation() {
        let temporary = tempfile::tempdir().unwrap();
        let (root, _) = root(&temporary.path().join("root"));
        let sql = Connection::open(root.root().join("content.sqlite3")).unwrap();
        let original_head: String = sql
            .query_row(
                "SELECT value FROM metadata WHERE key='reader_ids'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        sql.execute("DELETE FROM metadata WHERE key='reader_ids'", [])
            .unwrap();
        assert!(root.reader_status("original-absent-reader").is_err());
        sql.execute(
            "INSERT INTO metadata(key,value) VALUES('reader_ids',?1)",
            [original_head],
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
        let (root, _) = root(&temporary.path().join("root"));
        let original_head: String;
        {
            let guard = root.inner.connection.lock().unwrap();
            let sql = guard.as_ref().unwrap();
            original_head = sql
                .query_row(
                    "SELECT value FROM metadata WHERE key='reader_ids'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
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
                .execute(
                    "UPDATE metadata SET value=?1 WHERE key='reader_ids'",
                    [original_head],
                )
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
        let (root, _) = root(&temporary.path().join("root"));
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

    /// A later managed reader must not hide the loss of an earlier original history row.
    #[test]
    fn managed_reader_changes_never_hide_missing_original_history() {
        let temporary = tempfile::tempdir().unwrap();
        let (root, key) = root(&temporary.path().join("root"));
        let mut reader = root
            .open_reader(&key, "later-original-reader", &"b".repeat(64), 30000)
            .unwrap();
        std::io::copy(&mut reader, &mut std::io::sink()).unwrap();
        assert!(reader.finish().unwrap().complete);
        let sql = Connection::open(root.root().join("content.sqlite3")).unwrap();
        sql.execute(
            "DELETE FROM content_readers WHERE reader_id=?1",
            [super::super::reader::key("original-validation-reader").unwrap()],
        )
        .unwrap();
        assert!(root.reader_status("later-original-reader").is_err());
        assert!(
            root.open_reader(&key, "unadmitted-after-corruption", &"b".repeat(64), 30000)
                .is_err()
        );
    }

    /// A real extra trigger write cannot hide older history behind a later valid reader close.
    #[test]
    fn extra_reader_close_write_cannot_borrow_original_validation() {
        let temporary = tempfile::tempdir().unwrap();
        let (root, key) = root(&temporary.path().join("root"));
        let mut reader = root
            .open_reader(&key, "original-extra-close-write", &"b".repeat(64), 30000)
            .unwrap();
        std::io::copy(&mut reader, &mut std::io::sink()).unwrap();
        {
            let guard = root.inner.connection.lock().unwrap();
            let sql = guard.as_ref().unwrap();
            let original = super::super::reader::key("original-validation-reader").unwrap();
            sql.execute_batch(&format!(
                "CREATE TEMP TRIGGER actual_extra_reader_write AFTER UPDATE ON content_readers \
                 BEGIN DELETE FROM content_readers WHERE reader_id='{original}'; END;"
            ))
            .unwrap();
        }
        assert!(reader.finish().unwrap().complete);
        assert!(root.reader_status("original-extra-close-write").is_err());
        assert!(
            root.open_reader(&key, "unadmitted-after-extra-write", &"b".repeat(64), 30000)
                .is_err()
        );
    }

    /// A real SQLite close failure keeps the original Unknown and pin after the actual FD closes.
    #[test]
    fn managed_reader_close_sql_failure_preserves_original_unknown() {
        let temporary = tempfile::tempdir().unwrap();
        let (root, key) = root(&temporary.path().join("root"));
        let mut reader = root
            .open_reader(&key, "original-failed-close", &"b".repeat(64), 30000)
            .unwrap();
        std::io::copy(&mut reader, &mut std::io::sink()).unwrap();
        let original = root
            .reader_status("original-failed-close")
            .unwrap()
            .unwrap();
        {
            let guard = root.inner.connection.lock().unwrap();
            guard
                .as_ref()
                .unwrap()
                .execute_batch("PRAGMA query_only=ON")
                .unwrap();
        }
        assert!(reader.finish().is_err());
        {
            let guard = root.inner.connection.lock().unwrap();
            guard
                .as_ref()
                .unwrap()
                .execute_batch("PRAGMA query_only=OFF")
                .unwrap();
        }
        assert_eq!(
            root.reader_status("original-failed-close").unwrap(),
            Some(original)
        );
        assert!(
            root.active_references(&key)
                .unwrap()
                .iter()
                .any(|reference| {
                    reference.reference_id
                        == format!(
                            "reader:{}",
                            super::super::reader::key("original-failed-close").unwrap()
                        )
                })
        );
        assert!(
            root.open_reader(&key, "original-failed-close", &"b".repeat(64), 30000)
                .is_err()
        );
    }
}
