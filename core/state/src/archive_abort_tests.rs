//! Deterministic cleanup-port contracts; fixture leases are not deployment or physical-stop evidence.

use super::*;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Explicit unavailable copy port, preserving its first real SQL intent without content publication.
struct Unpublished;
impl super::super::archive::ArchiveSink for Unpublished {
    /// Legacy copying cannot stand in for the explicit original content contract.
    fn append_segment(
        &self,
        _manifest: &super::super::archive::ArchiveManifest,
        _bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        Err("unit publication is unavailable".into())
    }
}

/// Independent contract fixture, never a real OS writer certificate or a device stop.
struct Fixture {
    /// Original actual temporary SQL store whose mutex must be absent at port entry.
    log: SqliteEventLog,
    /// Fixture original evidence supplied only after its first durable cleanup admission.
    receipts: Mutex<BTreeMap<String, ArchiveAbortReceipt>>,
    /// Effect entries, independent of queries and response-loss reconciliation.
    effects: AtomicUsize,
    /// Readonly original-result observations.
    observations: AtomicUsize,
}

impl ArchiveAbortSink for Fixture {
    /// Checks first actual SQL intent, then supplies explicitly synthetic lease values to test the port.
    fn abort_unpublished(
        &self,
        _original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String> {
        assert!(self.log.connection.try_lock().is_ok());
        let stored = self
            .log
            .archive_abort_operation(&intent.request.command_id)
            .unwrap()
            .unwrap();
        assert_eq!(stored, (intent.clone(), None));
        intent.require_budget()?;
        self.effects.fetch_add(1, Ordering::SeqCst);
        let receipt = ArchiveAbortReceipt {
            schema: "roboguide.archive-abort-receipt/v0.1".into(),
            abort_intent_digest: intent.digest()?,
            tombstone_digest: format!("sha256:{:x}", Sha256::digest(b"explicit-unit-tombstone")),
            fence: ArchiveCopierFence {
                schema: "roboguide.original-local-archive-copier-fence/v0.1".into(),
                source_owner: intent.source_owner.clone(),
                source_writer: ArchiveLeaseIdentity {
                    device: 1,
                    inode: 2,
                },
                archive_writer: ArchiveLeaseIdentity {
                    device: 1,
                    inode: 3,
                },
                process_id: 1,
                process_start_ticks: 1,
                boot_id: intent.boot_id.clone(),
                time_namespace: intent.time_namespace.clone(),
                observed_ms: intent.issued_ms,
            },
        };
        receipt.validate(intent)?;
        self.receipts
            .lock()
            .unwrap()
            .insert(intent.request.command_id.clone(), receipt.clone());
        Ok(receipt)
    }

    /// Returning an original contract receipt never adds another effect or substitutes a fresh budget.
    fn observe_abort(
        &self,
        _original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String> {
        assert!(self.log.connection.try_lock().is_ok());
        self.observations.fetch_add(1, Ordering::SeqCst);
        self.receipts
            .lock()
            .unwrap()
            .get(&intent.request.command_id)
            .cloned()
            .ok_or_else(|| "original unit tombstone unavailable".into())
    }

    /// The fixture only checks lock ordering; native TLS and writer proof have their own gate.
    fn require_commit_permission(&self) -> Result<(), String> {
        assert!(self.log.connection.try_lock().is_ok());
        Ok(())
    }
}

/// Persists one actual SQL authority checkpoint and then admits an unpublished original copy command.
fn original(
    directory: &Path,
) -> (
    SqliteEventLog,
    super::super::archive_operation::ArchiveRequest,
    ArchiveIntent,
) {
    use ports::EventSink as _;
    let log = SqliteEventLog::open(directory.join("events.sqlite3")).unwrap();
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(1),
        &CorrelationId::new("unit-copy").unwrap(),
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("unit-group").unwrap(),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("unit-mission").unwrap(),
                domain::TaskId::new("unit-task").unwrap(),
            ),
            reason: "explicit unit port".into(),
        },
    );
    let command = ControlCommandJournalEntry {
        command_id: "unit-checkpoint".into(),
        causation_id: None,
        authority_revision: 0,
        controller_epoch: None,
        plan_digest: None,
        input_digest: format!("sha256:{:x}", Sha256::digest(b"unit-input")),
        ownership_digest: None,
        resulting_state_digest: format!("sha256:{:x}", Sha256::digest(b"{}")),
        checkpoint_event_sequence: 1,
        checkpoint_schema: crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
    };
    log.save_checkpoint_with_command(crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA, "{}", command)
        .unwrap();
    log.commit_batch().unwrap();
    let request = super::super::archive_operation::ArchiveRequest {
        command_id: "unit-copy".into(),
        principal: "unit-admin".into(),
        tenant: "local".into(),
        source_owner: format!("artifact-store:{}", "a".repeat(64)),
        authority_id: log.archive_authority_id().unwrap(),
        destination: directory.join("archive").to_string_lossy().into(),
        budget_ms: 60_000,
    };
    assert!(
        log.archive_and_gc_with_catalog(1, &Unpublished, request.clone())
            .is_err()
    );
    let intent = log
        .archive_operation(&request.command_id)
        .unwrap()
        .unwrap()
        .0;
    (log, request, intent)
}

/// Uses only the exact original canonical input and an independent cleanup budget in contract tests.
fn cleanup(original: &ArchiveIntent, id: &str, budget_ms: u64) -> ArchiveAbortRequest {
    ArchiveAbortRequest {
        command_id: id.into(),
        original_command_id: original.request.command_id.clone(),
        original_intent_digest: original.digest().unwrap(),
        principal: "unit-recovery-admin".into(),
        tenant: original.request.tenant.clone(),
        budget_ms,
    }
}

/// A real SQL receipt fault cannot retire the source pin; exact query/retry uses one original effect.
#[test]
fn actual_sql_fault_preserves_original_copy_and_cleanup_reconciles_without_repeating_effect() {
    let directory = tempfile::tempdir().unwrap();
    let (log, request, original) = original(directory.path());
    let fixture = Fixture {
        log: log.clone(),
        receipts: Mutex::new(BTreeMap::new()),
        effects: AtomicUsize::new(0),
        observations: AtomicUsize::new(0),
    };
    let command = cleanup(&original, "first-cleanup", 30_000);
    let connection = Connection::open(directory.path().join("events.sqlite3")).unwrap();
    connection.execute_batch("CREATE TRIGGER abort_receipt_fault BEFORE UPDATE OF receipt_json ON event_archive_aborts BEGIN SELECT RAISE(ABORT,'original abort receipt SQL fault'); END").unwrap();
    assert!(log.abort_archive(command.clone(), &fixture).is_err());
    assert!(!log.archive_is_aborted(&request.command_id).unwrap());
    let pending = log
        .archive_abort_operation(&command.command_id)
        .unwrap()
        .unwrap();
    assert!(pending.1.is_none());
    assert_eq!(
        log.archive_operation(&request.command_id).unwrap().unwrap(),
        (original.clone(), None)
    );
    connection
        .execute_batch("DROP TRIGGER abort_receipt_fault")
        .unwrap();
    let receipt = log.abort_archive(command.clone(), &fixture).unwrap();
    assert_eq!(
        log.abort_archive(command.clone(), &fixture).unwrap(),
        receipt
    );
    assert_eq!(fixture.effects.load(Ordering::SeqCst), 1);
    assert_eq!(fixture.observations.load(Ordering::SeqCst), 2);
    assert!(log.archive_is_aborted(&request.command_id).unwrap());
    assert_eq!(
        log.archive_abort_operation(&command.command_id)
            .unwrap()
            .unwrap()
            .0,
        pending.0
    );
    assert_eq!(
        log.archive_operation(&request.command_id).unwrap().unwrap(),
        (original.clone(), None)
    );
    assert!(
        log.archive_and_gc_with_catalog(1, &Unpublished, request)
            .is_err()
    );
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
    let mut changed = command;
    changed.budget_ms -= 1;
    assert!(log.abort_archive(changed, &fixture).is_err());
    connection
        .execute(
            "UPDATE event_archive_operations SET state='compacted',receipt_json=?1",
            [format!("sha256:{}", "a".repeat(64))],
        )
        .unwrap();
    assert!(log.load_checkpoint().is_err());
    assert!(log.archive_abort_operation("first-cleanup").is_err());
}

/// A new explicit cleanup may independently fence work; neither original copy nor failed cleanup is renewed.
#[test]
fn expired_cleanup_retains_its_original_unknown_beside_independent_new_cleanup() {
    let directory = tempfile::tempdir().unwrap();
    let (log, _, original) = original(directory.path());
    let fixture = Fixture {
        log: log.clone(),
        receipts: Mutex::new(BTreeMap::new()),
        effects: AtomicUsize::new(0),
        observations: AtomicUsize::new(0),
    };
    let expired = cleanup(&original, "expired-cleanup", 1);
    assert!(log.abort_archive(expired.clone(), &fixture).is_err());
    let first = log
        .archive_abort_operation(&expired.command_id)
        .unwrap()
        .unwrap();
    assert!(log.abort_archive(expired.clone(), &fixture).is_err());
    assert_eq!(
        log.archive_abort_operation(&expired.command_id)
            .unwrap()
            .unwrap(),
        first
    );
    assert!(
        !log.archive_is_aborted(&original.request.command_id)
            .unwrap()
    );
    log.abort_archive(
        cleanup(&original, "independent-current-cleanup", 30_000),
        &fixture,
    )
    .unwrap();
    assert!(
        log.archive_is_aborted(&original.request.command_id)
            .unwrap()
    );
    assert_eq!(
        log.archive_abort_operation(&expired.command_id)
            .unwrap()
            .unwrap(),
        first
    );
    assert_eq!(
        log.archive_operation(&original.request.command_id)
            .unwrap()
            .unwrap(),
        (original, None)
    );
    assert_eq!(fixture.effects.load(Ordering::SeqCst), 1);
}

/// Earlier tombstone uncertainty cannot acquire a second complete receipt after another cleanup won.
#[test]
fn earlier_unknown_cleanup_cannot_publish_a_second_retirement_after_another_command_completed() {
    let directory = tempfile::tempdir().unwrap();
    let (log, _, original) = original(directory.path());
    let fixture = Fixture {
        log: log.clone(),
        receipts: Mutex::new(BTreeMap::new()),
        effects: AtomicUsize::new(0),
        observations: AtomicUsize::new(0),
    };
    let earlier = cleanup(&original, "earlier-uncertain-cleanup", 30_000);
    let connection = Connection::open(directory.path().join("events.sqlite3")).unwrap();
    connection.execute_batch("CREATE TRIGGER abort_receipt_fault BEFORE UPDATE OF receipt_json ON event_archive_aborts BEGIN SELECT RAISE(ABORT,'original abort receipt SQL fault'); END").unwrap();
    assert!(log.abort_archive(earlier.clone(), &fixture).is_err());
    let unknown = log.archive_abort_operation(&earlier.command_id).unwrap();
    connection
        .execute_batch("DROP TRIGGER abort_receipt_fault")
        .unwrap();
    let winner = cleanup(&original, "later-confirmed-cleanup", 30_000);
    let receipt = log.abort_archive(winner.clone(), &fixture).unwrap();
    assert!(log.abort_archive(earlier.clone(), &fixture).is_err());
    assert_eq!(
        log.archive_abort_operation(&earlier.command_id).unwrap(),
        unknown
    );
    assert_eq!(log.abort_archive(winner, &fixture).unwrap(), receipt);
    assert!(log.load_checkpoint().is_ok());
    assert_eq!(fixture.effects.load(Ordering::SeqCst), 2);
    assert_eq!(fixture.observations.load(Ordering::SeqCst), 1);
}
