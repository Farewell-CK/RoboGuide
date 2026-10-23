//! Actual file/SQL fault windows behind the production HTTP upload workflow.

use super::*;

/// A labelled cache-only contract fixture supplies no actual process, content or quorum authority.
fn recovery_cache_fixture(catalog: &ArtifactCatalog) -> domain::ArtifactUploadRecovery {
    let caller = ArtifactPrincipal::local();
    let original = domain::ArtifactUploadRecord {
        upload_id: "original-upload".into(),
        principal_id: caller.subject,
        tenant_id: caller.tenant_id,
        original_certificate: "loopback-fixture".into(),
        admitted_at_ms: 1,
        deadline_ms: 1000,
        phase: domain::ArtifactUploadPhase::Prepared,
        content_digest: Some(digest_bytes(b"metadata-only-cache-contract")),
        byte_size: Some(28),
        prepared_at_ms: Some(2),
        completed_at_ms: None,
    };
    let replacement = domain::ArtifactUploadRecord {
        upload_id: "replacement".into(),
        phase: domain::ArtifactUploadPhase::Started,
        content_digest: None,
        byte_size: None,
        prepared_at_ms: None,
        completed_at_ms: None,
        ..original.clone()
    };
    let record = domain::ArtifactUploadRecovery {
        schema: "roboguide.artifact-upload-recovery/v0.1".into(),
        command_id: "original-command".into(),
        budget_ms: 1000,
        reader_id: "cache-contract-reader".into(),
        upload: original.clone(),
        replacement_admission: replacement,
        source_owner_id: format!("artifact-store:{}", "a".repeat(64)),
        destination_owner_id: format!("artifact-store:{}", "b".repeat(64)),
        destination_device: 1,
        destination_inode: 2,
        controller_process_json: "{}".into(),
        kernel_budget_json: "{}".into(),
        canonical_grant_json: "{}".into(),
        completion: None,
    };
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(original.upload_id.clone(), original);
    catalog
        .upload_recoveries
        .lock()
        .unwrap()
        .insert(record.command_id.clone(), record.clone());
    record
}

/// Actual HTTP observation survives a write latch without adding SQL or exposing content/mutations.
#[tokio::test]
async fn write_disabled_recovery_observation_preserves_original_cache_and_refuses_other_routes() {
    let directory = tempfile::tempdir().unwrap();
    let store = FileSystemArtifactStore::new(directory.path().join("cas")).unwrap();
    let log = SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .unwrap()
        .with_write_failure_fence("actual-cache-observer")
        .unwrap();
    seed_controller_checkpoint(&log);
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let original = recovery_cache_fixture(&catalog);
    log.disable_writes("explicit software SQL-fault contract", false)
        .unwrap();
    let uploads = test_uploads();
    let path = "/v1/artifact-uploads/original-upload/recoveries/original-command";
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", path, b"", true),
    )
    .await;
    let body: serde_json::Value = serde_json::from_slice(&response_body(&raw, "200 OK")).unwrap();
    assert_eq!(body["record"], serde_json::to_value(&original).unwrap());
    assert_eq!(body["audit_persisted"], false);
    assert_eq!(body["write_status"]["write_enabled"], false);
    assert_eq!(body["status"], "unknown");
    let assessment_path = format!("{path}/assessment");
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", &assessment_path, b"", true),
    )
    .await;
    let assessment: serde_json::Value =
        serde_json::from_slice(&response_body(&raw, "200 OK")).unwrap();
    assert_eq!(
        assessment["schema"],
        "roboguide.artifact-upload-recovery-assessment/v0.1"
    );
    assert_eq!(assessment["replacement_record_evidence"], "unavailable");
    assert!(assessment["replacement_record"].is_null());
    assert_eq!(assessment["repeat_allowed"], false);
    let mut completed = original.replacement_admission.clone();
    completed.phase = domain::ArtifactUploadPhase::Completed;
    completed.content_digest = original.upload.content_digest.clone();
    completed.byte_size = original.upload.byte_size;
    completed.prepared_at_ms = Some(3);
    completed.completed_at_ms = Some(4);
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(completed.upload_id.clone(), completed);
    let mut abandoned = original.upload.clone();
    abandoned.phase = domain::ArtifactUploadPhase::Abandoned;
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(abandoned.upload_id.clone(), abandoned);
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", &assessment_path, b"", true),
    )
    .await;
    let assessment: serde_json::Value =
        serde_json::from_slice(&response_body(&raw, "200 OK")).unwrap();
    assert_eq!(
        assessment["record"],
        serde_json::to_value(&original).unwrap()
    );
    assert_eq!(assessment["status"], "unknown");
    assert_eq!(assessment["source_record"]["phase"], "abandoned");
    assert_eq!(assessment["replacement_record"]["phase"], "completed");
    let replacement = catalog
        .upload_records
        .lock()
        .unwrap()
        .get("replacement")
        .cloned()
        .unwrap();
    let retirement = domain::ArtifactUploadRetirement {
        schema: "roboguide.artifact-upload-retirement/v0.1".into(),
        command_id: "contract-retirement".into(),
        store_owner_id: original.destination_owner_id.clone(),
        upload: replacement.clone(),
        principal_id: replacement.principal_id.clone(),
        tenant_id: replacement.tenant_id.clone(),
        certificate_digest: None,
        canonical_grant_json: "{}".into(),
        admitted_at_ms: 5,
        reason: "explicit metadata contract release".into(),
    };
    retirement.validate().unwrap();
    catalog
        .upload_retirements
        .lock()
        .unwrap()
        .insert(replacement.upload_id.clone(), retirement.clone());
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", &assessment_path, b"", true),
    )
    .await;
    let assessment: serde_json::Value =
        serde_json::from_slice(&response_body(&raw, "200 OK")).unwrap();
    assert_eq!(
        assessment["replacement_retirement"],
        serde_json::to_value(&retirement).unwrap()
    );
    assert_eq!(assessment["status"], "unknown");
    let mut foreign_replacement = replacement.clone();
    foreign_replacement.tenant_id = "other-tenant".into();
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(replacement.upload_id.clone(), foreign_replacement);
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", &assessment_path, b"", true),
    )
    .await;
    response_body(&raw, "503 Service Unavailable");
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(replacement.upload_id.clone(), replacement);
    let mut literal = original.clone();
    literal.command_id = "assessment".into();
    catalog
        .upload_recoveries
        .lock()
        .unwrap()
        .insert(literal.command_id.clone(), literal);
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request(
            "GET",
            "/v1/artifact-uploads/original-upload/recoveries/assessment",
            b"",
            true,
        ),
    )
    .await;
    let literal: serde_json::Value =
        serde_json::from_slice(&response_body(&raw, "200 OK")).unwrap();
    assert_eq!(
        literal["schema"],
        "roboguide.artifact-upload-recovery-observation/v0.1"
    );
    assert!(log.events().unwrap().is_empty());
    assert!(log.require_writes_enabled().is_err());
    *catalog.recovery_fence.lock().unwrap() =
        Some("actual cache outcome uncertainty fixture".into());
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", path, b"", true),
    )
    .await;
    response_body(&raw, "503 Service Unavailable");
    *catalog.recovery_fence.lock().unwrap() = None;
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request(
            "GET",
            "/v1/artifacts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            b"",
            true,
        ),
    )
    .await;
    response_body(&raw, "503 Service Unavailable");
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("POST", path, b"{}", true),
    )
    .await;
    response_body(&raw, "503 Service Unavailable");
    let mut foreign = original.upload.clone();
    foreign.tenant_id = "another-tenant".into();
    catalog
        .upload_records
        .lock()
        .unwrap()
        .insert(foreign.upload_id.clone(), foreign);
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", path, b"", true),
    )
    .await;
    response_body(&raw, "403 Forbidden");
    let raw = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", &assessment_path, b"", true),
    )
    .await;
    response_body(&raw, "403 Forbidden");
    assert_eq!(
        catalog
            .upload_recoveries
            .lock()
            .unwrap()
            .get(&original.command_id),
        Some(&original)
    );
    let restart_log = SqliteEventLog::open(directory.path().join("restart.sqlite3"))
        .unwrap()
        .with_write_failure_fence("actual-restart-cache-observer")
        .unwrap();
    seed_controller_checkpoint(&restart_log);
    let restart_catalog =
        ArtifactCatalog::replay_with_gate(&restart_log, Arc::new(Mutex::new(()))).unwrap();
    recovery_cache_fixture(&restart_catalog);
    restart_log
        .disable_writes("actual unresolved replica authority fixture", true)
        .unwrap();
    let raw = request_once(
        &store,
        &restart_catalog,
        &uploads,
        raw_request("GET", path, b"", true),
    )
    .await;
    response_body(&raw, "503 Service Unavailable");
}

/// Admits and streams actual bytes through HTTP, leaving finalization to the fault-window caller.
async fn staged(
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    id: &str,
    bytes: &[u8],
) {
    let body = serde_json::json!({"upload_id":id}).to_string();
    response_body(
        &request_once(
            store,
            catalog,
            uploads,
            raw_request("POST", "/v1/artifact-uploads", body.as_bytes(), true),
        )
        .await,
        "201 Created",
    );
    response_body(
        &request_once(
            store,
            catalog,
            uploads,
            raw_request(
                "POST",
                &format!("/v1/artifact-uploads/{id}/content"),
                bytes,
                true,
            ),
        )
        .await,
        "202 Accepted",
    );
}

/// CAS completion before SQL completion is reconciled under the same original staged-byte proof.
#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn upload_finalization_sql_failure_preserves_original_byte_proof_without_reupload() {
    let directory = tempfile::tempdir().unwrap();
    let store =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = SqliteEventLog::open(&path).unwrap();
    seed_controller_checkpoint(&log);
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let uploads = test_uploads();
    let bytes = b"original actual upload bytes";
    let digest = digest_bytes(bytes);
    staged(&store, &catalog, &uploads, "original", bytes).await;
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch("CREATE TRIGGER fail_upload_completion BEFORE INSERT ON events WHEN NEW.payload_json LIKE '%\"ArtifactUploadTransition\"%' AND NEW.payload_json LIKE '%\"phase\":\"completed\"%' BEGIN SELECT RAISE(ABORT,'actual upload completion failure'); END;").unwrap();
    let body = serde_json::json!({"content_digest":digest,"byte_size":bytes.len()}).to_string();
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request(
                "POST",
                "/v1/artifact-uploads/original/finalize",
                body.as_bytes(),
                true,
            ),
        )
        .await,
        "500 Internal Server Error",
    );
    assert!(store.contains(&digest).unwrap());
    let original = catalog.upload_record("original").unwrap().unwrap();
    assert_eq!(original.phase, domain::ArtifactUploadPhase::Prepared);
    assert_eq!(uploads.lock().unwrap().active_uploads(), 0);
    sql.execute_batch("DROP TRIGGER fail_upload_completion")
        .unwrap();
    let restored = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    assert_eq!(
        restored.upload_record("original").unwrap(),
        Some(original.clone())
    );
    drop(uploads);
    drop(store);
    let restarted_store =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    let restarted_uploads =
        super::super::server::restore_uploads(&restarted_store, &restored).unwrap();
    let response = request_once(
        &restarted_store,
        &restored,
        &restarted_uploads,
        raw_request(
            "POST",
            "/v1/artifact-uploads/original/finalize",
            body.as_bytes(),
            true,
        ),
    )
    .await;
    let completed: serde_json::Value =
        serde_json::from_slice(&response_body(&response, "201 Created")).unwrap();
    assert_eq!(
        completed["receipt"]["admitted_at_ms"],
        original.admitted_at_ms
    );
    assert_eq!(
        completed["receipt"]["prepared_at_ms"],
        original.prepared_at_ms.unwrap()
    );
    assert_eq!(completed["receipt"]["deadline_ms"], original.deadline_ms);
    let replay = request_once(
        &restarted_store,
        &restored,
        &restarted_uploads,
        raw_request(
            "POST",
            "/v1/artifact-uploads/original/finalize",
            body.as_bytes(),
            true,
        ),
    )
    .await;
    assert_eq!(
        response_body(&replay, "201 Created"),
        response_body(&response, "201 Created")
    );
    assert_eq!(
        log.decoded_events()
            .unwrap()
            .iter()
            .filter(|e| matches!(e.payload(), EventPayload::ArtifactUploadTransition { .. }))
            .count(),
        3
    );
    assert!(matches!(
        log.archive_and_gc(
            log.latest_sequence().unwrap(),
            &state::FileArchiveSink::new(directory.path().join("archive"))
        ),
        Err(state::SqliteEventLogError::ArchivePolicy(_))
    ));
}

/// Failed Prepared SQL revokes its transient read scope, preserving the exact Started upload for retry.
#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn prepared_sql_failure_preserves_upload_without_leaking_a_scoped_read_grant() {
    let directory = tempfile::tempdir().unwrap();
    let store =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = SqliteEventLog::open(&path).unwrap();
    seed_controller_checkpoint(&log);
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let uploads = test_uploads();
    let bytes = b"original prepared SQL rollback payload";
    let digest = digest_bytes(bytes);
    staged(
        &store,
        &catalog,
        &uploads,
        "original-prepared-failure",
        bytes,
    )
    .await;
    let original = catalog
        .upload_record("original-prepared-failure")
        .unwrap()
        .unwrap();
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch("CREATE TRIGGER fail_prepared BEFORE INSERT ON events WHEN NEW.payload_json LIKE '%\"ArtifactUploadTransition\"%' AND NEW.payload_json LIKE '%\"phase\":\"prepared\"%' BEGIN SELECT RAISE(ABORT,'actual original Prepared SQL failure'); END;").unwrap();
    let body = serde_json::json!({"content_digest":digest,"byte_size":bytes.len()}).to_string();
    let request = raw_request(
        "POST",
        "/v1/artifact-uploads/original-prepared-failure/finalize",
        body.as_bytes(),
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, request.clone()).await,
        "500 Internal Server Error",
    );
    assert_eq!(
        catalog.upload_record("original-prepared-failure").unwrap(),
        Some(original.clone())
    );
    assert!(!store.contains(&digest).unwrap());
    assert!(
        store
            .open_replication_source(&digest, bytes.len() as u64)
            .is_err()
    );
    assert_eq!(uploads.lock().unwrap().active_uploads(), 1);
    assert_eq!(log.len().unwrap(), 1);
    sql.execute_batch("DROP TRIGGER fail_prepared").unwrap();
    let response = request_once(&store, &catalog, &uploads, request).await;
    let completed: serde_json::Value =
        serde_json::from_slice(&response_body(&response, "201 Created")).unwrap();
    assert_eq!(
        completed["receipt"]["admitted_at_ms"],
        original.admitted_at_ms
    );
    assert_eq!(completed["receipt"]["deadline_ms"], original.deadline_ms);
    assert_eq!(log.len().unwrap(), 3);
    assert_eq!(uploads.lock().unwrap().active_uploads(), 0);
    store.verify_artifact(&digest, bytes.len() as u64).unwrap();
}

/// A declared digest already in CAS never substitutes for this upload's actual complete staged bytes.
#[tokio::test]
async fn existing_foreign_cas_cannot_complete_an_empty_upload() {
    let directory = tempfile::tempdir().unwrap();
    let store = FileSystemArtifactStore::new(directory.path().join("cas")).unwrap();
    let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    seed_controller_checkpoint(&log);
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let uploads = test_uploads();
    let bytes = b"foreign bytes";
    let digest = digest_bytes(bytes);
    let mut foreign = store.begin_upload("foreign-source").unwrap();
    foreign.write_chunk(bytes).unwrap();
    foreign.finalize(&digest, bytes.len() as u64).unwrap();
    let body = serde_json::json!({"upload_id":"empty"}).to_string();
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request("POST", "/v1/artifact-uploads", body.as_bytes(), true),
        )
        .await,
        "201 Created",
    );
    let body = serde_json::json!({"content_digest":digest,"byte_size":bytes.len()}).to_string();
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request(
                "POST",
                "/v1/artifact-uploads/empty/finalize",
                body.as_bytes(),
                true,
            ),
        )
        .await,
        "409 Conflict",
    );
    let original = catalog.upload_record("empty").unwrap().unwrap();
    assert_eq!(original.phase, domain::ArtifactUploadPhase::Started);
    assert!(original.content_digest.is_none());
    assert_eq!(uploads.lock().unwrap().active_uploads(), 0);
}

/// Scope cannot be widened through another Memory selector or the typed map alias, even by local admin.
#[test]
fn new_manifest_alias_cannot_remove_original_group_scope() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    seed_controller_checkpoint(&log);
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let digest = digest_bytes(b"scoped");
    let manifest = fixture_memory_manifest("private-source", MemoryKind::Artifact, &digest, 6);
    let mut value = serde_json::to_value(&manifest).unwrap();
    value["scope"] =
        serde_json::json!({"kind":"execution_group","execution_group_id":"original-group"});
    let scoped: MemoryArtifactManifest = serde_json::from_value(value).unwrap();
    catalog
        .append(EventPayload::MemoryManifestPublished { manifest: scoped })
        .unwrap();
    assert!(
        catalog
            .authorize_publication_bytes(
                &ArtifactPrincipal::local(),
                Some((&digest, 6)),
                Some(&manifest),
                &AllowTestMemoryAdmission
            )
            .is_err()
    );
    assert!(
        catalog
            .authorize_publication_bytes(
                &ArtifactPrincipal::local(),
                Some((&digest, 6)),
                None,
                &AllowTestMemoryAdmission
            )
            .is_err()
    );
}
