//! Durable execution journal tests.

use super::*;

#[path = "memory_use_journal_tests.rs"]
mod memory_use_cases;
use domain::{
    ContentDigest, ExecutionGroupId, LocalizationFrames, LocalizationVerificationEvidence,
    MapArtifactRef, MapId, MapRevisionId, MapRevisionSelector, MissionId, NodeId,
    PoseQualityComparison, PoseQualityEvidence, RoleId, SpatialAnchorId, TaskId, TaskRef,
    TimestampMs,
};
use tempfile::TempDir;

/// Supplies immutable metadata for deterministic journal tests, never registered device evidence.
fn preparation() -> domain::MemoryPreparation {
    let original = continuity("prepared-import");
    let manifest: domain::MemoryArtifactManifest = serde_json::from_str(
        original.original_invocation["manifest_json"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    let requirement = domain::MemoryRequirement {
        selector: manifest.selector().clone(),
        kind: manifest.kind(),
        payload_schema: manifest.payload_schema().into(),
        media_type: manifest.media_type().into(),
    };
    domain::MemoryPreparation {
        schema: "roboguide.memory-preparation/v0.1".into(),
        preparation_id: "prepared-context".into(),
        tenant_id: "journal-test".into(),
        group_id: ExecutionGroupId::new("group").unwrap(),
        task_ref: TaskRef::new(
            MissionId::new("mission").unwrap(),
            TaskId::new("task").unwrap(),
        ),
        role_id: RoleId::new("role").unwrap(),
        execution_id: "attempt".into(),
        node_id: NodeId::new("journal-node").unwrap(),
        source_session_id: "original-session".into(),
        membership: domain::GroupMembershipVersion {
            generation: 1,
            bindings_digest: format!("sha256:{}", "b".repeat(64)),
        },
        registry_id: "journal-registry".into(),
        registry_revision: 1,
        invocation: serde_json::json!({"mission_id":"mission","group_id":"group","task_id":"task","role_id":"role"}),
        resource_ids: vec![],
        valid_for_ms: 1000,
        inputs: vec![domain::PreparedMemoryInput {
            requirement,
            manifest,
            typed_map: None,
            consumer: domain::MemoryProviderDescriptor::new(
                "filesystem",
                domain::LocalSystemId::new("files").unwrap(),
                domain::MemoryKind::Artifact,
                domain::MemoryScopeLimit::Global,
                domain::MemoryVisibility::Exchangeable,
                "example.bytes/v1",
                "application/octet-stream",
            )
            .unwrap()
            .with_preparation_support(),
            import_id: original.import_id,
        }],
    }
}

/// Creates an exact once-only operation over the test context without a transport side effect.
fn preparation_command(
    context: &domain::MemoryPreparation,
    operation: domain::MemoryControlKind,
    generation: u64,
) -> domain::MemoryControlCommand {
    domain::MemoryControlCommand {
        schema: "roboguide.memory-control/v0.1".into(),
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        operation,
        generation,
        preparation: (operation == domain::MemoryControlKind::Prepare).then(|| context.clone()),
    }
}

/// Receiver budgets reject reboot/rollback/quantization uncertainty and never compare source clocks.
#[test]
fn memory_receiver_budget_preserves_original_deadline_and_boot_identity() {
    let actual = ReceiverClockObservation::read().unwrap();
    assert!(!actual.boot_id.is_empty());
    assert!(!actual.time_namespace.is_empty());
    let initial = ReceiverClockObservation {
        time_namespace: "unit-time-namespace".into(),
        boot_id: "boot-a".into(),
        lower_ms: 1000,
        uncertainty_ms: 10,
    };
    let budget = MemoryReceiverBudget::admit(&initial, 1000).unwrap();
    assert_eq!(budget.remaining_ms(&initial), 990);
    assert_eq!(
        budget.remaining_ms(&ReceiverClockObservation {
            lower_ms: 1990,
            ..initial.clone()
        }),
        0
    );
    assert_eq!(
        budget.remaining_ms(&ReceiverClockObservation {
            lower_ms: 999,
            ..initial.clone()
        }),
        0
    );
    assert_eq!(
        budget.remaining_ms(&ReceiverClockObservation {
            boot_id: "boot-b".into(),
            ..initial.clone()
        }),
        0
    );
    assert!(MemoryReceiverBudget::admit(&initial, 10).is_err());
    assert_eq!(
        budget.remaining_ms(&ReceiverClockObservation {
            time_namespace: "another-unit-time-namespace".into(),
            ..initial.clone()
        }),
        0
    );
    let mut legacy = serde_json::to_value(&budget).unwrap();
    legacy.as_object_mut().unwrap().remove("time_namespace");
    let legacy: MemoryReceiverBudget = serde_json::from_value(legacy).unwrap();
    assert_eq!(legacy.remaining_ms(&initial), 0);
}

/// Real SQLite reopen/replay preserves the first budget/configuration and irreversible revoke fence.
#[test]
fn memory_preparation_restart_cannot_renew_or_replace_its_original_context() {
    let (_directory, path, journal) = journal();
    let context = preparation();
    let config = format!("sha256:{}", "c".repeat(64));
    let clock = ReceiverClockObservation::read().unwrap();
    let first = journal
        .prepare_memory_context(&context, &clock, 900, &config)
        .unwrap();
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    let later = ReceiverClockObservation {
        lower_ms: clock.lower_ms + 500,
        ..clock.clone()
    };
    let replay = journal
        .prepare_memory_context(&context, &later, 1000, &config)
        .unwrap();
    assert_eq!(replay, first);
    assert!(
        journal
            .prepare_memory_context(
                &context,
                &later,
                1000,
                &format!("sha256:{}", "d".repeat(64))
            )
            .is_err()
    );
    let mut changed = context.clone();
    changed.invocation["objective"] = serde_json::json!("changed original intent");
    assert!(
        journal
            .prepare_memory_context(&changed, &later, 1000, &config)
            .is_err()
    );
    journal
        .revoke_memory_context(&context.preparation_id, &context.digest().unwrap())
        .unwrap();
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    assert!(
        journal
            .memory_preparation(&context.preparation_id)
            .unwrap()
            .unwrap()
            .revoked
    );
    assert!(
        journal
            .prepare_memory_context(&context, &later, 1000, &config)
            .is_err()
    );
    let mut delayed = context.clone();
    delayed.preparation_id = "delayed-original".into();
    delayed.execution_id = "another-attempt".into();
    journal
        .revoke_memory_context(&delayed.preparation_id, &delayed.digest().unwrap())
        .unwrap();
    assert!(
        journal
            .prepare_memory_context(&delayed, &later, 1000, &config)
            .is_err()
    );
}

/// Interrupted IDs cannot re-read, skipped generations cannot arrive late, and receipts are immutable.
#[test]
fn prepared_memory_observations_are_once_only_across_sql_restart_and_reordering() {
    let (_directory, path, journal) = journal();
    let context = preparation();
    journal
        .prepare_memory_context(
            &context,
            &ReceiverClockObservation::read().unwrap(),
            1000,
            &format!("sha256:{}", "c".repeat(64)),
        )
        .unwrap();
    let second = preparation_command(&context, domain::MemoryControlKind::Probe, 2);
    assert_eq!(
        journal.begin_memory_observation(&second).unwrap(),
        BeginMemoryObservation::Start
    );
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    assert_eq!(
        journal.begin_memory_observation(&second).unwrap(),
        BeginMemoryObservation::Interrupted
    );
    let first = preparation_command(&context, domain::MemoryControlKind::Probe, 1);
    assert!(journal.begin_memory_observation(&first).is_err());
    let receipt = domain::MemoryControlReceipt {
        configuration_digest: None,
        command_id: second.command_id(),
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        status: domain::MemoryControlStatus::Unavailable,
        remaining_budget_ms: 0,
        observations: vec![],
        reason: "original read interrupted".into(),
    };
    journal
        .record_memory_control_receipt(&second, &receipt)
        .unwrap();
    assert_eq!(
        journal.begin_memory_observation(&second).unwrap(),
        BeginMemoryObservation::Existing(receipt.clone())
    );
    let mut changed = receipt.clone();
    changed.reason = "new observation under an old ID".into();
    assert!(
        journal
            .record_memory_control_receipt(&second, &changed)
            .is_err()
    );
    assert!(
        journal
            .begin_memory_observation(&preparation_command(
                &context,
                domain::MemoryControlKind::Probe,
                17
            ))
            .is_err()
    );
}

/// Creates original-provider metadata for crash/identity conformance, without a physical device.
fn continuity(import: &str) -> ImportContinuity {
    let manifest = domain::MemoryArtifactManifest::new(
        domain::MemorySelector::new(
            domain::MemoryId::new("fixture").unwrap(),
            domain::MemoryRevisionId::new("r1").unwrap(),
        ),
        domain::MemoryKind::Artifact,
        "producer",
        domain::MemoryOwner::RoboGuide {
            component: "journal-test".into(),
        },
        domain::MemoryScope::Global,
        domain::MemoryVisibility::Exchangeable,
        "example.bytes/v1",
        "application/octet-stream",
        Some(domain::MemoryArtifactRef::new(
            domain::ContentDigest::new(format!("sha256:{}", "d".repeat(64))).unwrap(),
            2,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .unwrap();
    let command = integration::grpc::v0_4::MemoryImport {
        import_id: import.into(),
        command_id: format!("memory-import-{import}"),
        consumer_provider_id: "filesystem".into(),
        manifest_json: serde_json::to_string(&manifest).unwrap(),
        mission_id: "mission".into(),
        group_id: "group".into(),
        task_id: "task".into(),
        role_id: "role".into(),
        execution_id: "attempt".into(),
        ..Default::default()
    };
    ImportContinuity {
        import_id: import.into(),
        command_id: command.command_id.clone(),
        provider_id: "filesystem".into(),
        identity_digest: "a".repeat(64),
        original_content_digest: integration::memory_import_content_digest(&command),
        provider_instance_id: "original-persistent-backend".into(),
        provider_contract_digest: format!("sha256:{}", "c".repeat(64)),
        original_invocation: serde_json::to_value(command).unwrap(),
        admitted_at_ms: 100,
    }
}

/// Creates the exact positive original-provider result, independent of Task satisfaction.
fn continuity_result(original: &ImportContinuity) -> ImportQueryResult {
    ImportQueryResult {
        schema: "roboguide.memory-import-query-result/v0.1".into(),
        import_id: original.import_id.clone(),
        command_id: original.command_id.clone(),
        provider_id: original.provider_id.clone(),
        identity_digest: original.identity_digest.clone(),
        original_content_digest: original.original_content_digest.clone(),
        provider_instance_id: original.provider_instance_id.clone(),
        provider_contract_digest: original.provider_contract_digest.clone(),
        outcome: "completed".into(),
        durable: true,
        reason: String::new(),
    }
}

/// A provider side effect interrupted before its receipt never reruns import or the same query.
#[test]
fn original_memory_query_survives_crash_without_renewing_identity_or_budget() {
    let (_directory, path, journal) = journal();
    let original = continuity("import-crash");
    journal
        .prepare_memory_import_continuity(
            &original.import_id,
            &original.command_id,
            &original.identity_digest,
            Some(&original),
        )
        .unwrap();
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    assert_eq!(
        journal
            .memory_import(&original.import_id)
            .unwrap()
            .unwrap()
            .status(),
        MemoryImportStatus::Unknown
    );
    let mut replacement = continuity("replacement");
    replacement.provider_id = "another-provider".into();
    replacement.original_invocation["consumer_provider_id"] = "another-provider".into();
    let v = &replacement.original_invocation;
    replacement.original_content_digest = integration::memory_import_authorization_digest(
        v["import_id"].as_str().unwrap(),
        v["command_id"].as_str().unwrap(),
        v["consumer_provider_id"].as_str().unwrap(),
        v["manifest_json"].as_str().unwrap(),
        v["mission_id"].as_str().unwrap(),
        v["group_id"].as_str().unwrap(),
        v["task_id"].as_str().unwrap(),
        v["role_id"].as_str().unwrap(),
        v["execution_id"].as_str().unwrap(),
        "",
    );
    assert!(
        journal
            .prepare_memory_import_continuity(
                &replacement.import_id,
                &replacement.command_id,
                &replacement.identity_digest,
                Some(&replacement)
            )
            .unwrap_err()
            .to_string()
            .contains("unresolved original")
    );
    let query = ImportQuerySpec {
        query_id: format!("memory-query-{}-1", original.import_id),
        import_id: original.import_id.clone(),
        command_id: original.command_id.clone(),
        provider_id: original.provider_id.clone(),
        original_content_digest: original.original_content_digest.clone(),
        generation: 1,
        valid_for_ms: 50,
    };
    assert!(journal.prepare_import_query(&query, 200).unwrap().1);
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    let (replay, start) = journal.prepare_import_query(&query, 999_000).unwrap();
    assert!(!start);
    assert_eq!(replay.received_at_ms, 200);
    assert!(replay.result.is_none());
    assert!(
        journal
            .record_import_query(&query.query_id, &continuity_result(&original), 250)
            .is_err()
    );
    let mut next = query.clone();
    next.generation = 2;
    next.query_id = format!("memory-query-{}-2", original.import_id);
    assert!(journal.prepare_import_query(&next, 300).unwrap().1);
    let mut changed = continuity_result(&original);
    changed.provider_instance_id = "replacement-backend".into();
    assert!(
        journal
            .record_import_query(&next.query_id, &changed, 310)
            .is_err()
    );
    changed = continuity_result(&original);
    changed.outcome = "not_found".into();
    assert!(
        journal
            .record_import_query(&next.query_id, &changed, 310)
            .is_err()
    );
    let result = continuity_result(&original);
    let committed = journal
        .record_import_query(&next.query_id, &result, 310)
        .unwrap();
    assert_eq!(committed.result, Some(result.clone()));
    assert_eq!(
        journal.prepare_import_query(&next, 500).unwrap().0,
        committed
    );
    assert_eq!(
        journal
            .memory_import(&original.import_id)
            .unwrap()
            .unwrap()
            .status(),
        MemoryImportStatus::Completed
    );
    let mut contradicted = result;
    contradicted.outcome = "rejected_before_effect".into();
    assert!(
        journal
            .record_import_query(&next.query_id, &contradicted, 320)
            .is_err()
    );
    let mut renewed = next.clone();
    renewed.valid_for_ms = 100;
    assert!(journal.prepare_import_query(&renewed, 500).is_err());
    assert_eq!(
        journal.import_continuity(&original.import_id).unwrap(),
        Some(original)
    );
}

/// Failure between original identity and continuity inserts rolls both back before provider effects.
#[test]
fn memory_original_provider_admission_is_one_sql_transaction() {
    let (_directory, path, journal) = journal();
    let connection = Connection::open(path).unwrap();
    connection.execute_batch("CREATE TRIGGER fail_continuity BEFORE INSERT ON memory_import_continuity_v7 BEGIN SELECT RAISE(ABORT, 'injected continuity write failure'); END;").unwrap();
    let original = continuity("atomic-import");
    assert!(
        journal
            .prepare_memory_import_continuity(
                &original.import_id,
                &original.command_id,
                &original.identity_digest,
                Some(&original)
            )
            .is_err()
    );
    assert!(
        journal
            .memory_import(&original.import_id)
            .unwrap()
            .is_none()
    );
    assert!(
        journal
            .import_continuity(&original.import_id)
            .unwrap()
            .is_none()
    );
    connection
        .execute_batch("DROP TRIGGER fail_continuity;")
        .unwrap();
    assert!(matches!(
        journal
            .prepare_memory_import_continuity(
                &original.import_id,
                &original.command_id,
                &original.identity_digest,
                Some(&original)
            )
            .unwrap(),
        PrepareMemoryImport::Start
    ));
}

/// Restore/query cannot use a changed original invocation with a retained old content digest.
#[test]
fn changed_original_memory_tuple_is_rejected_before_readonly_query_admission() {
    let (_directory, path, journal) = journal();
    let original = continuity("original-tuple");
    journal
        .prepare_memory_import_continuity(
            &original.import_id,
            &original.command_id,
            &original.identity_digest,
            Some(&original),
        )
        .unwrap();
    let mut changed = serde_json::to_value(&original).unwrap();
    changed["original_invocation"]["execution_id"] = "another-attempt".into();
    let db = Connection::open(path).unwrap();
    db.execute(
        "UPDATE memory_import_continuity_v7 SET evidence_json=?1",
        [changed.to_string()],
    )
    .unwrap();
    assert!(journal.import_continuity(&original.import_id).is_err());
    let query = ImportQuerySpec {
        query_id: format!("memory-query-{}-1", original.import_id),
        import_id: original.import_id.clone(),
        command_id: original.command_id.clone(),
        provider_id: original.provider_id.clone(),
        original_content_digest: original.original_content_digest.clone(),
        generation: 1,
        valid_for_ms: 100,
    };
    assert!(journal.prepare_import_query(&query, 200).is_err());
    assert_eq!(
        db.query_row("SELECT COUNT(*) FROM memory_import_queries_v7", [], |r| r
            .get::<_, u64>(
            0
        ))
        .unwrap(),
        0
    );
}

/// Creates a deterministic canonical identity fixture.
fn spec(invocation: &str, workflow: &str, resources: &[&str]) -> ExecutionSpec {
    ExecutionSpec::new(
        invocation.as_bytes().to_vec(),
        workflow,
        resources.iter().map(|value| (*value).to_string()),
    )
    .expect("fixture spec is valid")
}

/// Creates a temporary on-disk journal and returns its owning directory.
fn journal() -> (TempDir, std::path::PathBuf, ExecutionJournal) {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("executions.sqlite3");
    let journal = ExecutionJournal::open(&path).expect("journal opens");
    (directory, path, journal)
}

/// Builds one typed prepared manifest with mandatory build execution and Task provenance.
fn prepared_manifest(execution_id: &str) -> MapArtifactManifest {
    let mission = MissionId::new("mission-a").expect("mission id");
    let artifact = MapArtifactRef::new(
        MapRevisionSelector::new(
            MapId::new("lab").expect("map id"),
            MapRevisionId::new("r1").expect("revision id"),
        ),
        ContentDigest::new(format!("sha256:{}", "a".repeat(64))).expect("digest"),
        9,
    );
    MapArtifactManifest::new_with_format(
        artifact,
        "application/octet-stream",
        "grid",
        "v1",
        NodeId::new("dog-a").expect("node id"),
        None,
        mission.clone(),
        Some(execution_id.to_string()),
        Some(TaskRef::new(
            mission,
            TaskId::new("build-map").expect("task id"),
        )),
        "map",
        "enu",
        SpatialAnchorId::new("anchor-lab").expect("anchor id"),
        Some(0.05),
        TimestampMs::new(7),
        None,
    )
    .expect("manifest")
}

/// Builds one complete strong localization evidence fixture for a durable local handle.
fn localization_evidence(active_local_map_id: &str) -> LocalizationVerificationEvidence {
    let mission = MissionId::new("mission-localize").expect("mission id");
    LocalizationVerificationEvidence::new(
        prepared_manifest("build-execution").artifact().clone(),
        mission.clone(),
        TaskRef::new(
            mission,
            TaskId::new("verify-map").expect("task id is valid"),
        ),
        ExecutionGroupId::new("group-localize").expect("group id is valid"),
        RoleId::new("localizer").expect("role id is valid"),
        NodeId::new("dog-b").expect("node id is valid"),
        "verify-execution",
        "local-verify",
        active_local_map_id,
        "localization",
        PoseQualityEvidence::new(
            "translation_stddev",
            "0.08",
            "0.10",
            "m",
            PoseQualityComparison::AtMost,
        )
        .expect("quality is valid"),
        LocalizationFrames::new("map", "odom", "base_link").expect("frames are valid"),
        SpatialAnchorId::new("anchor-lab").expect("anchor is valid"),
        TimestampMs::new(50),
    )
    .expect("localization evidence is valid")
}

/// Returns the complete canonical Verify invocation bound by strong evidence tests.
fn localization_invocation() -> &'static str {
    r#"{
            "mission_id":"mission-localize",
            "task_id":"verify-map",
            "group_id":"group-localize",
            "role_id":"localizer",
            "capability_contract":"spatial.localization.verify@v0",
            "parameters":{
                "artifact_operation":"verify",
                "map_id":"lab",
                "revision_id":"r1",
                "spatial_anchor_id":"anchor-lab"
            },
            "resource_ids":[]
        }"#
}

/// Prepares one nonterminal Verify execution with a durable local attempt and finalization fence.
fn prepare_localization_execution(journal: &ExecutionJournal, execution_id: &str) {
    let identity = spec(localization_invocation(), "workflow-verify", &[]);
    journal
        .prepare_dispatch(execution_id, &identity)
        .expect("verify execution prepares");
    journal
        .record_local_handle(execution_id, "local-verify")
        .expect("local handle persists");
    journal
        .record_status(
            execution_id,
            1,
            JournalStatus::Running,
            "local verification completed",
        )
        .expect("verify execution is active");
    journal
        .prepare_artifact_finalization(execution_id, ArtifactFinalizationKind::Verify)
        .expect("verify finalization is fenced");
}

/// New identities are durably prepared before a caller may dispatch locally.
#[test]
fn prepare_dispatch_uses_wal_and_persists_dispatching() {
    let (_directory, _path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
    let PrepareDispatch::Start(record) = journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares")
    else {
        panic!("new execution must receive dispatch permission");
    };
    assert_eq!(record.status(), JournalStatus::Dispatching);
    assert_eq!(record.sequence(), 0);
    assert_eq!(
        record.spec().invocation_digest(),
        identity.invocation_digest()
    );
    let connection = journal.lock_connection().expect("connection locks");
    let mode = connection
        .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
        .expect("journal mode reads");
    assert_eq!(mode.to_ascii_lowercase(), "wal");
}

/// One execution ID is idempotent only for the complete immutable identity tuple.
#[test]
fn identity_tuple_is_idempotent_and_conflict_checked() {
    let (_directory, _path, journal) = journal();
    let original = spec(
        "{\"capability\":\"reach\"}",
        "workflow-a",
        &["camera", "motor"],
    );
    assert!(matches!(
        journal.prepare_dispatch("execution-a", &original),
        Ok(PrepareDispatch::Start(_))
    ));
    let reordered = spec(
        "{\"capability\":\"reach\"}",
        "workflow-a",
        &["motor", "camera", "motor"],
    );
    assert!(matches!(
        journal.prepare_dispatch("execution-a", &reordered),
        Ok(PrepareDispatch::Existing(_))
    ));
    for conflict in [
        spec(
            "{\"capability\":\"dock\"}",
            "workflow-a",
            &["camera", "motor"],
        ),
        spec(
            "{\"capability\":\"reach\"}",
            "workflow-b",
            &["camera", "motor"],
        ),
        spec("{\"capability\":\"reach\"}", "workflow-a", &["camera"]),
    ] {
        assert!(matches!(
            journal.prepare_dispatch("execution-a", &conflict),
            Ok(PrepareDispatch::Conflict(_))
        ));
    }
}

/// Selective Memory import admission is durable, exact-identity fenced, and replayable.
#[test]
fn memory_import_identity_is_durable_and_conflict_checked() {
    let (directory, path, journal) = journal();
    assert!(matches!(
        journal.prepare_memory_import("import-a", "memory-import-import-a", "sha256:a"),
        Ok(PrepareMemoryImport::Start)
    ));
    let replay = journal
        .prepare_memory_import("import-a", "memory-import-import-a", "sha256:a")
        .expect("same import identity replays");
    assert!(
        matches!(replay, PrepareMemoryImport::Existing(record) if record.status() == MemoryImportStatus::Pending)
    );
    assert!(matches!(
        journal.prepare_memory_import("import-a", "memory-import-import-a", "sha256:b"),
        Ok(PrepareMemoryImport::Conflict(_))
    ));
    journal
        .record_memory_import(
            "import-a",
            "memory-import-import-a",
            "sha256:a",
            MemoryImportStatus::Completed,
            "",
        )
        .expect("completion persists");
    drop(journal);
    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let record = reopened
        .memory_import("import-a")
        .expect("import lookup succeeds")
        .expect("import survives restart");
    assert_eq!(record.status(), MemoryImportStatus::Completed);
    drop(reopened);
    drop(directory);
}

/// A crash during provider side effects requires reconciliation rather than automatic re-execution.
#[test]
fn ambiguous_memory_import_never_restarts_provider_side_effects() {
    let (_directory, path, journal) = journal();
    journal
        .prepare_memory_import("ambiguous", "memory-import-ambiguous", "sha256:original")
        .expect("intent persists before provider call");
    drop(journal);
    let reopened = ExecutionJournal::open(&path).expect("restart recovers pending imports");
    for _ in 0..2 {
        assert!(matches!(
            reopened.prepare_memory_import("ambiguous", "memory-import-ambiguous", "sha256:original"),
            Ok(PrepareMemoryImport::Existing(record)) if record.status() == MemoryImportStatus::Unknown
        ));
    }
    assert!(matches!(
        reopened.prepare_memory_import("ambiguous", "memory-import-ambiguous", "sha256:changed"),
        Ok(PrepareMemoryImport::Conflict(_))
    ));
}

/// Terminal status, sequence, handle, and identity survive restart for replay.
#[test]
fn terminal_execution_replays_after_restart() {
    let (directory, path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
    journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares");
    journal
        .record_local_handle("execution-a", "local-run-7")
        .expect("handle persists");
    journal
        .record_status("execution-a", 1, JournalStatus::Accepted, "")
        .expect("acceptance persists");
    journal
        .record_status("execution-a", 2, JournalStatus::Running, "moving")
        .expect("running persists");
    journal
        .record_status("execution-a", 3, JournalStatus::Completed, "arrived")
        .expect("completion persists");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let records = reopened.terminal_records().expect("terminal facts replay");
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].execution_id(), "execution-a");
    assert_eq!(records[0].local_handle(), Some("local-run-7"));
    assert!(!records[0].cancellation_requested());
    assert_eq!(records[0].sequence(), 3);
    assert_eq!(records[0].status(), JournalStatus::Completed);
    assert_eq!(records[0].reason(), "arrived");
    assert_eq!(records[0].spec(), &identity);
    drop(reopened);
    drop(directory);
}

/// A durable cancel request remains distinct from a later terminal Cancelled fact.
#[test]
fn cancellation_request_persists_without_synthesizing_cancelled() {
    let (_directory, path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
    journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares");
    journal
        .record_local_handle("execution-a", "local-run-7")
        .expect("handle persists");
    journal
        .record_status("execution-a", 1, JournalStatus::Running, "moving")
        .expect("running persists");
    let requested = journal
        .record_cancellation_requested("execution-a")
        .expect("cancel request persists");
    assert!(requested.cancellation_requested());
    assert_eq!(requested.status(), JournalStatus::Running);
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let replay = reopened
        .get("execution-a")
        .expect("record reads")
        .expect("record exists");
    assert!(replay.cancellation_requested());
    assert_eq!(replay.status(), JournalStatus::Running);
    let cancelled = reopened
        .record_status(
            "execution-a",
            2,
            JournalStatus::Cancelled,
            "local terminal fact",
        )
        .expect("terminal cancellation persists");
    assert!(cancelled.cancellation_requested());
    assert_eq!(cancelled.status(), JournalStatus::Cancelled);
}

/// A Cancel that arrives first creates a tombstone and prevents later local dispatch.
#[test]
fn cancel_before_execute_is_durable_and_suppresses_dispatch() {
    let (_directory, path, journal) = journal();
    journal
        .request_cancellation("execution-a")
        .expect("early cancellation persists");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    assert!(
        reopened
            .cancellation_pending("execution-a")
            .expect("tombstone reads")
    );
    let prepared = reopened
        .prepare_dispatch(
            "execution-a",
            &spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]),
        )
        .expect("late Execute is reduced against tombstone");
    let PrepareDispatch::Existing(record) = prepared else {
        panic!("cancel tombstone must prevent local dispatch authorization");
    };
    assert_eq!(record.status(), JournalStatus::Cancelled);
    assert!(reopened.authorize_local_dispatch("execution-a").is_err());
}

/// A crash before handle persistence becomes unknown and never grants redispatch.
#[test]
fn ambiguous_dispatch_requires_reconciliation_and_never_restarts() {
    let (_directory, path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
    assert!(matches!(
        journal.prepare_dispatch("execution-a", &identity),
        Ok(PrepareDispatch::Start(_))
    ));
    journal
        .authorize_local_dispatch("execution-a")
        .expect("local dispatch is durably authorized");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let record = reopened
        .get("execution-a")
        .expect("record reads")
        .expect("record exists");
    assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
    assert_eq!(record.reason(), AMBIGUOUS_DISPATCH_REASON);
    assert!(matches!(
        reopened.prepare_dispatch("execution-a", &identity),
        Ok(PrepareDispatch::Existing(JournalExecution {
            status: JournalStatus::ReconciliationRequired,
            ..
        }))
    ));
    assert!(matches!(
        reopened.record_local_handle("execution-a", "late-handle"),
        Err(JournalError::AmbiguousDispatch(_))
    ));
}

/// A crash after local handle persistence fences the handle for status-only reconciliation.
#[test]
fn handle_bearing_dispatch_requires_status_reconciliation_after_restart() {
    let (_directory, path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
    journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares");
    journal
        .authorize_local_dispatch("execution-a")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("execution-a", "local-run-7")
        .expect("local handle persists");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let record = reopened
        .get("execution-a")
        .expect("record reads")
        .expect("record exists");
    assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
    assert_eq!(record.reason(), HANDLE_BEARING_DISPATCH_REASON);
    assert_eq!(record.local_handle(), Some("local-run-7"));
}

/// A restart before local dispatch authorization is a conclusive failed preparation.
#[test]
fn interrupted_pre_dispatch_is_failed_without_claiming_physical_ambiguity() {
    let (_directory, path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
    journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    let record = reopened
        .get("execution-a")
        .expect("record reads")
        .expect("record exists");
    assert_eq!(record.status(), JournalStatus::Failed);
    assert_eq!(record.reason(), INTERRUPTED_PRE_DISPATCH_REASON);
}

/// Prepared bytes and pending remote finalization survive a complete process restart.
#[test]
fn prepared_artifact_and_finalization_are_durable_and_conflict_checked() {
    let (_directory, path, journal) = journal();
    let build_spec = spec("{\"task\":\"build\"}", "workflow-a", &[]);
    let publish_spec = spec("{\"task\":\"publish\"}", "workflow-b", &[]);
    journal
        .prepare_dispatch("build-execution", &build_spec)
        .expect("build execution prepares");
    journal
        .prepare_dispatch("publish-execution", &publish_spec)
        .expect("publish execution prepares");
    journal
        .record_local_handle("publish-execution", "local-publish")
        .expect("publish handle persists");
    journal
        .record_status(
            "publish-execution",
            1,
            JournalStatus::Running,
            "local publication workflow completed",
        )
        .expect("publish execution is active");
    let prepared = PreparedArtifactRecord::new(
        "lab-r1-output",
        "build-execution",
        "/var/lib/roboguide/prepared/aa/content",
        prepared_manifest("build-execution"),
    )
    .expect("prepared record validates");
    assert!(matches!(
        journal.record_prepared_artifact(&prepared),
        Err(JournalError::ArtifactPreparationConflict(_))
    ));
    assert_eq!(
        journal
            .prepare_artifact_freeze("build-execution", "lab-r1-output")
            .expect("one mutable-source read is granted"),
        PrepareArtifactFreeze::Start
    );
    assert_eq!(
        journal
            .record_prepared_artifact(&prepared)
            .expect("prepared artifact persists"),
        prepared
    );
    assert_eq!(
        journal
            .artifact_preparation("build-execution")
            .expect("preparation fence reads"),
        None,
        "prepared record and fence removal commit atomically"
    );
    journal
        .record_prepared_artifact(&prepared)
        .expect("exact prepared artifact is idempotent");
    journal
        .prepare_artifact_finalization("publish-execution", ArtifactFinalizationKind::Publish)
        .expect("finalization marker persists");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    assert_eq!(
        reopened
            .prepared_artifact("lab-r1-output")
            .expect("prepared artifact reads"),
        Some(prepared.clone())
    );
    assert_eq!(
        reopened
            .artifact_finalization("publish-execution")
            .expect("finalization reads"),
        Some(ArtifactFinalizationKind::Publish)
    );
    let conflicting = PreparedArtifactRecord::new(
        "lab-r1-output",
        "build-execution",
        "/different/path",
        prepared_manifest("build-execution"),
    )
    .expect("conflict fixture validates");
    assert!(matches!(
        reopened.record_prepared_artifact(&conflicting),
        Err(JournalError::PreparedArtifactConflict(_))
    ));
    assert!(matches!(
        reopened
            .prepare_artifact_finalization("publish-execution", ArtifactFinalizationKind::Verify),
        Err(JournalError::ArtifactFinalizationConflict(_))
    ));
}

/// Strong evidence is immutable, attempt-bound, and durable before remote finalization.
#[test]
fn localization_evidence_is_durable_and_conflict_checked() {
    let (_directory, path, journal) = journal();
    prepare_localization_execution(&journal, "verify-execution");
    let node_id = NodeId::new("dog-b").expect("node id is valid");
    let manifest = prepared_manifest("build-execution");
    let evidence = localization_evidence("lab-local");
    journal
        .prepare_localization_evidence("verify-execution", &node_id, &manifest, &evidence)
        .expect("evidence persists before delivery");
    journal
        .prepare_localization_evidence("verify-execution", &node_id, &manifest, &evidence)
        .expect("exact evidence is idempotent");
    drop(journal);

    let reopened = ExecutionJournal::open(path).expect("journal reopens");
    assert_eq!(
        reopened
            .localization_evidence("verify-execution")
            .expect("evidence reads"),
        Some(evidence)
    );
    assert!(matches!(
        reopened.prepare_localization_evidence(
            "verify-execution",
            &node_id,
            &manifest,
            &localization_evidence("different-local-map"),
        ),
        Err(JournalError::LocalizationEvidenceConflict(_))
    ));
}

/// Strong evidence rejects every authority-bearing identity that differs from its invocation.
#[test]
fn localization_evidence_rejects_mismatched_execution_context() {
    let (_directory, _path, journal) = journal();
    prepare_localization_execution(&journal, "verify-execution");
    let node_id = NodeId::new("dog-b").expect("node id is valid");
    let manifest = prepared_manifest("build-execution");
    let evidence = localization_evidence("lab-local");
    let mutations = [
        ("mission_id", "mission-other".to_string()),
        ("task_id", "verify-other".to_string()),
        ("group_id", "group-other".to_string()),
        ("role_id", "observer".to_string()),
        ("node_id", "dog-a".to_string()),
        ("execution_id", "execution-other".to_string()),
        ("local_attempt_id", "attempt-other".to_string()),
        ("map_id", "other-map".to_string()),
        ("revision_id", "r2".to_string()),
        ("anchor_id", "anchor-other".to_string()),
        ("content_digest", format!("sha256:{}", "b".repeat(64))),
    ];
    for (field, value) in mutations {
        let mut encoded = serde_json::to_value(&evidence).expect("evidence serializes");
        encoded[field] = serde_json::Value::String(value);
        let mismatched: LocalizationVerificationEvidence =
            serde_json::from_value(encoded).expect("mismatch remains structurally valid");
        assert!(matches!(
            journal.prepare_localization_evidence(
                "verify-execution",
                &node_id,
                &manifest,
                &mismatched,
            ),
            Err(JournalError::LocalizationEvidenceConflict(_))
        ));
    }
    assert_eq!(
        journal
            .localization_evidence("verify-execution")
            .expect("evidence lookup succeeds"),
        None,
        "a rejected mismatch must not become durable"
    );
}

/// A crash after freezing bytes never grants the same execution another mutable-source read.
#[test]
fn interrupted_artifact_freeze_is_durably_fenced_across_restart() {
    let (directory, path, journal) = journal();
    let identity = spec("{\"task\":\"build\"}", "workflow-a", &[]);
    journal
        .prepare_dispatch("build-execution", &identity)
        .expect("build execution prepares");
    journal
        .authorize_local_dispatch("build-execution")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("build-execution", "local-build")
        .expect("local handle persists");
    journal
        .record_status(
            "build-execution",
            1,
            JournalStatus::Running,
            "local map builder completed",
        )
        .expect("running state persists");
    assert_eq!(
        journal
            .prepare_artifact_freeze("build-execution", "lab-r1-output")
            .expect("first source read is durably granted"),
        PrepareArtifactFreeze::Start
    );
    let frozen = directory.path().join("prepared-snapshot");
    std::fs::write(&frozen, b"first-map").expect("simulated frozen snapshot writes");
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens after crash");
    let execution = reopened
        .get("build-execution")
        .expect("execution reads")
        .expect("execution exists");
    assert_eq!(execution.status(), JournalStatus::ReconciliationRequired);
    assert_eq!(execution.reason(), INTERRUPTED_ARTIFACT_PREPARATION_REASON);
    assert_eq!(
        reopened
            .artifact_preparation("build-execution")
            .expect("preparation marker reads"),
        Some("lab-r1-output".to_string())
    );
    assert_eq!(
        reopened
            .prepare_artifact_freeze("build-execution", "lab-r1-output")
            .expect("exact retry checks prior grant"),
        PrepareArtifactFreeze::Pending,
        "an exact retry must not authorize rereading a potentially changed source"
    );
    assert_eq!(
        std::fs::read(frozen).expect("first immutable snapshot remains available"),
        b"first-map"
    );
}

/// Migrating a v1 dispatch remains conservative because its call boundary was not recorded.
#[test]
fn v1_dispatch_migration_preserves_possible_local_side_effect() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("legacy.sqlite3");
    let connection = Connection::open(&path).expect("legacy database opens");
    connection
        .execute_batch(
            "CREATE TABLE executions (
                    execution_id TEXT PRIMARY KEY NOT NULL,
                    invocation_content BLOB NOT NULL,
                    invocation_digest TEXT NOT NULL,
                    workflow_digest TEXT NOT NULL,
                    resource_ids TEXT NOT NULL,
                    local_handle TEXT,
                    cancellation_requested INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL
                 );
                 PRAGMA user_version = 1;",
        )
        .expect("legacy schema creates");
    let invocation = b"{\"task\":\"legacy\"}";
    connection
        .execute(
            "INSERT INTO executions VALUES (?1, ?2, ?3, ?4, ?5, NULL, 0, 0, 'dispatching', '')",
            params![
                "legacy-execution",
                invocation.as_slice(),
                digest_bytes(invocation),
                "legacy-workflow",
                "[]"
            ],
        )
        .expect("legacy dispatch inserts");
    drop(connection);

    let migrated = ExecutionJournal::open(&path).expect("legacy journal migrates");
    let record = migrated
        .get("legacy-execution")
        .expect("record reads")
        .expect("record exists");
    assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
    assert_eq!(record.reason(), AMBIGUOUS_DISPATCH_REASON);
}

/// Sequence and terminal guards preserve a single ordered fact history.
#[test]
fn status_updates_reject_stale_and_post_terminal_facts() {
    let (_directory, _path, journal) = journal();
    let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
    journal
        .prepare_dispatch("execution-a", &identity)
        .expect("dispatch prepares");
    journal
        .record_status("execution-a", 1, JournalStatus::Running, "moving")
        .expect("running persists");
    journal
        .record_status("execution-a", 1, JournalStatus::Running, "moving")
        .expect("exact duplicate is idempotent");
    assert!(matches!(
        journal.record_status("execution-a", 1, JournalStatus::Failed, "late"),
        Err(JournalError::StaleSequence { .. })
    ));
    journal
        .record_status("execution-a", 2, JournalStatus::Completed, "done")
        .expect("completion persists");
    assert!(matches!(
        journal.record_status("execution-a", 3, JournalStatus::Running, "impossible"),
        Err(JournalError::InvalidTransition(_, _))
    ));
}

/// Replicated Controller epochs survive restart and reject commands from an older leader.
#[test]
fn controller_epoch_is_durable_and_monotonic() {
    let (directory, path, journal) = journal();
    let first = r#"{"term":4,"replica_id":2}"#;
    let same = r#"{"term":4,"replica_id":2}"#;
    let older = r#"{"term":3,"replica_id":1}"#;
    let newer = r#"{"term":5,"replica_id":1}"#;

    journal
        .admit_controller_epoch(first)
        .expect("first Controller epoch is accepted");
    journal
        .admit_controller_epoch(same)
        .expect("replaying the same epoch is idempotent");
    assert!(matches!(
        journal.admit_controller_epoch(older),
        Err(JournalError::StaleControllerEpoch { .. })
    ));
    drop(journal);

    let reopened = ExecutionJournal::open(&path).expect("journal reopens");
    assert!(matches!(
        reopened.admit_controller_epoch(older),
        Err(JournalError::StaleControllerEpoch { .. })
    ));
    reopened
        .admit_controller_epoch(newer)
        .expect("newer Controller epoch advances the durable fence");
    drop(reopened);
    drop(directory);
}

/// Readonly compatibility names fence old SQL writers even when no pending execution exists.
#[test]
fn current_journal_keeps_legacy_inspection_but_refuses_legacy_writes() {
    let (_directory, path, journal) = journal();
    journal
        .prepare_memory_import("import", "command", "sha256:original")
        .unwrap();
    let connection = rusqlite::Connection::open(&path).unwrap();
    assert_eq!(
        connection
            .query_row("SELECT COUNT(*) FROM memory_imports", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        1
    );
    for sql in [
        "UPDATE executions SET reason='old-writer' WHERE 1=0",
        "UPDATE memory_imports SET status='unknown' WHERE status='pending'",
        "INSERT INTO memory_imports VALUES ('other','command','digest','pending','')",
    ] {
        assert!(
            connection.execute(sql, []).is_err(),
            "legacy write must be fenced: {sql}"
        );
    }
    assert_eq!(
        journal.memory_import("import").unwrap().unwrap().status(),
        MemoryImportStatus::Pending
    );
    assert_eq!(
        connection
            .query_row(
                "SELECT version FROM node_journal_storage_schema",
                [],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        10
    );
}

/// A future journal or a conflicting shadow authority cannot be silently downgraded at startup.
#[test]
fn future_journal_and_dual_authorities_refuse_without_migration() {
    let (directory, path, journal) = journal();
    drop(journal);
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection.pragma_update(None, "user_version", 11).unwrap();
    drop(connection);
    assert!(ExecutionJournal::open(&path).is_err());
    let connection = rusqlite::Connection::open(&path).unwrap();
    assert_eq!(
        connection
            .pragma_query_value(None, "user_version", |row| row.get::<_, i64>(0))
            .unwrap(),
        11
    );
    connection.pragma_update(None, "user_version", 10).unwrap();
    connection
        .execute_batch("DROP VIEW executions; CREATE TABLE executions (shadow INTEGER)")
        .unwrap();
    drop(connection);
    assert!(ExecutionJournal::open(&path).is_err());
    drop(directory);
}
