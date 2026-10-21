//! Independent durable acceptance/rollback tests, including ambiguous quorum response loss.

use super::*;
use std::sync::atomic::{AtomicBool, Ordering};

/// Legacy replication cannot discard zero-event command authority from current or prior wrappers.
#[test]
fn legacy_batch_cannot_hide_v43_v44_or_current_authority_head() {
    for schema in [
        "roboguide.controller-checkpoint/v43",
        "roboguide.controller-checkpoint/v44",
        "roboguide.controller-checkpoint/v45",
        "roboguide.controller-checkpoint/v46",
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
    ] {
        let batch = ApplicationCommitBatch {
            schema: "roboguide.application-commit/v1".into(),
            previous_event_sequence: 0,
            previous_checkpoint_digest: None,
            checkpoint: PersistedCheckpoint {
                schema: schema.into(),
                event_sequence: 0,
                checkpoint_json: "{}".into(),
            },
            events: Vec::new(),
            commands: Vec::new(),
        };
        assert!(
            batch
                .validate()
                .unwrap_err()
                .to_string()
                .contains("legacy replica codec")
        );
    }
}

/// Records the external accepted candidate while checking that private SQL is still invisible.
struct QuorumFixture {
    /// Inactive readonly observer of the local materialized database.
    path: std::path::PathBuf,
    /// Simulated response loss after durable acceptance.
    lose_response: AtomicBool,
    /// Simulated lack of write authority before beginning SQL.
    unavailable: AtomicBool,
    /// Exact quorum-accepted candidate retained independently of SQL rollback.
    accepted: Mutex<Option<ApplicationCommitBatch>>,
}

impl ApplicationBatchReplicator for QuorumFixture {
    /// Supplies one frozen infrastructure identity.
    fn profile_identity(&self) -> &str {
        "quorum-fixture"
    }
    /// Rejects before any private mutation without a current majority.
    fn assert_write_authority(&self) -> Result<(), String> {
        if self.unavailable.load(Ordering::SeqCst) {
            Err("no quorum".into())
        } else {
            Ok(())
        }
    }
    /// Observes old durable SQL, accepts the exact target, and optionally loses its response.
    fn confirm_batch(&self, batch: ApplicationCommitBatch) -> Result<(), String> {
        assert_eq!(
            SqliteEventLog::read_checkpoint(&self.path)
                .unwrap()
                .as_ref()
                .map(checkpoint_identity),
            batch.previous_checkpoint_digest
        );
        *self.accepted.lock().unwrap() = Some(batch);
        if self.lose_response.load(Ordering::SeqCst) {
            Err("accepted response lost".into())
        } else {
            Ok(())
        }
    }
}

/// Creates an independent acceptance authority and one private SQLite projection.
fn fixture() -> (tempfile::TempDir, SqliteEventLog, Arc<QuorumFixture>) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("controller.sqlite3");
    let replicator = Arc::new(QuorumFixture {
        path: path.clone(),
        lose_response: AtomicBool::new(false),
        unavailable: AtomicBool::new(false),
        accepted: Mutex::new(None),
    });
    let log = SqliteEventLog::open(&path)
        .unwrap()
        .with_replicator(replicator.clone());
    (directory, log, replicator)
}

/// Quorum absence and missing checkpoints cannot become durable private authority.
#[test]
fn quorum_precedes_commit_and_requires_complete_target() {
    let (_directory, log, replicator) = fixture();
    replicator.unavailable.store(true, Ordering::SeqCst);
    assert!(log.begin_batch().is_err());
    assert_eq!(log.latest_sequence().unwrap(), 0);
    replicator.unavailable.store(false, Ordering::SeqCst);
    log.begin_batch().unwrap();
    assert!(log.commit_batch().is_err());
    log.rollback_batch().unwrap();
    assert!(replicator.accepted.lock().unwrap().is_none());
    log.begin_batch().unwrap();
    log.save_checkpoint("authority/v1", "{\"ownership\":\"held\"}")
        .unwrap();
    log.commit_batch().unwrap();
    assert!(replicator.accepted.lock().unwrap().is_some());
}

/// Response loss rolls back local SQL but promotion rematerializes the already accepted complete authority.
#[test]
fn accepted_response_loss_converges_without_event_or_checkpoint_loss() {
    let (directory, log, replicator) = fixture();
    replicator.lose_response.store(true, Ordering::SeqCst);
    log.begin_batch().unwrap();
    log.save_checkpoint("authority/v1", "{\"unknown\":true,\"ownership\":\"held\"}")
        .unwrap();
    assert!(log.commit_batch().is_err());
    log.rollback_batch().unwrap();
    assert!(log.load_checkpoint().unwrap().is_none());
    let batch = replicator.accepted.lock().unwrap().clone().unwrap();
    let restored = SqliteEventLog::open(directory.path().join("promoted.sqlite3")).unwrap();
    restored
        .install_replica_authority(&batch.events, &batch.checkpoint, None, &batch.commands)
        .unwrap();
    assert_eq!(restored.load_checkpoint().unwrap(), Some(batch.checkpoint));
}

/// A real deferred SQL constraint can fail after quorum accepted; rollback still cannot erase that acceptance.
#[test]
fn local_commit_failure_after_quorum_remains_recoverable() {
    let (_directory, log, replicator) = fixture();
    log.connection
        .lock()
        .unwrap()
        .execute_batch(
            "PRAGMA foreign_keys=ON; CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE child(parent INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED);",
        )
        .unwrap();
    log.begin_batch().unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute("INSERT INTO child(parent) VALUES (17)", [])
        .unwrap();
    log.save_checkpoint("authority/v1", "{\"ownership\":\"retained\"}")
        .unwrap();
    assert!(log.commit_batch().is_err());
    log.rollback_batch().unwrap();
    assert!(log.load_checkpoint().unwrap().is_none());
    assert!(replicator.accepted.lock().unwrap().is_some());
}
