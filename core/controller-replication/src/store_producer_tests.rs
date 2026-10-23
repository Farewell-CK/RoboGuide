//! Actual source SQL/producer transactions; direct storage tests never claim a network quorum.

use super::*;
use crate::content_application_tests::{candidate, original};

/// Copies actual original bytes through the maintained source writer before a receiving view may apply.
fn copy_fixture(
    directory: &tempfile::TempDir,
    content: &ReplicaContentStore,
    key: &ArtifactContentKey,
    operation: &str,
) {
    let source = artifact_store::FileSystemArtifactStore::new_with_archive_references(
        directory.path().join("source-cas"),
    )
    .unwrap();
    assert!(
        content
            .copy_from_artifact(&source, key, operation, &"f".repeat(64), 30000)
            .unwrap()
            .receipt
            .is_some()
    );
}

/// Finds exactly one original source kind without confusing independent log/view/Snapshot responsibilities.
fn original_kind(
    store: &SqliteRaftStore,
    kind: ReplicaContentProducerKind,
) -> ReplicaContentProducerStatus {
    let matches = store
        .parent_producers()
        .unwrap()
        .into_iter()
        .filter(|original| {
            serde_json::from_value::<ReplicaContentProducerSource>(
                original.original["intent"]["source"].clone(),
            )
            .unwrap()
            .kind
                == kind
        })
        .collect::<Vec<_>>();
    assert_eq!(matches.len(), 1);
    matches.into_iter().next().unwrap()
}

/// Creates original actual upload bytes/State and a separate real parent SQL + opted-in producer backend.
fn fixture() -> (
    tempfile::TempDir,
    SqliteRaftStore,
    ReplicaContentStore,
    ArtifactContentKey,
    Entry<ControllerRaftConfig>,
) {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, key) = original(directory.path());
    let profile = "e".repeat(64);
    let content = ReplicaContentStore::create_with_producers(
        &directory.path().join("managed-content"),
        &profile,
        1,
    )
    .unwrap();
    let store = SqliteRaftStore::open_with_content(
        &directory.path().join("managed-parent.sqlite3"),
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
    let entry = Entry {
        log_id: LogId::new(openraft::CommittedLeaderId::new(1, 1), 1),
        payload: EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: Box::new(candidate),
        }),
    };
    (directory, store, content, key, entry)
}

/// A real parent trigger failure leaves original intent/pins/close evidence; no retry creates another effect.
#[test]
fn actual_log_sql_failure_keeps_original_unknown_and_retires_only_after_actual_scope_and_source() {
    let (_directory, store, content, key, entry) = fixture();
    store.connection.lock().unwrap().execute_batch("CREATE TRIGGER actual_original_log_fault BEFORE INSERT ON replica_v1_log BEGIN SELECT RAISE(ABORT,'actual original log SQL failure'); END").unwrap();
    assert!(
        store
            .persist_log_entries(std::slice::from_ref(&entry))
            .unwrap_err()
            .to_string()
            .contains("actual original log SQL failure")
    );
    let original = store.parent_producers().unwrap().pop().unwrap();
    assert_eq!(original.disposition, "unknown");
    assert!(original.original["publication"].is_null());
    assert!(original.original["worker_closed_ms"].is_number());
    assert_eq!(content.active_references(&key).unwrap().len(), 1);
    assert!(
        store
            .persist_log_entries(std::slice::from_ref(&entry))
            .unwrap_err()
            .to_string()
            .contains("cannot execute again")
    );
    assert_eq!(store.parent_producers().unwrap(), vec![original.clone()]);
    let closed = store.reconcile_content_producers().unwrap().pop().unwrap();
    assert_eq!(closed.disposition, "retired-unknown");
    assert_eq!(closed.original["intent"], original.original["intent"]);
    assert!(closed.original["publication"].is_null());
    assert!(closed.original["resolution"]["process_end"].is_null());
    assert!(content.active_references(&key).unwrap().is_empty());
    assert_eq!(store.reconcile_content_producers().unwrap(), vec![closed]);
    store
        .connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER actual_original_log_fault")
        .unwrap();
    assert!(
        store
            .persist_log_entries(&[entry])
            .unwrap_err()
            .to_string()
            .contains("cannot execute again")
    );
    assert_eq!(
        store
            .connection
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM replica_v1_log", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
}

/// Actual durable original log publication and its independent pin survive reopen; purge retires its own scope.
#[tokio::test]
async fn actual_log_publication_reopen_and_purge_preserve_original_budget_and_receipts() {
    let (directory, store, content, key, entry) = fixture();
    store
        .persist_log_entries(std::slice::from_ref(&entry))
        .unwrap();
    let original = store.parent_producers().unwrap().pop().unwrap();
    assert_eq!(original.disposition, "published");
    assert!(original.original["publication"]["completed_ms"].is_number());
    let pins = content.active_references(&key).unwrap();
    assert_eq!(pins.len(), 1);
    assert_eq!(pins[0].kind, ReplicaContentReferenceKind::Log);
    store
        .persist_log_entries(std::slice::from_ref(&entry))
        .unwrap();
    assert_eq!(store.parent_producers().unwrap(), vec![original.clone()]);
    drop(store);
    drop(content);
    let content = ReplicaContentStore::open_with_producers(
        &directory.path().join("managed-content"),
        &"e".repeat(64),
        1,
    )
    .unwrap();
    let mut store = SqliteRaftStore::open_with_content(
        &directory.path().join("managed-parent.sqlite3"),
        &"e".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    assert_eq!(store.parent_producers().unwrap(), vec![original.clone()]);
    store.purge(entry.log_id).await.unwrap();
    let released = store.parent_producers().unwrap().pop().unwrap();
    assert_eq!(released.disposition, "released");
    assert_eq!(
        released.original["publication"],
        original.original["publication"]
    );
    assert_eq!(released.original["intent"], original.original["intent"]);
    assert!(content.active_references(&key).unwrap().is_empty());
    assert!(
        ReplicaContentStore::open(
            &directory.path().join("managed-content"),
            &"e".repeat(64),
            1
        )
        .is_err()
    );
}

/// Actual apply rollback cannot reuse its original view intent or retire a still-durable independent log.
#[tokio::test]
async fn actual_view_sql_rollback_preserves_unknown_and_independent_log() {
    let (directory, mut store, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-view-input");
    store
        .persist_log_entries(std::slice::from_ref(&entry))
        .unwrap();
    store.connection.lock().unwrap().execute_batch("CREATE TRIGGER actual_view_fault BEFORE INSERT ON replica_v1_meta WHEN NEW.key='content_state' BEGIN SELECT RAISE(ABORT,'actual original view SQL failure'); END").unwrap();
    assert!(
        store
            .apply([entry.clone()])
            .await
            .unwrap_err()
            .to_string()
            .contains("actual original view SQL failure")
    );
    let original = original_kind(&store, ReplicaContentProducerKind::View);
    assert_eq!(original.disposition, "unknown");
    assert!(original.original["worker_closed_ms"].is_number());
    assert!(store.accepted_view().unwrap().checkpoint.is_none());
    store
        .connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER actual_view_fault")
        .unwrap();
    assert!(
        store
            .apply([entry])
            .await
            .unwrap_err()
            .to_string()
            .contains("cannot execute again")
    );
    store.reconcile_content_producers().unwrap();
    let closed = original_kind(&store, ReplicaContentProducerKind::View);
    assert_eq!(closed.disposition, "retired-unknown");
    assert_eq!(closed.original["intent"], original.original["intent"]);
    assert!(closed.original["publication"].is_null());
    let pins = content.active_references(&key).unwrap();
    assert_eq!(
        pins.iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Log)
            .count(),
        1
    );
    assert_eq!(
        pins.iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Receive)
            .count(),
        1
    );
    assert_eq!(pins.len(), 2);
}

/// Exact repeated apply is a readonly observation even if a later actual blank log advanced its pointer.
#[tokio::test]
async fn actual_view_publication_does_not_regress_applied_or_repeat_sql_on_original_retry() {
    let (directory, mut store, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-view-input");
    assert!(store.apply([entry.clone()]).await.unwrap()[0].accepted);
    let original = original_kind(&store, ReplicaContentProducerKind::View);
    assert_eq!(original.disposition, "published");
    let later = Entry {
        log_id: LogId::new(openraft::CommittedLeaderId::new(1, 1), 2),
        payload: EntryPayload::Blank,
    };
    store.apply([later.clone()]).await.unwrap();
    store.connection.lock().unwrap().execute_batch("CREATE TRIGGER no_duplicate_view_write BEFORE INSERT ON replica_v1_meta BEGIN SELECT RAISE(ABORT,'duplicate wrote parent SQL'); END").unwrap();
    assert!(store.apply([entry]).await.unwrap()[0].accepted);
    assert_eq!(store.applied_state().await.unwrap().0, Some(later.log_id));
    assert_eq!(
        original_kind(&store, ReplicaContentProducerKind::View),
        original
    );
}

/// Actual failed Snapshot SQL retains its first Unknown; explicit new work replaces only its own producer.
#[tokio::test]
async fn actual_snapshot_failure_repair_and_replacement_retain_independent_readers() {
    let (directory, mut store, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-snapshot-input");
    store.apply([entry]).await.unwrap();
    let reader = store
        .build_content_snapshot_for("original-first-build")
        .unwrap();
    let first = original_kind(&store, ReplicaContentProducerKind::SnapshotBuild);
    store.connection.lock().unwrap().execute_batch("CREATE TRIGGER actual_snapshot_fault BEFORE INSERT ON replica_v1_snapshot BEGIN SELECT RAISE(ABORT,'actual original Snapshot SQL failure'); END").unwrap();
    assert!(
        store
            .build_content_snapshot_for("original-failed-build")
            .err()
            .expect("original build must refuse")
            .to_string()
            .contains("actual original Snapshot SQL failure")
    );
    let unknown = store
        .parent_producers()
        .unwrap()
        .into_iter()
        .find(|original| original.disposition == "unknown")
        .unwrap();
    assert!(unknown.original["publication"].is_null());
    store
        .connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER actual_snapshot_fault")
        .unwrap();
    assert!(
        store
            .build_content_snapshot_for("original-failed-build")
            .err()
            .expect("original build must refuse")
            .to_string()
            .contains("cannot execute again")
    );
    let replacement = store
        .build_content_snapshot_for("explicit-independent-repair")
        .unwrap();
    let retired = content
        .producer_status(&unknown.operation_id)
        .unwrap()
        .unwrap();
    assert_eq!(retired.disposition, "retired-unknown");
    assert_eq!(retired.original["intent"], unknown.original["intent"]);
    assert!(retired.original["publication"].is_null());
    assert_eq!(
        content
            .producer_status(&first.operation_id)
            .unwrap()
            .unwrap()
            .disposition,
        "released"
    );
    let pins = content.active_references(&key).unwrap();
    assert_eq!(
        pins.iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
            .count(),
        2
    );
    assert_eq!(
        pins.iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Snapshot)
            .count(),
        1
    );
    assert!(
        store
            .build_content_snapshot_for("original-first-build")
            .is_err()
    );
    drop(reader);
    let closure = content
        .wait_for_snapshot_closures(Duration::from_secs(3))
        .await
        .unwrap();
    assert_eq!(closure.publication_errors_total, 0);
    assert_eq!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
            .count(),
        1
    );
    drop(replacement);
    let closure = content
        .wait_for_snapshot_closures(Duration::from_secs(3))
        .await
        .unwrap();
    assert_eq!(closure.publication_errors_total, 0);
    assert_eq!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Snapshot)
            .count(),
        1
    );
}

/// Receiving Snapshot authority belongs to actual target writers; missing bytes and old IDs cannot install.
#[tokio::test]
async fn actual_snapshot_install_requires_target_bytes_and_its_own_original_journal() {
    let (directory, mut source, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-source-input");
    source.apply([entry]).await.unwrap();
    let snapshot = source
        .build_content_snapshot_for("actual-source-snapshot")
        .unwrap();
    let meta = snapshot.meta;
    let bytes = snapshot.snapshot.into_inner();
    let target_content = ReplicaContentStore::create_with_producers(
        &directory.path().join("target-content"),
        &"e".repeat(64),
        2,
    )
    .unwrap();
    let target = SqliteRaftStore::open_with_content(
        &directory.path().join("target.sqlite3"),
        &"e".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        target_content.clone(),
    )
    .unwrap();
    assert!(
        target
            .install_content_snapshot_for("original-missing-bytes", &meta, bytes.clone())
            .is_err()
    );
    let originals = target.parent_producers().unwrap();
    assert_eq!(originals.len(), 2);
    assert!(
        originals
            .iter()
            .all(|original| original.disposition == "unknown")
    );
    copy_fixture(
        &directory,
        &target_content,
        &key,
        "actual-independent-target-input",
    );
    assert!(
        target
            .install_content_snapshot_for("original-missing-bytes", &meta, bytes.clone())
            .unwrap_err()
            .to_string()
            .contains("cannot execute again")
    );
    target
        .install_content_snapshot_for("explicit-target-storage-repair", &meta, bytes.clone())
        .unwrap();
    target.verify_accepted_content().unwrap();
    for old in &originals {
        let current = target_content
            .producer_status(&old.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(current.disposition, "retired-unknown");
        assert_eq!(current.original["intent"], old.original["intent"]);
        assert!(current.original["publication"].is_null());
    }
    let before = target.parent_producers().unwrap();
    target.connection.lock().unwrap().execute_batch("CREATE TRIGGER no_original_snapshot_repeat BEFORE INSERT ON replica_v1_snapshot BEGIN SELECT RAISE(ABORT,'original Snapshot repeated SQL'); END").unwrap();
    target
        .install_content_snapshot_for("explicit-target-storage-repair", &meta, bytes)
        .unwrap();
    assert_eq!(target.parent_producers().unwrap(), before);
    assert!(source.parent_producers().unwrap().iter().all(|original| {
        target_content
            .producer_status(&original.operation_id)
            .unwrap()
            .is_none()
    }));
}

/// Missing mandatory local heads refuse current reads/reconciliation/reopen without retiring any root pins.
#[tokio::test]
async fn actual_missing_parent_head_is_not_empty_reference_authority() {
    let (directory, mut store, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-head-input");
    store.apply([entry]).await.unwrap();
    let snapshot = store
        .build_content_snapshot_for("actual-original-head")
        .unwrap();
    drop(snapshot);
    content
        .wait_for_snapshot_closures(Duration::from_secs(3))
        .await
        .unwrap();
    let before = store.parent_producers().unwrap();
    let pins = content.active_references(&key).unwrap();
    store
        .connection
        .lock()
        .unwrap()
        .execute("DELETE FROM replica_v1_meta WHERE key=?1", [HEADS_KEY])
        .unwrap();
    assert!(
        store
            .reconcile_content_producers()
            .unwrap_err()
            .contains("heads are missing")
    );
    assert!(store.get_current_snapshot().await.is_err());
    assert_eq!(store.parent_producers().unwrap(), before);
    assert_eq!(content.active_references(&key).unwrap(), pins);
    drop(store);
    assert!(
        SqliteRaftStore::open_with_content(
            &directory.path().join("managed-parent.sqlite3"),
            &"e".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            content
        )
        .is_err()
    );
}

/// Actual single-thread Tokio scheduling/cancellation does not close a worker blocked on its real parent writer.
#[tokio::test(flavor = "current_thread")]
async fn actual_blocked_worker_keeps_original_scope_after_waiter_cancellation() {
    let (directory, store, content, key, entry) = fixture();
    copy_fixture(&directory, &content, &key, "actual-worker-input");
    let parent = store.clone();
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let holder = std::thread::spawn(move || {
        let _actual_writer = parent.connection.lock().unwrap();
        ready_tx.send(()).unwrap();
        release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    });
    ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let mut worker = store.clone();
    let waiter = tokio::spawn(async move { worker.apply([entry]).await });
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    let mut network_turns = 0;
    let original = loop {
        network_turns += 1;
        if let Some(original) = store.parent_producers().unwrap().pop() {
            break original;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "actual worker never persisted its original intent"
        );
        tokio::time::sleep(Duration::from_millis(5)).await;
    };
    waiter.abort();
    assert!(waiter.await.unwrap_err().is_cancelled());
    for _ in 0..5 {
        tokio::time::sleep(Duration::from_millis(5)).await;
        network_turns += 1;
    }
    assert!(network_turns >= 6);
    let uncertain = content
        .producer_status(&original.operation_id)
        .unwrap()
        .unwrap();
    assert!(uncertain.original["worker_closed_ms"].is_null());
    assert!(uncertain.original["publication"].is_null());
    let descriptor = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(directory.path().join("managed-parent.sqlite3"))
        .unwrap();
    assert!(descriptor.try_lock().is_err());
    release_tx.send(()).unwrap();
    holder.join().unwrap();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
    loop {
        let actual = content
            .producer_status(&original.operation_id)
            .unwrap()
            .unwrap();
        assert_eq!(actual.original["intent"], original.original["intent"]);
        if actual.disposition == "published" {
            break;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "actual cancelled wait's worker did not complete independently"
        );
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    store.verify_accepted_content().unwrap();
}

/// Real blocked SQL cannot occupy the sole network worker or lose its queue/lease on waiter cancellation.
#[tokio::test(flavor = "current_thread")]
async fn actual_consensus_sql_workers_retain_capacity_and_leases_after_cancel() {
    let (directory, store, content, _key, _entry) = fixture();
    let capacity = store.consensus_jobs.clone();
    let writer = Arc::downgrade(&store._writer);
    let parent_connection = store.connection.clone();
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let holder = std::thread::spawn(move || {
        let _actual_writer = parent_connection.lock().unwrap();
        ready_tx.send(()).unwrap();
        release_rx.recv_timeout(Duration::from_secs(10)).unwrap();
    });
    ready_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let mut waiters = Vec::new();
    for _ in 0..16 {
        let mut worker = store.clone();
        waiters.push(tokio::spawn(async move { worker.get_log_state().await }));
    }
    tokio::time::timeout(Duration::from_secs(2), async {
        while capacity.available_permits() != 0 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    let mut refused = store.clone();
    assert!(
        refused
            .get_log_state()
            .await
            .unwrap_err()
            .to_string()
            .contains("capacity is exhausted")
    );
    drop(refused);
    for waiter in waiters {
        waiter.abort();
        assert!(waiter.await.unwrap_err().is_cancelled());
    }
    drop(store);
    drop(content);
    for _ in 0..5 {
        tokio::time::sleep(Duration::from_millis(5)).await;
        assert_eq!(capacity.available_permits(), 0);
    }
    let descriptor = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(directory.path().join("managed-parent.sqlite3"))
        .unwrap();
    assert!(descriptor.try_lock().is_err());
    assert!(writer.upgrade().is_some());
    release_tx.send(()).unwrap();
    holder.join().unwrap();
    tokio::time::timeout(Duration::from_secs(2), async {
        while capacity.available_permits() != 16 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(
        writer.strong_count(),
        0,
        "actual worker did not release original parent lease"
    );
    descriptor.try_lock().unwrap();
}

/// Heavy producer admission remains independently bounded and does not consume consensus SQL slots.
#[test]
fn actual_original_storage_queue_is_bounded_before_new_intent() {
    let (_directory, store, _content, _key, _entry) = fixture();
    let mut workers = Vec::new();
    for _ in 0..8 {
        workers.push(store.original_storage_work().unwrap());
    }
    assert!(
        store
            .original_storage_work()
            .err()
            .expect("queue must refuse ninth work")
            .to_string()
            .contains("capacity is exhausted")
    );
    assert!(store.parent_producers().unwrap().is_empty());
    workers.pop();
    assert!(store.original_storage_work().is_ok());
}

/// Real same-kernel expiry never renews the original producer or fabricates its publication on retirement.
#[test]
fn actual_original_producer_deadline_refuses_effects_without_renewing_history() {
    let (_directory, store, content, key, entry) = fixture();
    let EntryPayload::Normal(ReplicaCommand::ContentApplication {
        content_application: candidate,
    }) = &entry.payload
    else {
        panic!("actual fixture lost its original candidate");
    };
    let source = ReplicaContentProducerSource {
        kind: ReplicaContentProducerKind::Log,
        epoch: candidate.epoch,
        position: serde_json::to_string(&entry.log_id).unwrap(),
        authority_digest: candidate.identity().unwrap(),
        parent_record_digest: format!("{:x}", Sha256::digest(serde_json::to_vec(&entry).unwrap())),
        checkpoint_digest: Some(state::checkpoint_identity(&candidate.batch.checkpoint)),
    };
    let admission = content
        .admit_producer(
            "actual-short-original-budget",
            source.clone(),
            store._writer.clone(),
            std::slice::from_ref(&key),
            100,
            None,
        )
        .unwrap();
    let ProducerAdmission::New(permit) = admission else {
        panic!("original admission was not new");
    };
    std::thread::sleep(Duration::from_millis(125));
    assert!(permit.check().unwrap_err().contains("expired"));
    drop(permit);
    let original = content
        .producer_status("actual-short-original-budget")
        .unwrap()
        .unwrap();
    assert!(original.original["publication"].is_null());
    assert!(original.original["worker_closed_ms"].is_number());
    assert!(matches!(
        content
            .admit_producer(
                "actual-short-original-budget",
                source,
                store._writer.clone(),
                std::slice::from_ref(&key),
                100,
                None
            )
            .unwrap(),
        ProducerAdmission::Existing(_)
    ));
    assert_eq!(
        content
            .producer_status("actual-short-original-budget")
            .unwrap()
            .unwrap(),
        original
    );
    let retired = store.reconcile_content_producers().unwrap().pop().unwrap();
    assert_eq!(retired.disposition, "retired-unknown");
    assert_eq!(retired.original["intent"], original.original["intent"]);
    assert!(retired.original["publication"].is_null());
}
