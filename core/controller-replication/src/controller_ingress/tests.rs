//! Actual parent SQL/process contracts; synthetic membership below is no real election or device fact.

use super::*;
use openraft::{CommittedLeaderId, LogId, Membership, StoredMembership};
use std::path::Path;
use std::process::{Child, Command, Stdio};

/// Creates actual explicit parent8 and Root4 without any network, Controller or Node.
fn parent(path: &Path) -> (ReplicaContentStore, SqliteRaftStore) {
    let root =
        ReplicaContentStore::create_with_streams(&path.join("root"), &"a".repeat(64), 1).unwrap();
    let store = SqliteRaftStore::open_with_controller_content(
        &path.join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root.clone(),
    )
    .unwrap();
    (root, store)
}

/// Registers an actual owned OS child before constructing any original operation fixture.
fn process(store: &SqliteRaftStore) -> (Child, ControllerProcessIdentity) {
    store.enable_controller_supervision().unwrap();
    let epoch = ControllerEpoch {
        term: 1,
        replica_id: 1,
    };
    let supervisor = crate::supervision::new_process_instance().unwrap();
    let instance = store.begin_controller_process(epoch, &supervisor).unwrap();
    let child = Command::new("sh")
        .args(["-c", "read -r original_line"])
        .stdin(Stdio::piped())
        .spawn()
        .unwrap();
    let stat = std::fs::read_to_string(format!("/proc/{}/stat", child.id())).unwrap();
    let process = ControllerProcessIdentity {
        instance_id: instance,
        supervisor_instance: supervisor,
        epoch,
        pid: child.id(),
        boot_id: std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
            .unwrap()
            .trim()
            .into(),
        start_ticks: stat
            .rsplit_once(')')
            .unwrap()
            .1
            .split_whitespace()
            .nth(19)
            .unwrap()
            .parse()
            .unwrap(),
    };
    store.register_controller_process(process.clone()).unwrap();
    (child, process)
}

/// Reaps only the original actual child and durably retains its genuine signal outcome.
fn stop(store: &SqliteRaftStore, mut child: Child, process: ControllerProcessIdentity) {
    use std::os::unix::process::ExitStatusExt;
    child.kill().unwrap();
    let outcome = child.wait().unwrap();
    let supervisor = process.supervisor_instance.clone();
    store
        .record_controller_exit(
            ControllerProcessExit {
                process,
                exit_code: outcome.code(),
                signal: outcome.signal(),
            },
            &supervisor,
        )
        .unwrap();
}

/// Creates labelled synthetic retention context over actual source/checkpoint files, without consensus.
fn intent(
    path: &Path,
    root: &ReplicaContentStore,
    process: ControllerProcessIdentity,
) -> IngressIntent {
    let source = path.join("source");
    std::fs::create_dir(&source).unwrap();
    let (log, cas, _) = crate::content_application_tests::original(&source);
    drop(log);
    let candidate = crate::content_application_tests::candidate(
        &source.join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &"a".repeat(64),
    );
    let target = ContentQuorumTarget {
        profile_identity: "a".repeat(64),
        epoch: process.epoch,
        revision: 1,
        checkpoint_digest: state::checkpoint_identity(&candidate.batch.checkpoint),
        batch_digest: candidate.identity().unwrap(),
        membership: StoredMembership::new(
            Some(LogId::new(CommittedLeaderId::new(1, 1), 1)),
            Membership::new(
                vec![BTreeSet::from([1])],
                BTreeMap::from([(
                    1,
                    ReplicaMember::with_content_store(
                        "127.0.0.1:24467",
                        root.identity().store_owner.clone(),
                    )
                    .unwrap(),
                )]),
            ),
        ),
        member_stores: BTreeMap::from([(1, root.identity().store_owner.clone())]),
        inputs: candidate.inputs.clone(),
    };
    IngressIntent {
        schema: JOURNAL_SCHEMA.into(),
        command_id: "actual-original-command".into(),
        process,
        peer_leaf: format!("sha256:{}", "b".repeat(64)),
        candidate: Box::new(candidate),
        target,
        budget: ContentBudget::issue(30000).unwrap(),
    }
}

/// A genuinely empty parent8 still fences old opens and refuses a missing mandatory zero-operation head.
#[test]
fn actual_parent8_empty_inventory_fences_old_writers_and_refuses_missing_authority() {
    let directory = tempfile::tempdir().unwrap();
    let (root, store) = parent(directory.path());
    let identity = store
        .connection
        .lock()
        .unwrap()
        .query_row("PRAGMA user_version", [], |row| row.get::<_, u64>(0))
        .unwrap();
    assert_eq!(identity, 8);
    validate_storage(&store.connection.lock().unwrap(), true).unwrap();
    drop(store);
    assert!(
        SqliteRaftStore::open_with_network_content(
            &directory.path().join("parent.sqlite3"),
            &"a".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            root.clone()
        )
        .is_err()
    );
    let reopened = SqliteRaftStore::open_with_controller_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root.clone(),
    )
    .unwrap();
    reopened
        .connection
        .lock()
        .unwrap()
        .execute("DELETE FROM replica_v1_meta WHERE key=?1", [HEAD_KEY])
        .unwrap();
    drop(reopened);
    assert!(
        SqliteRaftStore::open_with_controller_content(
            &directory.path().join("parent.sqlite3"),
            &"a".repeat(64),
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            root
        )
        .err()
        .unwrap()
        .contains("mandatory original Controller ingress inventory")
    );
}

/// First Unknown survives later permission loss; a real SQL abort saves neither intent nor its head.
#[test]
fn actual_original_command_is_readonly_on_retry_and_sql_failure_retains_no_partial_head() {
    let directory = tempfile::tempdir().unwrap();
    let (root, store) = parent(directory.path());
    let (child, process) = process(&store);
    let original = intent(directory.path(), &root, process.clone());
    let denied: StreamPermission =
        Arc::new(|| Err("actual last-boundary permission refusal".into()));
    assert!(journal::admit(&store, original.clone(), denied.clone()).is_err());
    assert!(empty_storage(&store.connection.lock().unwrap()).unwrap());
    store.connection.lock().unwrap().execute_batch("CREATE TRIGGER fail_ingress BEFORE INSERT ON controller_content_ingress BEGIN SELECT RAISE(ABORT,'actual original intent SQL failure'); END;").unwrap();
    assert!(journal::admit(&store, original.clone(), Arc::new(|| Ok(()))).is_err());
    assert!(empty_storage(&store.connection.lock().unwrap()).unwrap());
    store
        .connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_ingress")
        .unwrap();
    let IngressAdmission::New(admitted) =
        journal::admit(&store, original.clone(), Arc::new(|| Ok(()))).unwrap()
    else {
        panic!("first original admission was not new");
    };
    let expected = status(&admitted).unwrap();
    let IngressAdmission::Existing(replayed) =
        journal::admit(&store, original.clone(), denied).unwrap()
    else {
        panic!("original retry admitted another effect");
    };
    assert_eq!(*replayed, expected);
    let mut changed = original;
    changed.candidate.source_writer.inode += 1;
    assert!(journal::admit(&store, changed, Arc::new(|| Ok(()))).is_err());
    stop(&store, child, process);
    validate_storage(&store.connection.lock().unwrap(), true).unwrap();
    drop(store);
    let reopened = SqliteRaftStore::open_with_controller_content(
        &directory.path().join("parent.sqlite3"),
        &"a".repeat(64),
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root,
    )
    .unwrap();
    let connection = reopened.connection.lock().unwrap();
    assert_eq!(
        status(
            &journal::record(&connection, "actual-original-command")
                .unwrap()
                .unwrap()
        )
        .unwrap(),
        expected
    );
}

/// The kernel PID and actual start ticks cannot be borrowed by another process or an unreaped zombie.
#[test]
fn actual_original_peer_requires_exact_registered_live_incarnation_and_actual_wait() {
    let directory = tempfile::tempdir().unwrap();
    let (_root, store) = parent(directory.path());
    let (mut child, process) = process(&store);
    assert!(store.require_original_endpoint_reopen().is_err());
    {
        let connection = store.connection.lock().unwrap();
        let check = |pid| {
            crate::supervision::require_original_peer(
                &connection,
                &process.supervisor_instance,
                process.epoch,
                &process.instance_id,
                pid,
            )
        };
        assert_eq!(check(child.id()).unwrap(), process);
        assert!(check(std::process::id()).is_err());
    }
    child.kill().unwrap();
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    loop {
        let connection = store.connection.lock().unwrap();
        let result = crate::supervision::require_original_peer(
            &connection,
            &process.supervisor_instance,
            process.epoch,
            &process.instance_id,
            process.pid,
        );
        if result.is_err() {
            break;
        }
        assert!(std::time::Instant::now() < deadline);
        drop(connection);
        std::thread::yield_now();
    }
    use std::os::unix::process::ExitStatusExt;
    let actual = child.wait().unwrap();
    store
        .record_controller_exit(
            ControllerProcessExit {
                process: process.clone(),
                exit_code: actual.code(),
                signal: actual.signal(),
            },
            &process.supervisor_instance,
        )
        .unwrap();
    store.require_original_endpoint_reopen().unwrap();
    assert!(
        crate::supervision::require_original_peer(
            &store.connection.lock().unwrap(),
            &process.supervisor_instance,
            process.epoch,
            &process.instance_id,
            process.pid
        )
        .is_err()
    );
}
