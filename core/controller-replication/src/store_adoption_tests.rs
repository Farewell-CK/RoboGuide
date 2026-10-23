//! Actual SQL/CAS upgrade invariants; local storage tests never establish an online quorum.

use super::*;
use crate::content_application_tests::{candidate, original};
use openraft::storage::RaftStateMachine;

/// Produces real original v4 Log/View/Snapshot records and closes their actual old managed writers.
async fn legacy_fixture() -> (tempfile::TempDir, ArtifactContentKey) {
    let directory = tempfile::tempdir().unwrap();
    let (_original, cas, key) = original(directory.path());
    let profile = "a".repeat(64);
    let content =
        ReplicaContentStore::create(&directory.path().join("content"), &profile, 1).unwrap();
    let mut store = SqliteRaftStore::open_with_content(
        &directory.path().join("parent.sqlite3"),
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    let candidate = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &profile,
    );
    content
        .copy_from_artifact(
            &cas,
            &key,
            "original-v4-copy",
            &candidate.identity().unwrap(),
            30000,
        )
        .unwrap();
    let entry = Entry {
        log_id: LogId::new(openraft::CommittedLeaderId::new(1, 1), 1),
        payload: EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: Box::new(candidate),
        }),
    };
    store
        .persist_log_entries(std::slice::from_ref(&entry))
        .unwrap();
    assert!(store.apply([entry]).await.unwrap()[0].accepted);
    let snapshot = store.build_snapshot().await.unwrap();
    drop(snapshot);
    content
        .wait_for_snapshot_closures(Duration::from_secs(5))
        .await
        .unwrap();
    (directory, key)
}

/// Reopens only the actual already-upgraded original root for a new explicit adoption invocation.
fn root(directory: &Path) -> ReplicaContentStore {
    ReplicaContentStore::open_with_producers(&directory.join("content"), &"a".repeat(64), 1)
        .unwrap()
}

/// Runs the actual public adoption entry on the exact original source without an implicit root upgrade.
fn adopt(directory: &Path, operation: &str) -> Result<ReplicaContentParentAdoptionStatus, String> {
    SqliteRaftStore::adopt_original_content_parent(
        &directory.join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root(directory),
        operation,
        300_000,
    )
}

/// Complete original business SQL, legacy pins and unavailable history survive new current responsibility.
#[tokio::test]
async fn actual_v4_adoption_preserves_original_rows_pins_and_reopens_current_producer_paths() {
    let (directory, key) = legacy_fixture().await;
    let path = directory.path().join("parent.sqlite3");
    let before = source_digest(&Connection::open(&path).unwrap()).unwrap();
    let content = ReplicaContentStore::upgrade_producer_journal(
        &directory.path().join("content"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    let identity = content.identity().clone();
    let legacy = content.legacy_producer_inventory_digest().unwrap();
    drop(content);
    let result = adopt(directory.path(), "actual-original-adoption").unwrap();
    assert_eq!(result.disposition, "published");
    assert_eq!(
        result.original["intent"]["sources"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    assert_eq!(
        source_digest(&Connection::open(&path).unwrap()).unwrap(),
        before
    );
    assert_eq!(
        adopt(directory.path(), "actual-original-adoption").unwrap(),
        result
    );
    let content = root(directory.path());
    assert_eq!(content.identity(), &identity);
    assert_eq!(content.legacy_producer_inventory_digest().unwrap(), legacy);
    assert_eq!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::View)
            .count(),
        2
    );
    let mut store = SqliteRaftStore::open_with_content(
        &path,
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    assert_eq!(
        store.content_parent_adoption_history().unwrap(),
        vec![result]
    );
    assert_eq!(store.reconcile_content_producers().unwrap().len(), 3);
    let snapshot = store.build_snapshot().await.unwrap();
    drop(snapshot);
    content
        .wait_for_snapshot_closures(Duration::from_secs(5))
        .await
        .unwrap();
    store.verify_accepted_content().unwrap();
    let sql = store.connection.lock().unwrap();
    assert_eq!(
        sql.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        6
    );
}

/// Missing actual bytes leave a durable original Unknown; same-ID retry never repeats byte work.
#[tokio::test]
async fn actual_missing_content_keeps_original_unknown_and_distinct_repair_retains_all_history() {
    let (directory, key) = legacy_fixture().await;
    let content = ReplicaContentStore::upgrade_producer_journal(
        &directory.path().join("content"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    let content_file = content
        .root()
        .join("content-v1")
        .join(&key.source_owner[15..])
        .join(&key.content_digest[7..]);
    drop(content);
    let hidden = content_file.with_extension("held-original");
    std::fs::rename(&content_file, &hidden).unwrap();
    assert!(adopt(directory.path(), "actual-interrupted-adoption").is_err());
    let original = adopt(directory.path(), "actual-interrupted-adoption").unwrap();
    assert_eq!(original.disposition, "unknown");
    assert!(original.original["publication"].is_null());
    assert_eq!(
        original.original["intent"]["budget"]["duration_ms"],
        300_000
    );
    std::fs::rename(hidden, content_file).unwrap();
    assert_eq!(
        adopt(directory.path(), "actual-interrupted-adoption").unwrap(),
        original
    );
    let repaired = adopt(directory.path(), "actual-distinct-adoption-repair").unwrap();
    assert_eq!(repaired.disposition, "published");
    let content = root(directory.path());
    let store = SqliteRaftStore::open_with_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content,
    )
    .unwrap();
    assert_eq!(
        store.content_parent_adoption_history().unwrap(),
        vec![original, repaired]
    );
    drop(store);
    assert!(adopt(directory.path(), "new-command-after-publication").is_err());
}

/// An actual unclosed legacy SQLite connection blocks native migration before original intent admission.
#[tokio::test]
async fn actual_unmanaged_sql_connection_blocks_adoption_without_publishing_new_intent() {
    let (directory, _) = legacy_fixture().await;
    let content = ReplicaContentStore::upgrade_producer_journal(
        &directory.path().join("content"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    drop(content);
    let path = directory.path().join("parent.sqlite3");
    let old = Connection::open(&path).unwrap();
    old.execute_batch("PRAGMA journal_mode=WAL; BEGIN; SELECT * FROM replica_v1_meta;")
        .unwrap();
    let before = source_digest(&old).unwrap();
    let error = adopt(directory.path(), "actual-held-original-connection").unwrap_err();
    assert!(error.contains("not quiescent"), "{error}");
    assert_eq!(source_digest(&old).unwrap(), before);
    assert!(history(&old, false).unwrap().is_empty());
    drop(old);
    assert_eq!(
        adopt(directory.path(), "actual-held-original-connection")
            .unwrap()
            .disposition,
        "published"
    );
}

/// A lost mandatory adopted journal refuses normal reopen and cannot be silently defaulted to empty.
#[tokio::test]
async fn actual_missing_adoption_history_refuses_before_current_authority_can_open() {
    let (directory, _) = legacy_fixture().await;
    drop(
        ReplicaContentStore::upgrade_producer_journal(
            &directory.path().join("content"),
            &"a".repeat(64),
            1,
        )
        .unwrap(),
    );
    adopt(directory.path(), "actual-history-source").unwrap();
    let path = directory.path().join("parent.sqlite3");
    let sql = Connection::open(&path).unwrap();
    sql.execute("DELETE FROM replica_v1_meta WHERE key=?1", [HISTORY_KEY])
        .unwrap();
    drop(sql);
    let error = SqliteRaftStore::open_with_content(
        &path,
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root(directory.path()),
    )
    .err()
    .unwrap();
    assert!(
        error.contains("mandatory original parent adoption history"),
        "{error}"
    );
    assert!(adopt(directory.path(), "actual-history-source").is_err());
}

/// A real original pre-spawn intent without an actual child wait cannot become drained by storage upgrade.
#[tokio::test]
async fn actual_unreaped_spawn_history_blocks_adoption_without_inventing_an_empty_child_set() {
    let (directory, _) = legacy_fixture().await;
    let content =
        ReplicaContentStore::open(&directory.path().join("content"), &"a".repeat(64), 1).unwrap();
    let store = SqliteRaftStore::open_with_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content,
    )
    .unwrap();
    store.enable_controller_supervision().unwrap();
    store
        .begin_controller_process(
            ControllerEpoch {
                term: 1,
                replica_id: 1,
            },
            &crate::supervision::new_process_instance().unwrap(),
        )
        .unwrap();
    drop(store);
    drop(
        ReplicaContentStore::upgrade_producer_journal(
            &directory.path().join("content"),
            &"a".repeat(64),
            1,
        )
        .unwrap(),
    );
    let error = adopt(directory.path(), "actual-unreaped-original-spawn").unwrap_err();
    assert!(error.contains("actual wait is unavailable"), "{error}");
    let sql = Connection::open(directory.path().join("parent.sqlite3")).unwrap();
    assert!(history(&sql, false).unwrap().is_empty());
}

/// An actual empty original parent can adopt without fabricating a consensus epoch or producer history.
#[test]
fn actual_empty_v4_parent_adopts_only_its_new_management_record_and_keeps_empty_sources() {
    let directory = tempfile::tempdir().unwrap();
    let content =
        ReplicaContentStore::create(&directory.path().join("content"), &"a".repeat(64), 1).unwrap();
    drop(
        SqliteRaftStore::open_with_content(
            &directory.path().join("parent.sqlite3"),
            &"a".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            content,
        )
        .unwrap(),
    );
    drop(
        ReplicaContentStore::upgrade_producer_journal(
            &directory.path().join("content"),
            &"a".repeat(64),
            1,
        )
        .unwrap(),
    );
    let result = adopt(directory.path(), "actual-empty-parent").unwrap();
    assert_eq!(result.disposition, "published");
    assert_eq!(result.original["intent"]["sources"], serde_json::json!([]));
    assert_eq!(
        result.original["publication"]["producer_ids"],
        serde_json::json!([])
    );
    let content = root(directory.path());
    let store = SqliteRaftStore::open_with_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content,
    )
    .unwrap();
    assert!(store.content_producer_history().unwrap().is_empty());
    assert!(store.accepted_view().unwrap().checkpoint.is_none());
}

/// The actual pending format refuses lost history even when both local journal keys were deleted.
#[tokio::test]
async fn actual_pending_original_format_never_defaults_lost_adoption_history_to_empty() {
    let (directory, key) = legacy_fixture().await;
    let content = ReplicaContentStore::upgrade_producer_journal(
        &directory.path().join("content"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    let file = content
        .root()
        .join("content-v1")
        .join(&key.source_owner[15..])
        .join(&key.content_digest[7..]);
    drop(content);
    std::fs::remove_file(file).unwrap();
    assert!(adopt(directory.path(), "actual-pending-journal-source").is_err());
    let sql = Connection::open(directory.path().join("parent.sqlite3")).unwrap();
    assert_eq!(
        sql.query_row("SELECT schema FROM replica_storage_format", [], |row| {
            row.get::<_, String>(0)
        })
        .unwrap(),
        PENDING_SCHEMA
    );
    sql.execute(
        "DELETE FROM replica_v1_meta WHERE key IN (?1,?2)",
        [HISTORY_KEY, HISTORY_HEAD],
    )
    .unwrap();
    drop(sql);
    let error = adopt(directory.path(), "actual-pending-journal-source").unwrap_err();
    assert!(
        error.contains("mandatory original parent adoption history"),
        "{error}"
    );
}
