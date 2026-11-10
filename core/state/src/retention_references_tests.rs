//! Exact source-epoch/sequence references, missing source proofs and rollback behavior.

use super::*;
use domain::*;

/// Creates an isolated reference authority inside an actual explicit writer transaction.
fn fixture() -> Connection {
    let connection = Connection::open_in_memory().unwrap();
    connection.execute_batch("CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT,timestamp_ms INTEGER,correlation_id TEXT,causation_id TEXT,payload_schema TEXT,payload_json TEXT); BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    connection
}

/// Retains all original source identity fields while varying only the requested epoch/sequence.
fn record(epoch: &str, sequence: u64) -> StateRecord {
    StateRecord::new_with_source_epoch(
        StateObjectRef::new(StateObjectClass::Node, "fixture", "node").unwrap(),
        StateSemantic::Reported,
        StateSource::Node {
            node_id: NodeId::new("node").unwrap(),
            local_system_id: LocalSystemId::new("sensor").unwrap(),
        },
        "pose",
        "fixture/v1",
        serde_json::json!({"value":1}),
        Some(TimestampMs::new(1)),
        TimestampMs::new(2),
        1000,
        None,
        Some(epoch.into()),
        sequence,
    )
    .unwrap()
}

/// Wraps one typed original payload without replacing its original global event sequence.
fn event(sequence: u64, payload: EventPayload) -> PersistedEvent {
    PersistedEvent {
        sequence,
        event_id: format!("original-event-{sequence}"),
        timestamp_ms: sequence,
        correlation_id: "original-correlation".into(),
        causation_id: None,
        payload_schema: "domain.EventPayload.json/v33".into(),
        payload_json: serde_json::to_string(&payload).unwrap(),
    }
}

/// Persists the actual source envelope before invoking the reference hook in the same transaction.
fn observe(connection: &Connection, event: &PersistedEvent) -> Result<(), SqliteEventLogError> {
    if !connection.is_autocommit() {
        connection.execute(
            "INSERT OR IGNORE INTO events VALUES(?1,?2,?3,?4,?5,?6,?7)",
            params![
                event.sequence,
                event.event_id,
                event.timestamp_ms,
                event.correlation_id,
                event.causation_id,
                event.payload_schema,
                event.payload_json
            ],
        )?;
    }
    super::observe(connection, event)
}

/// Constructs a protected decision with the exact State record originally selected by Control.
fn decision(record: StateRecord) -> EventPayload {
    EventPayload::StatePolicyDecision {
        task_ref: TaskRef::new(
            MissionId::new("mission").unwrap(),
            TaskId::new("task").unwrap(),
        ),
        receipts: vec![DecisionEvidenceReceipt {
            registry_digest: format!("sha256:{}", "a".repeat(64)),
            policy_id: "original-policy".into(),
            policy_revision: 1,
            decision: DecisionKind::Commit,
            node_id: NodeId::new("node").unwrap(),
            disposition: EvidenceDisposition::Accepted,
            condition_passed: true,
            record: Some(record),
            decided_at: TimestampMs::new(3),
        }],
    }
}

/// Same value with a distinct epoch is not substituted for the original consumed observation.
#[test]
fn decisions_pin_exact_source_identity_not_just_the_same_value() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(
        &connection,
        &event(
            1,
            EventPayload::StateRecordObserved {
                record: record("one", 1),
            },
        ),
    )
    .unwrap();
    observe(
        &connection,
        &event(
            2,
            EventPayload::StateRecordObserved {
                record: record("two", 1),
            },
        ),
    )
    .unwrap();
    observe(&connection, &event(3, decision(record("one", 1)))).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    validate(&connection, 3).unwrap();
    let proof = decision_references(&connection, 1, 3).unwrap();
    assert_eq!(proof.pins.len(), 1);
    assert_eq!(proof.pins[0].start_sequence, 1);
    assert_eq!(proof.pins[0].reference_id, "original-decision-event:3");
    assert!(proof.unresolved_decision_sequences.is_empty());
}

/// A selected record absent from the original event stream remains an explicit unresolved dependency.
#[test]
fn missing_source_never_turns_into_unreferenced_telemetry() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(&connection, &event(1, decision(record("unavailable", 1)))).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    let proof = decision_references(&connection, 1, 1).unwrap();
    assert!(proof.pins.is_empty());
    assert_eq!(proof.unresolved_decision_sequences, vec![1]);
}

/// Original coverage cannot skip a row, survive rollback or silently reset after a missing index head.
#[test]
fn transaction_rollback_and_incomplete_coverage_are_fail_closed() {
    let connection = fixture();
    let observation = event(
        1,
        EventPayload::StateRecordObserved {
            record: record("epoch", 1),
        },
    );
    assert!(observe(&connection, &observation).is_err());
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(&connection, &observation).unwrap();
    connection.execute_batch("ROLLBACK").unwrap();
    validate(&connection, 0).unwrap();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    assert!(observe(&connection, &event(2, decision(record("epoch", 1)))).is_err());
    connection.execute_batch("ROLLBACK").unwrap();
    assert!(decision_references(&connection, 1, 1).is_err());
    connection
        .execute("DELETE FROM retention_reference_head", [])
        .unwrap();
    assert!(validate(&connection, 0).is_err());
}

/// Query responses remain bounded while preserving a visible indication of further unresolved work.
#[test]
fn unresolved_decision_queries_do_not_materialize_unbounded_history() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    for sequence in 1..=130 {
        observe(
            &connection,
            &event(sequence, decision(record("absent", sequence))),
        )
        .unwrap();
    }
    connection.execute_batch("COMMIT").unwrap();
    let proof = decision_references(&connection, 1, 130).unwrap();
    assert_eq!(proof.unresolved_decision_sequences.len(), 128);
    assert!(proof.more_unresolved);
    assert!(decision_references(&connection, 1, 8193).is_err());
}

/// Dropping or replacing a protected edge or its source identity cannot silently remove a pin.
#[test]
fn deleted_and_replaced_reference_metadata_is_refused_before_assessment() {
    for damage in [
        "DELETE FROM retention_reference_edges",
        "DELETE FROM retention_reference_records",
        "UPDATE retention_reference_edges SET record_digest='replaced'",
        "UPDATE retention_reference_records SET record_digest='replaced'",
        "UPDATE retention_reference_head SET through_sequence=0",
    ] {
        let connection = fixture();
        connection.execute_batch("BEGIN IMMEDIATE").unwrap();
        observe(
            &connection,
            &event(
                1,
                EventPayload::StateRecordObserved {
                    record: record("one", 1),
                },
            ),
        )
        .unwrap();
        observe(&connection, &event(2, decision(record("one", 1)))).unwrap();
        connection.execute_batch("COMMIT").unwrap();
        validate(&connection, 2).unwrap();
        connection.execute(damage, []).unwrap();
        assert!(validate(&connection, 2).is_err(), "{damage}");
        assert!(decision_references(&connection, 1, 2).is_err(), "{damage}");
    }
}

/// A migrated archive floor never proves the absent prefix's original dependency history.
#[test]
fn existing_archive_prefix_stays_explicitly_unresolved() {
    let connection = Connection::open_in_memory().unwrap();
    connection.execute_batch("CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT,timestamp_ms INTEGER,correlation_id TEXT,causation_id TEXT,payload_schema TEXT,payload_json TEXT); INSERT INTO event_log_metadata VALUES('history_floor','100'); BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    observe(
        &connection,
        &event(
            101,
            EventPayload::StateRecordObserved {
                record: record("new", 1),
            },
        ),
    )
    .unwrap();
    connection.execute_batch("COMMIT").unwrap();
    let proof = decision_references(&connection, 101, 101).unwrap();
    assert!(proof.archived_prefix_unresolved);
}

/// A corrupt mutable source index cannot convert a different same-value epoch into original proof.
#[test]
fn decision_admission_rechecks_the_actual_original_observation() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(
        &connection,
        &event(
            1,
            EventPayload::StateRecordObserved {
                record: record("one", 1),
            },
        ),
    )
    .unwrap();
    connection.execute_batch("COMMIT").unwrap();
    connection
        .execute(
            "UPDATE retention_reference_records SET record_digest=?1",
            [record_identity(&record("two", 1)).unwrap()],
        )
        .unwrap();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    assert!(observe(&connection, &event(2, decision(record("two", 1)))).is_err());
    connection.execute_batch("ROLLBACK").unwrap();
    assert_eq!(through_sequence(&connection).unwrap(), 1);
}

/// Selected archived evidence needs its own actual archive reader before complete coverage is claimed.
#[test]
fn newly_consumed_archived_source_remains_an_explicit_gap() {
    let connection = fixture();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(
        &connection,
        &event(
            1,
            EventPayload::StateRecordObserved {
                record: record("one", 1),
            },
        ),
    )
    .unwrap();
    connection.execute_batch("COMMIT").unwrap();
    connection
        .execute("DELETE FROM events WHERE sequence=1", [])
        .unwrap();
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    observe(&connection, &event(2, decision(record("one", 1)))).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    let proof = decision_references(&connection, 1, 2).unwrap();
    assert!(proof.pins.is_empty());
    assert_eq!(proof.unresolved_decision_sequences, vec![2]);
}
