//! Actual file/SQL fault windows behind the production HTTP upload workflow.

use super::*;

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
#[tokio::test]
async fn upload_finalization_sql_failure_preserves_original_byte_proof_without_reupload() {
    let directory = tempfile::tempdir().unwrap();
    let store = FileSystemArtifactStore::new(directory.path().join("cas")).unwrap();
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
    let empty_uploads = test_uploads();
    let response = request_once(
        &store,
        &restored,
        &empty_uploads,
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
        &store,
        &restored,
        &empty_uploads,
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
