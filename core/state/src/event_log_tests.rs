//! Durable event log and checkpoint tests.

use super::*;
use domain::{
    ContentDigest, ContextRoleId, CoordinationContextId, CorrelationId, EventPayload,
    ExecutionGroupId, LocalSystemId, LocalizationFrames, LocalizationVerificationEvidence,
    MapArtifactManifest, MapArtifactRef, MapId, MapRevisionId, MapRevisionSelector,
    MemoryArtifactManifest, MemoryArtifactRef, MemoryId, MemoryKind, MemoryOwner, MemoryRevisionId,
    MemoryScope, MemorySelector, MemoryVisibility, MissionId, NodeId, PoseQualityComparison,
    PoseQualityEvidence, RoleId, SpatialAnchorId, StateObjectClass, StateObjectRef, StateRecord,
    StateSemantic, StateSource, TaskId, TaskRef, TimestampMs,
};
use ports::MemoryCatalogReader;
use tempfile::tempdir;

/// Builds one valid map manifest shared by payload-version compatibility tests.
fn manifest() -> MapArtifactManifest {
    MapArtifactManifest::new(
        MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("warehouse").expect("map id is valid"),
                MapRevisionId::new("r1").expect("revision id is valid"),
            ),
            ContentDigest::new(format!("sha256:{}", "a".repeat(64))).expect("digest is valid"),
            10,
        ),
        "application/octet-stream",
        "grid-v1",
        NodeId::new("dog-a").expect("node id is valid"),
        None,
        MissionId::new("mission-build").expect("mission id is valid"),
        Some("execution-build".to_string()),
        None,
        "map",
        "enu",
        SpatialAnchorId::new("warehouse-origin").expect("anchor is valid"),
        Some(0.05),
        TimestampMs::new(10),
        None,
    )
    .expect("manifest is valid")
}

/// Builds one valid v4-only localization evidence payload.
fn localization_evidence_payload() -> EventPayload {
    let manifest = manifest();
    let mission_id = MissionId::new("mission-localize").expect("mission id is valid");
    let evidence = LocalizationVerificationEvidence::new(
        manifest.artifact().clone(),
        mission_id.clone(),
        TaskRef::new(
            mission_id,
            TaskId::new("verify-map").expect("task id is valid"),
        ),
        ExecutionGroupId::new("group-localize").expect("group id is valid"),
        RoleId::new("localizer").expect("role id is valid"),
        NodeId::new("dog-b").expect("node id is valid"),
        "execution-verify",
        "attempt-verify",
        "warehouse-local",
        "localization",
        PoseQualityEvidence::new(
            "translation_stddev",
            "0.08",
            "0.10",
            "m",
            PoseQualityComparison::AtMost,
        )
        .expect("pose quality is valid"),
        LocalizationFrames::new("map", "odom", "base_link").expect("frames are valid"),
        manifest.anchor_id().clone(),
        TimestampMs::new(20),
    )
    .expect("localization evidence is valid");
    EventPayload::MapLocalizationEvidenceRecorded { evidence }
}

/// Builds one v5-only execution relation registration payload.
fn execution_relation_payload() -> EventPayload {
    let mission_id = MissionId::new("mission-relation").expect("mission id is valid");
    EventPayload::ExecutionRelationRegistered {
        group_id: ExecutionGroupId::new("group-relation").expect("group id is valid"),
        relation_id: domain::ExecutionRelationId::new("safety-guards-navigation")
            .expect("relation id is valid"),
        source_task_ref: TaskRef::new(
            mission_id.clone(),
            TaskId::new("observe-safety").expect("task id is valid"),
        ),
        source_role_id: RoleId::new("safety-observer").expect("role id is valid"),
        target_task_ref: TaskRef::new(
            mission_id,
            TaskId::new("navigate").expect("task id is valid"),
        ),
        target_role_id: RoleId::new("navigator").expect("role id is valid"),
        kind: domain::ExecutionRelationKind::RequiresActive,
        relation_type: domain::ExecutionRelationType::RequiresActive,
        coupling_mode: domain::ExecutionCouplingMode::Independent,
    }
}

/// Builds one v8-only typed relation registration payload with non-default coupling.
fn typed_execution_relation_payload() -> EventPayload {
    let EventPayload::ExecutionRelationRegistered {
        group_id,
        relation_id,
        source_task_ref,
        source_role_id,
        target_task_ref,
        target_role_id,
        ..
    } = execution_relation_payload()
    else {
        unreachable!("relation fixture always returns a registration event")
    };
    EventPayload::ExecutionRelationRegistered {
        group_id,
        relation_id,
        source_task_ref,
        source_role_id,
        target_task_ref,
        target_role_id,
        kind: domain::ExecutionRelationKind::GroupMemberState,
        relation_type: domain::ExecutionRelationType::GroupMemberState {
            state_key: "user-contact".to_string(),
        },
        coupling_mode: domain::ExecutionCouplingMode::ConcurrentCooperation,
    }
}

/// Builds one v9-only admitted Local EAIOS peer readiness payload.
fn peer_channel_readiness_payload() -> EventPayload {
    EventPayload::PeerChannelReadinessObserved {
        group_id: ExecutionGroupId::new("group-guidance").expect("group id is valid"),
        context_id: CoordinationContextId::new("guidance").expect("context id is valid"),
        context_role_id: ContextRoleId::new("guide").expect("ContextRole id is valid"),
        node_id: NodeId::new("dog-a").expect("Node id is valid"),
        local_system_id: LocalSystemId::new("motion").expect("Local System id is valid"),
        session_id: "session-dog-a".to_string(),
        channel_instance_id: "channel-guidance-1".to_string(),
        profile_id: "guidance-peer".to_string(),
        message_schema: "guidance/v1".to_string(),
        sequence: 4,
        expires_at: TimestampMs::new(5_000),
        ready: true,
    }
}

/// Builds one v6-only source-aware State evidence payload.
fn state_record_payload() -> EventPayload {
    EventPayload::StateRecordObserved {
        record: StateRecord::new(
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("State object is valid"),
            StateSemantic::Observed,
            StateSource::Node {
                node_id: NodeId::new("cane-a").expect("node id is valid"),
                local_system_id: LocalSystemId::new("safety").expect("local system id is valid"),
            },
            "hazards",
            "example.hazard/v1",
            serde_json::json!({"present": true}),
            None,
            TimestampMs::new(40),
            1_000,
            None,
            1,
        )
        .expect("State record is valid"),
    }
}

/// Builds one generic exchangeable Memory manifest for v6/v7 compatibility tests.
fn generic_memory_manifest() -> MemoryArtifactManifest {
    MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("history-a").expect("Memory id is valid"),
            MemoryRevisionId::new("r1").expect("Memory revision is valid"),
        ),
        MemoryKind::Execution,
        "history-producer",
        MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("producer Node id is valid"),
            local_system_id: LocalSystemId::new("history").expect("local system id is valid"),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "example.history/v1",
        "application/json",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("b".repeat(64)).expect("digest is valid"),
            10,
        )),
        None,
        None,
        None,
        TimestampMs::new(50),
    )
    .expect("Memory manifest is valid")
}

/// Builds one v7 provider-qualified replica payload.
fn memory_replica_payload(consumer_provider_id: &str) -> EventPayload {
    EventPayload::MemoryArtifactStaged {
        manifest: generic_memory_manifest(),
        node_id: NodeId::new("dog-b").expect("consumer Node id is valid"),
        consumer_provider_id: consumer_provider_id.to_string(),
    }
}

/// WAL storage survives reopening and preserves causal envelope fields.
#[test]
fn sqlite_event_log_survives_reopen() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events.sqlite3");
    let correlation = CorrelationId::new("test-correlation").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(10),
        &correlation,
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission-a").expect("id valid"),
                domain::TaskId::new("task-a").expect("id valid"),
            ),
            reason: "test".to_string(),
        },
    );
    drop(log);
    let reopened = SqliteEventLog::open(&path).expect("event log reopens");
    let events = reopened.events().expect("events are readable");
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].event_id, "event-1");
    assert_eq!(events[0].correlation_id, "test-correlation");
    assert_eq!(events[0].payload_schema, EVENT_PAYLOAD_SCHEMA_V11);
    let payload: EventPayload =
        serde_json::from_str(&events[0].payload_json).expect("payload codec is readable");
    assert!(matches!(
        payload,
        EventPayload::ExecutionGroupBlocked { .. }
    ));
    assert_eq!(reopened.decoded_events().expect("events decode").len(), 1);
    assert_eq!(
        reopened.latest_timestamp().expect("latest timestamp reads"),
        TimestampMs::new(10)
    );
}

/// A v10 row cannot claim Task satisfaction evidence introduced by codec v11.
#[test]
fn event_decoder_rejects_task_satisfaction_under_v10_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory
        .path()
        .join("events-task-satisfaction-v11.sqlite3");
    let correlation = CorrelationId::new("task-satisfaction-v11").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(50),
        &correlation,
        None,
        EventPayload::TaskSatisfied {
            group_id: ExecutionGroupId::new("group-a").expect("group id valid"),
            task_ref: TaskRef::new(
                MissionId::new("mission-a").expect("mission id valid"),
                TaskId::new("task-a").expect("task id valid"),
            ),
            basis: domain::TaskSatisfactionBasis::ExecutionReport,
        },
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V10],
        )
        .expect("fixture marker changes to v10");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v11")
    ));
}

/// The current decoder retains the previous v2 JSON path after v3 Spatial variants ship.
#[test]
fn event_decoder_retains_v2_payload_compatibility() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-v2.sqlite3");
    let correlation = CorrelationId::new("v2-compatibility").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(10),
        &correlation,
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group-v2").expect("id valid"),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission-v2").expect("id valid"),
                domain::TaskId::new("task-v2").expect("id valid"),
            ),
            reason: "compatibility".to_string(),
        },
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V2],
        )
        .expect("fixture marker changes to v2");

    let decoded = log.decoded_events().expect("v2 payload remains readable");
    assert!(matches!(
        decoded[0].payload(),
        EventPayload::ExecutionGroupBlocked { reason, .. } if reason == "compatibility"
    ));
}

/// A v2 marker cannot masquerade a Spatial Memory variant introduced by codec v3.
#[test]
fn event_decoder_rejects_spatial_payload_under_v2_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-spatial-v2.sqlite3");
    let correlation = CorrelationId::new("spatial-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(10),
        &correlation,
        None,
        EventPayload::MapArtifactDeclared {
            manifest: manifest(),
        },
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V2],
        )
        .expect("fixture marker changes to v2");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v3")
    ));
}

/// A v3 marker cannot masquerade strong localization evidence introduced by codec v4.
#[test]
fn event_decoder_rejects_strong_evidence_under_v3_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-evidence-v3.sqlite3");
    let correlation = CorrelationId::new("evidence-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(20),
        &correlation,
        None,
        localization_evidence_payload(),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V3],
        )
        .expect("fixture marker changes to v3");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v4")
    ));
}

/// A v4 marker cannot masquerade relation evidence introduced by codec v5.
#[test]
fn event_decoder_rejects_relation_payload_under_v4_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-relation-v4.sqlite3");
    let correlation = CorrelationId::new("relation-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(30),
        &correlation,
        None,
        execution_relation_payload(),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V4],
        )
        .expect("fixture marker changes to v4");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v5")
    ));
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V5],
        )
        .expect("fixture marker changes to v5");
    assert_eq!(
        log.decoded_events()
            .expect("legacy relation remains v5-compatible")[0]
            .payload(),
        &execution_relation_payload()
    );
}

/// A v7 marker cannot masquerade typed relation/coupling evidence introduced by codec v8.
#[test]
fn event_decoder_rejects_typed_relation_payload_under_v7_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-typed-relation-v7.sqlite3");
    let correlation = CorrelationId::new("typed-relation-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(31),
        &correlation,
        None,
        typed_execution_relation_payload(),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V7],
        )
        .expect("fixture marker changes to v7");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v8")
    ));
}

/// A v8 marker cannot masquerade identified readiness evidence introduced by codec v9.
#[test]
fn event_decoder_rejects_peer_readiness_payload_under_v8_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-peer-readiness-v8.sqlite3");
    let correlation = CorrelationId::new("peer-readiness-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(31),
        &correlation,
        None,
        peer_channel_readiness_payload(),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V8],
        )
        .expect("fixture marker changes to v8");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v9")
    ));
}

/// A v5 marker cannot masquerade State/Memory evidence introduced by codec v6.
#[test]
fn event_decoder_rejects_state_payload_under_v5_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-state-v5.sqlite3");
    let correlation = CorrelationId::new("state-version").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(40),
        &correlation,
        None,
        state_record_payload(),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V5],
        )
        .expect("fixture marker changes to v5");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v6")
    ));
}

/// v6 replica evidence replays into an explicit legacy provider bucket without guessing.
#[test]
fn event_decoder_migrates_v6_replica_without_provider_identity() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-memory-v6.sqlite3");
    let correlation = CorrelationId::new("memory-v6").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    let manifest = generic_memory_manifest();
    log.append(
        TimestampMs::new(50),
        &correlation,
        None,
        EventPayload::MemoryManifestPublished {
            manifest: manifest.clone(),
        },
    );
    log.append(
        TimestampMs::new(51),
        &correlation,
        None,
        memory_replica_payload("archive-a"),
    );
    let mut payload: serde_json::Value =
        serde_json::from_str(&log.events().expect("events are readable")[1].payload_json)
            .expect("replica payload is JSON");
    payload["MemoryArtifactStaged"]
        .as_object_mut()
        .expect("replica variant is an object")
        .remove("consumer_provider_id");
    let payload = serde_json::to_string(&payload).expect("legacy payload serializes");
    let connection = log
        .connection
        .lock()
        .expect("event connection lock is available");
    connection
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V6],
        )
        .expect("manifest marker changes to v6");
    connection
        .execute(
            "UPDATE events SET payload_schema = ?1, payload_json = ?2 WHERE sequence = 2",
            rusqlite::params![EVENT_PAYLOAD_SCHEMA_V6, payload],
        )
        .expect("replica row becomes a historical v6 fixture");
    drop(connection);

    let decoded = log.decoded_events().expect("v6 Memory evidence decodes");
    let projection =
        crate::MemoryCatalogProjection::from_events(decoded).expect("v6 Memory evidence replays");
    let replicas = projection.memory_replicas(manifest.selector());
    assert_eq!(replicas.len(), 1);
    assert_eq!(
        replicas[0].consumer_provider_id(),
        domain::LEGACY_MEMORY_CONSUMER_PROVIDER_ID
    );
}

/// A v6 marker cannot claim provider-qualified replica evidence introduced by codec v7.
#[test]
fn event_decoder_rejects_provider_qualified_replica_under_v6_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events-memory-v7.sqlite3");
    let correlation = CorrelationId::new("memory-v7").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(50),
        &correlation,
        None,
        memory_replica_payload("archive-a"),
    );
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
            [EVENT_PAYLOAD_SCHEMA_V6],
        )
        .expect("fixture marker changes to v6");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v7")
    ));
}

/// A v7 row cannot use the v6 migration default to conceal a missing provider identity.
#[test]
fn event_decoder_rejects_missing_provider_identity_under_v7_marker() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory
        .path()
        .join("events-memory-v7-missing-provider.sqlite3");
    let correlation = CorrelationId::new("memory-v7-missing").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.append(
        TimestampMs::new(50),
        &correlation,
        None,
        memory_replica_payload("archive-a"),
    );
    let mut payload: serde_json::Value =
        serde_json::from_str(&log.events().expect("events are readable")[0].payload_json)
            .expect("replica payload is JSON");
    payload["MemoryArtifactStaged"]
        .as_object_mut()
        .expect("replica variant is an object")
        .remove("consumer_provider_id");
    let payload = serde_json::to_string(&payload).expect("invalid v7 fixture serializes");
    log.connection
        .lock()
        .expect("event connection lock is available")
        .execute(
            "UPDATE events SET payload_json = ?1 WHERE sequence = 1",
            [payload],
        )
        .expect("fixture removes provider identity");

    assert!(matches!(
        log.decoded_events(),
        Err(SqliteEventLogError::Codec(reason))
            if reason.contains("v7 requires Memory consumer provider identity")
    ));
}

/// Append sequence, rather than lexical event identity, defines stable order and paging.
#[test]
fn event_sequence_orders_double_digit_ids_and_pages() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("events.sqlite3");
    let correlation = CorrelationId::new("sequence-test").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    for _ in 0..12 {
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: "test".to_string(),
            },
        );
    }
    let events = log.events().expect("events are readable");
    assert_eq!(events[9].event_id, "event-10");
    assert_eq!(events[10].event_id, "event-11");
    let page = log.events_page(Some(10), 2).expect("page is readable");
    assert_eq!(
        page.iter().map(|event| event.sequence).collect::<Vec<_>>(),
        [11, 12]
    );
}

/// The schema migration preserves legacy rows while assigning replay sequence values.
#[test]
fn legacy_debug_payload_schema_migrates() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("legacy.sqlite3");
    let connection = rusqlite::Connection::open(&path).expect("legacy database opens");
    connection
        .execute_batch(
            "CREATE TABLE events (
                event_id TEXT PRIMARY KEY NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                payload_schema TEXT NOT NULL,
                payload_debug TEXT NOT NULL
            );
            INSERT INTO events VALUES ('event-1', 10, 'legacy', NULL,
                'domain.EventPayload.debug/v0', 'legacy-payload');",
        )
        .expect("legacy schema is created");
    drop(connection);
    let log = SqliteEventLog::open(&path).expect("legacy schema migrates");
    let events = log.events().expect("migrated events are readable");
    assert_eq!(events[0].sequence, 1);
    assert_eq!(events[0].payload_json, "legacy-payload");
}

/// A rolled-back event batch leaves no rows and reuses the uncommitted sequence.
#[test]
fn event_batch_rolls_back_rows_and_sequence() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("batch.sqlite3");
    let correlation = CorrelationId::new("batch-test").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.begin_batch().expect("batch begins");
    log.append(
        TimestampMs::new(10),
        &correlation,
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission-a").expect("id valid"),
                domain::TaskId::new("task-a").expect("id valid"),
            ),
            reason: "rollback".to_string(),
        },
    );
    log.rollback_batch().expect("batch rolls back");
    assert!(log.is_empty().expect("event log is readable"));
    log.append(
        TimestampMs::new(20),
        &correlation,
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission-a").expect("id valid"),
                domain::TaskId::new("task-a").expect("id valid"),
            ),
            reason: "committed".to_string(),
        },
    );
    let events = log.events().expect("events are readable");
    assert_eq!(events[0].sequence, 1);
    assert_eq!(events[0].event_id, "event-1");
}

/// A committed event batch makes all appended rows visible together.
#[test]
fn event_batch_commits_multiple_events() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("batch.sqlite3");
    let correlation = CorrelationId::new("batch-test").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.begin_batch().expect("batch begins");
    assert!(matches!(
        log.events(),
        Err(SqliteEventLogError::Codec(reason)) if reason.contains("still open")
    ));
    for reason in ["first", "second"] {
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: reason.to_string(),
            },
        );
    }
    log.commit_batch().expect("batch commits");
    assert_eq!(log.len().expect("event log is readable"), 2);
}

/// Checkpoint data commits with its event sequence and survives reopening.
#[test]
fn controller_checkpoint_commits_with_event_batch() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("checkpoint.sqlite3");
    let correlation = CorrelationId::new("checkpoint-test").expect("correlation valid");
    let mut log = SqliteEventLog::open(&path).expect("event log opens");
    log.begin_batch().expect("batch begins");
    log.append(
        TimestampMs::new(10),
        &correlation,
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission-a").expect("id valid"),
                domain::TaskId::new("task-a").expect("id valid"),
            ),
            reason: "checkpoint".to_string(),
        },
    );
    log.save_checkpoint("checkpoint/v1", r#"{"state":"ready"}"#)
        .expect("checkpoint saves");
    log.commit_batch().expect("batch commits");
    drop(log);

    let reopened = SqliteEventLog::open(&path).expect("event log reopens");
    let checkpoint = reopened
        .load_checkpoint()
        .expect("checkpoint is readable")
        .expect("checkpoint exists");
    assert_eq!(checkpoint.event_sequence, 1);
    assert_eq!(checkpoint.schema, "checkpoint/v1");
    assert_eq!(checkpoint.checkpoint_json, r#"{"state":"ready"}"#);
    assert_eq!(reopened.latest_sequence().expect("sequence readable"), 1);
}

/// Rolling back a batch also rolls back its checkpoint replacement.
#[test]
fn controller_checkpoint_rolls_back_with_event_batch() {
    let directory = tempdir().expect("temporary directory should exist");
    let path = directory.path().join("checkpoint.sqlite3");
    let log = SqliteEventLog::open(&path).expect("event log opens");
    log.begin_batch().expect("batch begins");
    log.save_checkpoint("checkpoint/v1", "uncommitted")
        .expect("checkpoint saves in transaction");
    log.rollback_batch().expect("batch rolls back");
    assert!(
        log.load_checkpoint()
            .expect("checkpoint query succeeds")
            .is_none()
    );
}
