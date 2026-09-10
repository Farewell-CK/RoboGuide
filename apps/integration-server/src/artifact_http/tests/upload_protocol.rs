use super::*;

/// Typed maps and generic Memory cannot claim the same unified selector in either order.
#[test]
fn typed_and_generic_memory_selectors_are_mutually_exclusive() {
    let first_directory = tempfile::tempdir().expect("temporary directory exists");
    let first_log = SqliteEventLog::open(first_directory.path().join("events.sqlite3"))
        .expect("event log opens");
    seed_controller_checkpoint(&first_log);
    let first_catalog = ArtifactCatalog::replay_with_gate(&first_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let digest = "a".repeat(64);
    let map = fixture_manifest(&digest, 12);
    let generic = fixture_memory_manifest("map-a", MemoryKind::Artifact, &digest, 12);
    first_catalog
        .append(EventPayload::MapArtifactPublished {
            manifest: map.clone(),
        })
        .expect("typed map claims selector first");
    assert_eq!(
        first_catalog
            .append(EventPayload::MemoryManifestPublished {
                manifest: generic.clone(),
            })
            .expect_err("generic Memory must not reuse typed selector")
            .status(),
        "409 Conflict"
    );

    let second_directory = tempfile::tempdir().expect("temporary directory exists");
    let second_log = SqliteEventLog::open(second_directory.path().join("events.sqlite3"))
        .expect("event log opens");
    seed_controller_checkpoint(&second_log);
    let second_catalog = ArtifactCatalog::replay_with_gate(&second_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    second_catalog
        .append(EventPayload::MemoryManifestPublished { manifest: generic })
        .expect("generic Memory claims selector first");
    assert_eq!(
        second_catalog
            .append(EventPayload::MapArtifactPublished { manifest: map })
            .expect_err("typed map must not reuse generic selector")
            .status(),
        "409 Conflict"
    );
}

/// Fixed-length framing rejects ambiguous requests and separates head and body size limits.
#[test]
fn request_head_framing_is_bounded_and_unambiguous() {
    let cases = [
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\n\r\n".as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.0\r\nContent-Length: 0\r\n\r\n".as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nMalformed\r\nContent-Length: 0\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: 2\r\n\r\nabc"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: +1\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/memories/a/revisions/r1 HTTP/1.1\r\nContent-Length: 0\r\nX-RoboGuide-Node-Id: dog-a\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/memories/a/revisions/r1 HTTP/1.1\r\nContent-Length: 0\r\nX-RoboGuide-Session-Id: session-a\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
        ];
    for (bytes, expected_status) in cases {
        let header_end = bytes
            .windows(4)
            .position(|window| window == b"\r\n\r\n")
            .map(|index| index + 4)
            .expect("fixture has complete headers");
        let error = parse_request_head(bytes.to_vec(), header_end)
            .expect_err("ambiguous framing is rejected");
        assert_eq!(error.status(), expected_status);
    }

    let oversized_length = format!(
        "POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
        MAX_ARTIFACT_BYTES + 1
    )
    .into_bytes();
    let oversized_length_end = oversized_length
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|index| index + 4)
        .expect("fixture has complete headers");
    assert_eq!(
        parse_request_head(oversized_length, oversized_length_end)
            .expect_err("oversized body is rejected")
            .status(),
        "413 Payload Too Large"
    );

    let mut oversized_header = b"GET /healthz HTTP/1.1\r\nX-Pad: ".to_vec();
    oversized_header.resize(MAX_HEADER_BYTES, b'a');
    oversized_header.extend_from_slice(b"\r\n\r\n");
    let oversized_header_end = oversized_header.len();
    assert_eq!(
        parse_request_head(oversized_header, oversized_header_end)
            .expect_err("oversized headers are rejected")
            .status(),
        "431 Request Header Fields Too Large"
    );

    let body = vec![b'x'; MAX_HEADER_BYTES + 1];
    let mut large_prefetch = format!(
        "PUT /v1/artifact-uploads/upload/content HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
        body.len()
    )
    .into_bytes();
    let large_prefetch_header_end = large_prefetch.len();
    large_prefetch.extend_from_slice(&body);
    let parsed = parse_request_head(large_prefetch, large_prefetch_header_end)
        .expect("a bounded header may be followed by a large body prefix");
    assert_eq!(parsed.content_length, body.len() as u64);
    assert_eq!(parsed.prefetched_body, body);
}

/// Header delimiters and fixed-length JSON/artifact bodies may arrive in separate TCP reads.
#[tokio::test]
async fn fragmented_headers_and_bodies_are_read_to_declared_length() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();

    let start_body = br#"{"upload_id":"fragmented"}"#;
    let start_head = format!(
        "POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r",
        start_body.len()
    );
    let start_fragments = [
        start_head.as_bytes(),
        b"\n{".as_slice(),
        &start_body[1..12],
        &start_body[12..],
    ];
    response_body(
        &fragmented_request_once(&store, &catalog, &uploads, &start_fragments).await,
        "201 Created",
    );

    let artifact = b"map-body-arrives-in-several-packets";
    let content_head = format!(
        "PUT /v1/artifact-uploads/fragmented/content HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        artifact.len()
    );
    let content_fragments = [
        content_head.as_bytes(),
        &artifact[..3],
        &artifact[3..17],
        &artifact[17..],
    ];
    let response = fragmented_request_once(&store, &catalog, &uploads, &content_fragments).await;
    let response: serde_json::Value =
        serde_json::from_slice(&response_body(&response, "202 Accepted"))
            .expect("append response is JSON");
    assert_eq!(response["received_bytes"], artifact.len() as u64);

    let finalize_body = serde_json::to_vec(&serde_json::json!({
        "content_digest": digest_bytes(artifact),
        "byte_size": artifact.len(),
    }))
    .expect("finalize body serializes");
    let finalize = raw_request(
        "POST",
        "/v1/artifact-uploads/fragmented/finalize",
        &finalize_body,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, finalize).await,
        "201 Created",
    );
}

/// JSON endpoints reject their smaller body limit before attempting to wait for payload bytes.
#[tokio::test]
async fn json_body_limit_returns_payload_too_large() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();
    let request = format!(
            "POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            MAX_JSON_BODY_BYTES + 1
        )
        .into_bytes();
    response_body(
        &request_once(&store, &catalog, &uploads, request).await,
        "413 Payload Too Large",
    );
}

/// Registry quotas reserve declared bytes atomically and expiration removes staged files.
#[test]
fn upload_registry_enforces_count_bytes_and_idle_expiration() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let now = Instant::now();
    let mut registry = UploadRegistry::with_limits(2, 5, Duration::from_secs(10));
    let first = store.begin_upload("first").expect("first upload begins");
    registry
        .insert_new("first".to_string(), first, now)
        .expect("first upload enters registry");
    let second = store.begin_upload("second").expect("second upload begins");
    registry
        .insert_new("second".to_string(), second, now)
        .expect("second upload enters registry");
    let excess = store
        .begin_upload("excess")
        .expect("temporary upload begins");
    let error = registry
        .insert_new("excess".to_string(), excess, now)
        .expect_err("count quota rejects a third identity");
    assert_eq!(error.status(), "429 Too Many Requests");
    assert!(!store.root().join("staging").join("excess.partial").exists());

    let mut first = registry
        .take_for_append("first", 4)
        .expect("four bytes fit aggregate quota");
    first
        .session
        .upload
        .write_chunk(b"map1")
        .expect("reserved bytes write");
    registry
        .restore_after_append("first".to_string(), first, now)
        .expect("completed append restores session");
    let error = match registry.take_for_append("second", 2) {
        Ok(_) => panic!("aggregate byte quota must reject declared body"),
        Err(error) => error,
    };
    assert_eq!(error.status(), "413 Payload Too Large");
    assert_eq!(registry.active_bytes, 4);

    let expired_at = now
        .checked_add(Duration::from_secs(10))
        .expect("test instant can advance");
    assert_eq!(
        registry.expire_idle(expired_at).expect("expiry succeeds"),
        2
    );
    assert_eq!(registry.active_uploads(), 0);
    assert_eq!(registry.active_bytes, 0);
    assert!(!store.root().join("staging").join("first.partial").exists());
    assert!(!store.root().join("staging").join("second.partial").exists());
}

/// An interrupted body and a mismatched finalize both abort staging and release all quota.
#[tokio::test]
async fn upload_errors_abort_session_and_release_registry_quota() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();

    let interrupted_start = Request {
        path: "/v1/artifact-uploads".to_string(),
        body: br#"{"upload_id":"interrupted"}"#.to_vec(),
        memory_publisher: None,
    };
    start_upload(&interrupted_start, &store, &uploads).expect("upload starts");
    let interrupted = raw_request_with_declared_length(
        "PUT",
        "/v1/artifact-uploads/interrupted/content",
        b"ab",
        4,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, interrupted).await,
        "400 Bad Request",
    );
    {
        let registry = uploads.lock().expect("registry remains readable");
        assert_eq!(registry.active_uploads(), 0);
        assert_eq!(registry.active_bytes, 0);
    }
    assert!(
        !store
            .root()
            .join("staging")
            .join("interrupted.partial")
            .exists()
    );

    let mismatch_start = raw_request(
        "POST",
        "/v1/artifact-uploads",
        br#"{"upload_id":"mismatch"}"#,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, mismatch_start).await,
        "201 Created",
    );
    let content = raw_request("PUT", "/v1/artifact-uploads/mismatch/content", b"map", true);
    response_body(
        &request_once(&store, &catalog, &uploads, content).await,
        "202 Accepted",
    );
    let finalize_body = serde_json::to_vec(&serde_json::json!({
        "content_digest": digest_bytes(b"different"),
        "byte_size": 3,
    }))
    .expect("finalize body serializes");
    let finalize = raw_request(
        "POST",
        "/v1/artifact-uploads/mismatch/finalize",
        &finalize_body,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, finalize).await,
        "409 Conflict",
    );
    {
        let registry = uploads.lock().expect("registry remains readable");
        assert_eq!(registry.active_uploads(), 0);
        assert_eq!(registry.active_bytes, 0);
    }
    assert!(
        !store
            .root()
            .join("staging")
            .join("mismatch.partial")
            .exists()
    );
    let explicit_start = raw_request(
        "POST",
        "/v1/artifact-uploads",
        br#"{"upload_id":"explicit-abort"}"#,
        true,
    );
    response_body(
        &request_once(&store, &catalog, &uploads, explicit_start).await,
        "201 Created",
    );
    let explicit_abort = raw_request("DELETE", "/v1/artifact-uploads/explicit-abort", &[], false);
    response_body(
        &request_once(&store, &catalog, &uploads, explicit_abort).await,
        "200 OK",
    );
    let registry = uploads.lock().expect("registry remains readable");
    assert_eq!(registry.active_uploads(), 0);
    assert_eq!(registry.active_bytes, 0);
}
