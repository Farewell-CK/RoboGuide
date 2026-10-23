//! Actual SQL/inode/generation refusal contracts; synthetic member data is never a network proof.

use super::*;
use openraft::{CommittedLeaderId, Membership};

/// Creates one actual original Root4/parent7 without starting any network, Controller or Node.
fn original() -> (tempfile::TempDir, ReplicaContentStore, SqliteRaftStore) {
    let directory = tempfile::tempdir().unwrap();
    let root = ReplicaContentStore::create_with_streams(
        &directory.path().join("content"),
        &"a".repeat(64),
        31,
    )
    .unwrap();
    let store = SqliteRaftStore::open_with_network_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root.clone(),
    )
    .unwrap();
    (directory, root, store)
}

/// Empty new authority fences old writers and requires its original marker before any operation.
#[test]
fn actual_network_parent_requires_explicit_generation_and_original_empty_marker() {
    let (directory, root, store) = original();
    let path = directory.path().join("parent.sqlite3");
    let identity = store._writer.identity().unwrap();
    assert_eq!(
        store
            .connection
            .lock()
            .unwrap()
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        7
    );
    drop(store);
    assert!(
        SqliteRaftStore::open(
            &path,
            &"a".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        )
        .is_err()
    );
    assert!(
        SqliteRaftStore::open_with_content(
            &path,
            &"a".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            root.clone()
        )
        .is_err()
    );
    let reopened = SqliteRaftStore::open_with_network_content(
        &path,
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root.clone(),
    )
    .unwrap();
    assert_eq!(reopened._writer.identity().unwrap(), identity);
    reopened
        .connection
        .lock()
        .unwrap()
        .execute(
            "DELETE FROM replica_v1_meta WHERE key=?1",
            [NETWORK_CONTENT_KEY],
        )
        .unwrap();
    drop(reopened);
    let error = SqliteRaftStore::open_with_network_content(
        &path,
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root,
    );
    assert!(
        error
            .err()
            .unwrap()
            .contains("mandatory original network content marker")
    );
}

/// A false original local owner is refused before log or member rows change.
#[test]
fn actual_network_parent_refuses_wrong_local_owner_before_log_admission() {
    let (_directory, root, store) = original();
    let data =
        ReplicaMember::with_content_store("127.0.0.1:24317", root.identity().store_owner.clone())
            .unwrap();
    let mut membership = Membership::new(vec![BTreeSet::from([31])], BTreeMap::from([(31, data)]));
    store
        .persist_log_entries(&[Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 31), 1),
            payload: EntryPayload::Membership(membership.clone()),
        }])
        .unwrap();
    let before: String = store
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT data FROM replica_v1_log WHERE log_index=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    let wrong = ReplicaMember::with_content_store(
        "127.0.0.1:24317",
        "replica-content:".to_string() + &"b".repeat(64),
    )
    .unwrap();
    membership = Membership::new(vec![BTreeSet::from([31])], BTreeMap::from([(31, wrong)]));
    assert!(
        store
            .persist_log_entries(&[Entry {
                log_id: LogId::new(CommittedLeaderId::new(1, 31), 2),
                payload: EntryPayload::Membership(membership)
            }])
            .is_err()
    );
    let sql = store.connection.lock().unwrap();
    assert_eq!(
        sql.query_row("SELECT COUNT(*) FROM replica_v1_log", [], |row| row
            .get::<_, u64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        sql.query_row(
            "SELECT data FROM replica_v1_log WHERE log_index=1",
            [],
            |row| row.get::<_, String>(0)
        )
        .unwrap(),
        before
    );
}

/// Original committed WAL evidence remains readable while the actual parent application mutex is held.
#[test]
fn actual_readonly_network_authority_avoids_parent_mutex_and_refuses_replaced_sql_inode() {
    let (directory, root, store) = original();
    let pins = BTreeMap::from([
        (31, "sha256:".to_string() + &"c".repeat(64)),
        (37, "sha256:".to_string() + &"d".repeat(64)),
        (41, "sha256:".to_string() + &"e".repeat(64)),
    ]);
    store.initialize_certificate_trust(&pins).unwrap();
    let member =
        ReplicaMember::with_content_store("127.0.0.1:24317", root.identity().store_owner.clone())
            .unwrap();
    let membership = StoredMembership::new(
        Some(LogId::new(CommittedLeaderId::new(1, 31), 1)),
        Membership::new(vec![BTreeSet::from([31])], BTreeMap::from([(31, member)])),
    );
    {
        let sql = store.connection.lock().unwrap();
        write_meta(&sql, "membership", &membership).unwrap();
        let (trust, actual) = store.try_network_authority().unwrap();
        assert_eq!(actual, membership);
        assert_eq!(trust.bootstrap, pins);
    }
    let original = directory.path().join("parent.sqlite3");
    let moved = directory.path().join("actual-original-held.sqlite3");
    std::fs::rename(&original, &moved).unwrap();
    std::fs::copy(&moved, &original).unwrap();
    assert!(
        store
            .try_network_authority()
            .unwrap_err()
            .contains("backing changed")
    );
    std::fs::remove_file(&original).unwrap();
    std::fs::rename(moved, original).unwrap();
}
