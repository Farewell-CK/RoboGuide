//! Original command receipts, bounded assessments and clock/reference safety under real SQL.

use super::*;
use domain::{LeaseId, NodeId};

/// Creates an isolated policy store and original event table under the real initialization contract.
fn fixture() -> Connection {
    let connection = Connection::open_in_memory().unwrap();
    connection.execute_batch("CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT,timestamp_ms INTEGER,correlation_id TEXT,causation_id TEXT,payload_schema TEXT,payload_json TEXT); BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    connection
}

/// Adds a typed original event without changing policy or any freshness information.
fn put(connection: &Connection, sequence: u64, timestamp: u64, protected: bool) {
    let payload = if protected {
        EventPayload::NodeRegistered {
            node_id: NodeId::new("node").unwrap(),
            lease_id: LeaseId::new("lease").unwrap(),
        }
    } else {
        EventPayload::NodeHeartbeatAccepted {
            node_id: NodeId::new("node").unwrap(),
            lease_id: LeaseId::new("lease").unwrap(),
        }
    };
    connection.execute("INSERT INTO events VALUES(?1,?2,?3,'correlation',NULL,'domain.EventPayload.json/v33',?4)",params![sequence,format!("event-{sequence}"),timestamp,serde_json::to_string(&payload).unwrap()]).unwrap();
}

/// Builds an explicitly covered reference snapshot; an empty default is intentionally insufficient.
fn covered(sequence: u64) -> RetentionProtection {
    RetentionProtection {
        complete_through_sequence: sequence,
        pins: Vec::new(),
    }
}

/// Commits a complete administrator command using the same outer FIFO transaction as production.
fn set_policy(
    connection: &Connection,
    command: &RetentionPolicyCommand,
    now_ms: u64,
) -> RetentionPolicyReceipt {
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    let receipt = update_policy(connection, command, now_ms).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    receipt
}

/// Initial migration chooses evaluation only and retains the exact approved engineering defaults.
#[test]
fn default_policy_never_authorizes_removal() {
    let connection = fixture();
    validate(&connection).unwrap();
    let current = policy(&connection).unwrap();
    assert_eq!(current.revision, 0);
    assert_eq!(current.policy.mode, RetentionMode::Evaluate);
    assert_eq!(current.policy.hot_window_ms, 86_400_000);
    assert_eq!(current.policy.archive_window_ms, 2_592_000_000);
    assert_eq!(current.policy.max_events, 8192);
    assert_eq!(current.policy.max_input_bytes, 4 * 1024 * 1024);
    put(&connection, 1, 1, false);
    let status = evaluate(&connection, 86_400_002, 1, &covered(1)).unwrap();
    assert_eq!(status.batch.unwrap().ordinary_events, 1);
    assert!(
        status
            .blockers
            .iter()
            .any(|blocker| blocker.contains("evaluation mode"))
    );
    assert_eq!(
        connection
            .query_row("SELECT COUNT(*) FROM events", [], |row| row
                .get::<_, u64>(0))
            .unwrap(),
        1
    );
}

/// Routine telemetry and authority evidence are archived into separate contiguous sequence ranges.
#[test]
fn candidate_stops_before_first_evidence_class_change() {
    let connection = fixture();
    put(&connection, 1, 1, false);
    put(&connection, 2, 2, true);
    put(&connection, 3, 3, false);
    let status = evaluate(&connection, 100_000_000, 3, &covered(3)).unwrap();
    let batch = status.batch.unwrap();
    assert_eq!(
        (
            batch.start_sequence,
            batch.end_sequence,
            batch.ordinary_events,
            batch.protected_events
        ),
        (1, 1, 1, 0)
    );
}

/// RoboGuide-owned State values never inherit ordinary-retention permission from their event variant.
#[test]
fn roboguide_state_remains_protected_while_node_reports_are_ordinary() {
    use domain::*;
    let make = |source: StateSource, semantic: StateSemantic, class: StateObjectClass| {
        EventPayload::StateRecordObserved {
            record: StateRecord::new_with_source_epoch(
                StateObjectRef::new(class, "fixture", "original").unwrap(),
                semantic,
                source,
                "channel",
                "fixture/v1",
                serde_json::json!({"value":1}),
                None,
                TimestampMs::new(1),
                1000,
                None,
                None,
                1,
            )
            .unwrap(),
        }
    };
    assert!(ordinary(&make(
        StateSource::Node {
            node_id: NodeId::new("node").unwrap(),
            local_system_id: LocalSystemId::new("sensor").unwrap()
        },
        StateSemantic::Reported,
        StateObjectClass::Node
    )));
    for semantic in [
        StateSemantic::Desired,
        StateSemantic::Committed,
        StateSemantic::Derived,
        StateSemantic::Belief,
    ] {
        assert!(!ordinary(&make(
            StateSource::RoboGuide {
                component: "control".into()
            },
            semantic,
            StateObjectClass::RoboGuide
        )));
    }
}

/// Lost replies return the original receipt even after later accepted policy changes and clock skew.
#[test]
fn retries_preserve_original_receipt_and_conflicts_fail() {
    let connection = fixture();
    let command = RetentionPolicyCommand {
        command_id: "policy-first".into(),
        expected_revision: 0,
        policy: RetentionPolicy {
            mode: RetentionMode::Enabled,
            ..RetentionPolicy::default()
        },
    };
    let first = set_policy(&connection, &command, 100);
    let second = RetentionPolicyCommand {
        command_id: "policy-second".into(),
        expected_revision: 1,
        policy: RetentionPolicy::default(),
    };
    set_policy(&connection, &second, 200);
    validate(&connection).unwrap();
    assert_eq!(set_policy(&connection, &command, 1), first);
    assert_eq!(policy(&connection).unwrap().clock_high_water_ms, 200);
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    let mut conflict = command.clone();
    conflict.policy.hot_window_ms += 1;
    assert!(update_policy(&connection, &conflict, 300).is_err());
    conflict.command_id = "new-but-stale".into();
    assert!(update_policy(&connection, &conflict, 300).is_err());
    connection.execute_batch("ROLLBACK").unwrap();
    assert_eq!(policy(&connection).unwrap().revision, 2);
}

/// SQL rollback cannot leave a new policy without its original acceptance receipt.
#[test]
fn policy_and_receipt_rollback_together_and_missing_receipt_is_refused() {
    let connection = fixture();
    let command = RetentionPolicyCommand {
        command_id: "rollback".into(),
        expected_revision: 0,
        policy: RetentionPolicy {
            mode: RetentionMode::Enabled,
            ..RetentionPolicy::default()
        },
    };
    assert!(update_policy(&connection, &command, 100).is_err());
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    update_policy(&connection, &command, 100).unwrap();
    connection.execute_batch("ROLLBACK").unwrap();
    assert_eq!(policy(&connection).unwrap().revision, 0);
    set_policy(&connection, &command, 100);
    connection
        .execute("DELETE FROM retention_policy_commands", [])
        .unwrap();
    assert!(validate(&connection).is_err());
}

/// The monotonic floor survives reassessment; backwards time never deletes or renews evidence.
#[test]
fn clock_regression_and_missing_reference_coverage_withhold_work() {
    let connection = fixture();
    put(&connection, 1, 1, false);
    let now = 86_400_100;
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe_clock(&connection, now).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    let backward = evaluate(&connection, now - 1, 1, &covered(1)).unwrap();
    assert!(backward.batch.is_none());
    assert!(
        backward
            .blockers
            .iter()
            .any(|blocker| blocker.contains("clock regressed"))
    );
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    assert!(observe_clock(&connection, now - 1).is_err());
    connection.execute_batch("ROLLBACK").unwrap();
    let unknown = evaluate(&connection, now, 1, &RetentionProtection::default()).unwrap();
    assert!(unknown.batch.is_none());
    assert!(
        unknown
            .blockers
            .iter()
            .any(|blocker| blocker.contains("coverage is incomplete"))
    );
}

/// Typed routine telemetry remains distinct from authority evidence and explicit task/Unknown pins.
#[test]
fn classification_preserves_authority_and_exact_reference_pins() {
    let connection = fixture();
    put(&connection, 1, 1, true);
    put(&connection, 2, 2, false);
    put(&connection, 3, 3, false);
    let mut protection = covered(3);
    protection.pins.push(RetentionPin {
        reference_id: "unknown:original-task".into(),
        start_sequence: 2,
        end_sequence: 3,
    });
    let status = evaluate(&connection, 86_400_100, 3, &protection).unwrap();
    let batch = status.batch.unwrap();
    assert_eq!((batch.ordinary_events, batch.protected_events), (0, 1));
    assert!(batch.protecting_references.is_empty());
    assert!(status.more_rows);
    connection
        .execute(
            "INSERT INTO event_log_metadata VALUES('history_floor','1')",
            [],
        )
        .unwrap();
    connection
        .execute("DELETE FROM events WHERE sequence=1", [])
        .unwrap();
    let tail = evaluate(&connection, 86_400_100, 3, &protection).unwrap();
    let batch = tail.batch.unwrap();
    assert_eq!((batch.ordinary_events, batch.protected_events), (2, 0));
    assert_eq!(batch.protecting_references, vec!["unknown:original-task"]);
    assert!(
        tail.blockers
            .iter()
            .any(|blocker| blocker.contains("active protection"))
    );
    assert!(!tail.more_rows);
}

/// Large histories are assessed only through one 8192-row prefix and remain entirely intact.
#[test]
fn assessment_is_bounded_without_counting_the_entire_history() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    for sequence in 1..=8193 {
        put(&connection, sequence, sequence, false);
    }
    connection.execute_batch("COMMIT").unwrap();
    let status = evaluate(&connection, 86_500_000, 8193, &covered(8193)).unwrap();
    assert_eq!(status.batch.unwrap().event_count, 8192);
    assert!(status.more_rows);
    assert_eq!(
        connection
            .query_row("SELECT COUNT(*) FROM events", [], |row| row
                .get::<_, u64>(0))
            .unwrap(),
        8193
    );
}

/// The byte bound is enforced before decoding the next full payload, preserving a fitting prefix.
#[test]
fn input_byte_budget_and_checkpoint_cover_are_hard_limits() {
    let connection = fixture();
    put(&connection, 1, 1, false);
    put(&connection, 2, 2, false);
    let first: PersistedEvent = connection.query_row("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence=1", [], event_from_row).unwrap();
    let command = RetentionPolicyCommand {
        command_id: "small-budget".into(),
        expected_revision: 0,
        policy: RetentionPolicy {
            max_input_bytes: original_bytes(&first).unwrap(),
            ..RetentionPolicy::default()
        },
    };
    set_policy(&connection, &command, 1);
    let status = evaluate(&connection, 86_400_100, 2, &covered(2)).unwrap();
    assert_eq!(status.batch.unwrap().end_sequence, 1);
    assert!(status.more_rows);
    let uncovered = evaluate(&connection, 86_400_100, 0, &covered(2)).unwrap();
    assert!(uncovered.batch.is_none());
    assert!(
        uncovered
            .blockers
            .iter()
            .any(|blocker| blocker.contains("checkpoint"))
    );
}

/// Holes, replacement policies, corrupt receipts and unknown schemas fail closed.
#[test]
fn corrupt_authority_and_missing_original_events_are_not_empty_history() {
    let connection = fixture();
    put(&connection, 2, 1, false);
    assert!(evaluate(&connection, 86_400_100, 2, &covered(2)).is_err());
    let invalid_policy = RetentionPolicy {
        schema: "roboguide.retention-policy/future".into(),
        ..RetentionPolicy::default()
    };
    assert!(invalid_policy.validate().is_err());
    let invalid_policy = RetentionPolicy {
        max_input_bytes: MAX_RETENTION_INPUT_BYTES + 1,
        ..RetentionPolicy::default()
    };
    assert!(invalid_policy.validate().is_err());
    connection
        .execute("UPDATE retention_policy SET policy_json='{}'", [])
        .unwrap();
    assert!(validate(&connection).is_err());
}

/// Records at the exact hot boundary, and future timestamps, remain hot rather than expiring early.
#[test]
fn equality_at_hot_cutoff_is_retained() {
    let connection = fixture();
    put(&connection, 1, 100, false);
    let boundary = evaluate(&connection, 86_400_100, 1, &covered(1)).unwrap();
    assert!(boundary.batch.is_none());
    let after = evaluate(&connection, 86_400_101, 1, &covered(1)).unwrap();
    assert_eq!(after.batch.unwrap().event_count, 1);
}
