//! Durable execution journal tests.

use super::*;
use domain::{
    ContentDigest, ExecutionGroupId, LocalizationFrames, LocalizationVerificationEvidence,
    MapArtifactRef, MapId, MapRevisionId, MapRevisionSelector, MissionId, NodeId,
    PoseQualityComparison, PoseQualityEvidence, RoleId, SpatialAnchorId, TaskId, TaskRef,
    TimestampMs,
};
use tempfile::TempDir;

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
