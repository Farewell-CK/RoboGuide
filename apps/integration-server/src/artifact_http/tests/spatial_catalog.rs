use super::*;

/// Exercises Node A map export through Node B import and strong localization verification.
#[tokio::test]
async fn spatial_map_cross_node_exchange_reaches_strong_verification() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_path = directory.path().join("events.sqlite3");
    let event_log = SqliteEventLog::open(&event_path).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();
    let bytes = b"map-bytes";
    let digest = digest_bytes(bytes);

    let start = raw_request(
        "POST",
        "/v1/artifact-uploads",
        br#"{"upload_id":"upload-test"}"#,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, start).await,
        "201 Created",
    );
    let content = raw_request(
        "PUT",
        "/v1/artifact-uploads/upload-test/content",
        bytes,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, content).await,
        "202 Accepted",
    );
    let finalize_body = serde_json::to_vec(&serde_json::json!({
        "content_digest": digest,
        "byte_size": bytes.len(),
    }))
    .expect("finalize body serializes");
    let finalize = raw_request(
        "POST",
        "/v1/artifact-uploads/upload-test/finalize",
        &finalize_body,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, finalize).await,
        "201 Created",
    );

    let manifest = fixture_manifest(&digest, bytes.len() as u64);
    let publish_body = serde_json::to_vec(&manifest).expect("manifest serializes");
    let publish = raw_request("POST", "/v1/maps/map-a/revisions/r1", &publish_body, true);
    response_body(
        &request_once(&store, &catalog, &uploads, publish).await,
        "201 Created",
    );

    let get_manifest = raw_request("GET", "/v1/maps/map-a/revisions/r1", &[], false);
    let manifest_body = response_body(
        &request_once(&store, &catalog, &uploads, get_manifest).await,
        "200 OK",
    );
    let manifest_json: serde_json::Value =
        serde_json::from_slice(&manifest_body).expect("manifest response is JSON");
    assert_eq!(manifest_json["status"], "Published");

    let list_memories = raw_request("GET", "/v1/memories", &[], false);
    let memories_body = response_body(
        &request_once(&store, &catalog, &uploads, list_memories).await,
        "200 OK",
    );
    let memories_json: serde_json::Value =
        serde_json::from_slice(&memories_body).expect("memory catalog response is JSON");
    assert_eq!(memories_json["memories"][0]["kind"], "spatial");
    assert_eq!(memories_json["memories"][0]["typed_extension"], "map");
    assert_eq!(
        memories_json["memories"][0]["artifact"]["content_digest"],
        digest
    );

    let memory_detail = raw_request("GET", "/v1/memories/map-a/revisions/r1", &[], false);
    let memory_detail_body = response_body(
        &request_once(&store, &catalog, &uploads, memory_detail).await,
        "200 OK",
    );
    let memory_detail_json: serde_json::Value = serde_json::from_slice(&memory_detail_body)
        .expect("typed map Memory detail response is JSON");
    assert_eq!(memory_detail_json["manifest"]["typed_extension"], "map");
    assert_eq!(
        memory_detail_json["manifest"]["selector"]["memory_id"],
        "map-a"
    );
    assert_eq!(
        memory_detail_json["replicas"].as_array().map(Vec::len),
        Some(0)
    );

    let get_blob = raw_request("GET", &format!("/v1/artifacts/{digest}"), &[], false);
    let blob_body = response_body(
        &request_once(&store, &catalog, &uploads, get_blob).await,
        "200 OK",
    );
    assert_eq!(blob_body, bytes);

    let staged_body = serde_json::to_vec(&serde_json::json!({
        "manifest": manifest,
        "node_id": "dog-b",
        "mission_id": "mission-b",
        "status": "staged"
    }))
    .expect("replica body serializes");
    let staged = raw_request(
        "POST",
        "/v1/maps/map-a/revisions/r1/replicas",
        &staged_body,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, staged).await,
        "202 Accepted",
    );
    let imported_body = serde_json::to_vec(&serde_json::json!({
        "manifest": manifest,
        "node_id": "dog-b",
        "mission_id": "mission-b",
        "status": "imported"
    }))
    .expect("replica body serializes");
    let imported = raw_request(
        "POST",
        "/v1/maps/map-a/revisions/r1/replicas",
        &imported_body,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, imported).await,
        "202 Accepted",
    );
    let evidence_body = serde_json::to_vec(&serde_json::json!({
        "schema": "roboguide.localization-verification-evidence/v0.1",
        "map_id": "map-a",
        "revision_id": "r1",
        "content_digest": digest,
        "byte_size": bytes.len(),
        "mission_id": "mission-b",
        "task_id": "verify-map",
        "group_id": "group-b",
        "role_id": "localizer",
        "node_id": "dog-b",
        "execution_id": "execution-verify",
        "local_attempt_id": "local-verify-1",
        "active_local_map_id": "map-a-local",
        "mode": "localization",
        "pose_quality": {
            "metric": "translation_stddev",
            "value": "0.08",
            "threshold": "0.10",
            "unit": "m",
            "comparison": "at_most"
        },
        "frames": {"map": "map", "odom": "odom", "base": "base_link"},
        "anchor_id": "anchor-lab",
        "source_observed_at_ms": 50
    }))
    .expect("localization evidence serializes");
    let stale_evidence = raw_memory_request(
        "/v1/maps/map-a/revisions/r1/localization-evidence",
        &evidence_body,
        "dog-b",
        "session-old",
    );
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &uploads,
            stale_evidence,
            Arc::new(FixtureSessionMemoryAdmission),
        )
        .await,
        "403 Forbidden",
    );
    let stale_attempt_evidence = raw_memory_request(
        "/v1/maps/map-a/revisions/r1/localization-evidence",
        &evidence_body,
        "dog-b",
        "session-current",
    );
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &uploads,
            stale_attempt_evidence,
            Arc::new(DenyLocalizationAttemptAdmission),
        )
        .await,
        "403 Forbidden",
    );
    let evidence = raw_memory_request(
        "/v1/maps/map-a/revisions/r1/localization-evidence",
        &evidence_body,
        "dog-b",
        "session-current",
    );
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &uploads,
            evidence,
            Arc::new(FixtureSessionMemoryAdmission),
        )
        .await,
        "201 Created",
    );
    let selector = MapRevisionSelector::new(
        MapId::new("map-a").expect("map id"),
        MapRevisionId::new("r1").expect("revision id"),
    );
    let projection =
        MapCatalogProjection::from_events(event_log.decoded_events().expect("events decode"))
            .expect("catalog projection rebuilds");
    assert_eq!(
        projection.replicas(&selector)[0].status(),
        MapReplicaStatus::Verified
    );
    assert!(projection.replicas(&selector)[0].is_strongly_verified());
    let reopened = SqliteEventLog::open(&event_path).expect("event log reopens");
    let checkpoint = reopened
        .load_checkpoint()
        .expect("checkpoint reads")
        .expect("checkpoint remains present");
    assert_eq!(checkpoint.event_sequence, 4);
    assert_eq!(
        checkpoint.event_sequence,
        reopened.latest_sequence().expect("log head reads")
    );
    assert_eq!(checkpoint.schema, TEST_CHECKPOINT_SCHEMA);
    assert_eq!(checkpoint.checkpoint_json, TEST_CHECKPOINT_JSON);
}

/// Publishing a manifest without a finalized CAS blob is rejected before catalog mutation.
#[tokio::test]
async fn publish_requires_existing_blob() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();
    let digest = digest_bytes(b"missing");
    let manifest = fixture_manifest(&digest, 7);
    let body = serde_json::to_vec(&manifest).expect("manifest serializes");
    let request = raw_request("POST", "/v1/maps/map-a/revisions/r1", &body, true);
    response_body(
        &request_once(&store, &catalog, &uploads, request).await,
        "404 Not Found",
    );
    assert!(event_log.is_empty().expect("event log reads"));
    assert!(
        catalog
            .revisions()
            .expect("catalog remains readable")
            .is_empty()
    );
    let checkpoint = event_log
        .load_checkpoint()
        .expect("checkpoint reads")
        .expect("checkpoint remains present");
    assert_eq!(checkpoint.event_sequence, 0);
    assert_eq!(checkpoint.checkpoint_json, TEST_CHECKPOINT_JSON);
}

/// Publication rejects a same-size CAS replacement whose bytes no longer match its digest.
#[tokio::test]
async fn publish_rehashes_the_finalized_blob_before_catalog_mutation() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();
    let bytes = b"map-bytes";
    let digest = digest_bytes(bytes);
    let mut upload = store.begin_upload("upload-corrupt").expect("upload begins");
    upload.write_chunk(bytes).expect("map bytes write");
    let artifact = upload
        .finalize(&digest, bytes.len() as u64)
        .expect("upload finalizes");
    std::fs::remove_file(artifact.path()).expect("test removes sealed blob");
    std::fs::write(artifact.path(), b"bad-bytes")
        .expect("test replaces blob with same-size corruption");

    let manifest = fixture_manifest(&digest, bytes.len() as u64);
    let body = serde_json::to_vec(&manifest).expect("manifest serializes");
    let request = raw_request("POST", "/v1/maps/map-a/revisions/r1", &body, true);
    response_body(
        &request_once(&store, &catalog, &uploads, request).await,
        "409 Conflict",
    );
    assert!(event_log.is_empty().expect("event log reads"));
    assert!(
        catalog
            .revisions()
            .expect("catalog remains readable")
            .is_empty()
    );
}

/// Spatial evidence refuses to create controller authority when no checkpoint exists.
#[test]
fn append_requires_an_existing_controller_checkpoint() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let manifest = fixture_manifest(&digest_bytes(b"map"), 3);

    let error = catalog
        .append(EventPayload::MapArtifactPublished { manifest })
        .expect_err("missing controller checkpoint must fail closed");

    assert_eq!(error.status(), "500 Internal Server Error");
    assert!(event_log.is_empty().expect("event log reads"));
    assert!(
        catalog
            .revisions()
            .expect("catalog remains readable")
            .is_empty()
    );
}

/// Spatial evidence refuses to conceal a checkpoint that already trails the event log.
#[test]
fn append_rejects_a_divergent_controller_checkpoint() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let manifest = fixture_manifest(&digest_bytes(b"map"), 3);
    let correlation =
        domain::CorrelationId::new("divergent-test").expect("correlation identity is valid");
    let mut divergent_log = event_log.clone();
    divergent_log.append(
        TimestampMs::new(1),
        &correlation,
        None,
        EventPayload::MapArtifactPublished {
            manifest: manifest.clone(),
        },
    );
    assert!(
        divergent_log
            .take_error()
            .expect("append health reads")
            .is_none()
    );
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays the divergent log");

    let error = catalog
        .append(EventPayload::MapArtifactPublished { manifest })
        .expect_err("divergent controller checkpoint must fail closed");

    assert_eq!(error.status(), "500 Internal Server Error");
    assert_eq!(event_log.latest_sequence().expect("log head reads"), 1);
    let checkpoint = event_log
        .load_checkpoint()
        .expect("checkpoint reads")
        .expect("checkpoint remains present");
    assert_eq!(checkpoint.event_sequence, 0);
    assert_eq!(
        catalog.revisions().expect("catalog remains readable").len(),
        1
    );
}

/// Spatial evidence ordering remains monotonic when the local wall clock moves backward.
#[test]
fn append_allocates_timestamp_after_persisted_high_water() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let forced_future = TimestampMs::new(9_000_000_000_000);
    *catalog
        .timestamp_high_water
        .lock()
        .expect("timestamp high-water lock remains healthy") = forced_future;
    let manifest = fixture_manifest(&digest_bytes(b"map"), 3);
    catalog
        .append(EventPayload::MapArtifactPublished {
            manifest: manifest.clone(),
        })
        .expect("publication appends after forced high-water");
    catalog
        .append(EventPayload::MapArtifactStaged {
            manifest: manifest.clone(),
            node_id: NodeId::new("dog-b").expect("node id is valid"),
            mission_id: MissionId::new("mission-import").expect("mission id is valid"),
        })
        .expect("later replica evidence remains ordered");

    let events = event_log.decoded_events().expect("durable events decode");
    assert_eq!(events.len(), 2);
    assert!(events[0].timestamp() > forced_future);
    assert!(events[1].timestamp() > events[0].timestamp());
    assert_eq!(
        catalog
            .projection
            .lock()
            .expect("catalog lock remains healthy")
            .replicas(manifest.selector())[0]
            .status(),
        MapReplicaStatus::Staged
    );
}

/// An uncertain catalog commit fences both reads and writes until replay on restart.
#[test]
fn recovery_fence_rejects_stale_projection_access() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let fenced = catalog.fence("simulated commit uncertainty");
    assert_eq!(fenced.status(), "503 Service Unavailable");
    assert!(matches!(
        catalog.revisions(),
        Err(error) if error.status() == "503 Service Unavailable"
    ));
    assert!(matches!(
        catalog.append(EventPayload::MapArtifactPublished {
            manifest: fixture_manifest(&digest_bytes(b"map"), 3),
        }),
        Err(error) if error.status() == "503 Service Unavailable"
    ));
}
