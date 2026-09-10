//! Artifact staging and publication tests.

use super::*;
use std::io::Write;

/// Builds one typed manifest for transport-envelope unit tests.
fn manifest(digest: &str) -> MapArtifactManifest {
    manifest_at(digest, 1)
}

/// Builds one typed publication attempt with a caller-controlled creation timestamp.
fn manifest_at(digest: &str, created_at_ms: u64) -> MapArtifactManifest {
    let artifact = MapArtifactRef::new(
        MapRevisionSelector::new(
            MapId::new("lab").expect("map id"),
            MapRevisionId::new("r1").expect("revision id"),
        ),
        ContentDigest::new(digest).expect("digest"),
        4,
    );
    MapArtifactManifest::new(
        artifact,
        "application/octet-stream",
        "grid-v1",
        NodeId::new("dog-a").expect("node id"),
        None,
        MissionId::new("mission-a").expect("mission id"),
        Some("execution-a".to_string()),
        None,
        "map",
        "enu",
        SpatialAnchorId::new("anchor-lab").expect("anchor"),
        Some(0.05),
        TimestampMs::new(created_at_ms),
        None,
    )
    .expect("manifest")
}

/// Constructs a valid artifact client for local unit tests.
fn client(directory: &Path) -> ArtifactStager {
    client_at(directory, "http://127.0.0.1:18080")
}

/// Constructs a valid artifact client against one test-owned endpoint.
fn client_at(directory: &Path, endpoint: &str) -> ArtifactStager {
    ArtifactStager::from_config(
        &ArtifactServiceConfig {
            endpoint: endpoint.to_string(),
            cache_directory: PathBuf::from("cache"),
            max_artifact_bytes: 1024,
            chunk_size_bytes: 4,
            connect_timeout_ms: 5_000,
            read_timeout_ms: 30_000,
            input_bindings: Vec::new(),
            output_bindings: Vec::new(),
        },
        directory,
    )
    .expect("stager config is valid")
}

/// Builds one complete static output binding for local freeze tests.
fn output_binding() -> ArtifactOutputBindingConfig {
    ArtifactOutputBindingConfig {
        id: "lab-r1-output".to_string(),
        map_id: "lab".to_string(),
        revision_id: "r1".to_string(),
        source_path: PathBuf::from("outputs/lab-r1.bundle"),
        media_type: "application/octet-stream".to_string(),
        format_name: "grid".to_string(),
        format_version: "v1".to_string(),
        root_frame: "map".to_string(),
        coordinate_convention: "enu".to_string(),
        spatial_anchor_id: "anchor-lab".to_string(),
        resolution_meters: Some(0.05),
    }
}

/// Builds exact provenance for the execution that produced a freeze-test artifact.
fn build_provenance() -> ArtifactProvenance {
    let mission = MissionId::new("mission-a").expect("mission id");
    ArtifactProvenance {
        producer_node_id: NodeId::new("dog-a").expect("node id"),
        producer_local_system_id: Some(
            domain::LocalSystemId::new("mapping-runtime").expect("local system id"),
        ),
        source_mission_id: mission.clone(),
        source_execution_id: Some("build-execution-a".to_string()),
        source_task_ref: Some(TaskRef::new(
            mission,
            TaskId::new("build-map").expect("task id"),
        )),
        created_at: TimestampMs::new(7),
        parent_revision_id: None,
    }
}

/// Digest normalization accepts both contract spellings and rejects malformed values.
#[test]
fn normalizes_digest_prefix() {
    let plain = "a".repeat(64);
    assert_eq!(
        normalize_digest(&plain).expect("plain digest"),
        format!("sha256:{plain}")
    );
    assert_eq!(
        normalize_digest(&format!("sha256:{plain}")).expect("prefixed digest"),
        format!("sha256:{plain}")
    );
    assert!(normalize_digest(&"A".repeat(64)).is_err());
}

/// Binding paths cannot escape the deployment-owned cache root.
#[test]
fn rejects_path_traversal() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let stager = client(directory.path());
    let binding = ArtifactInputBindingConfig {
        id: "map".to_string(),
        map_id: "lab".to_string(),
        revision_id: "r1".to_string(),
        content_digest: None,
        target_path: PathBuf::from("../outside.map"),
    };
    let result = stager.target_path(&binding.target_path);
    assert!(matches!(result, Err(ArtifactError::Configuration(_))));
}

/// A verified file is reused only when both size and digest match.
#[tokio::test]
async fn verifies_cached_file_identity() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let path = directory.path().join("map.bin");
    let mut file = std::fs::File::create(&path).expect("file creates");
    file.write_all(b"map-bytes").expect("fixture writes");
    let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));
    assert!(
        verified_file(&path, &digest, 9, 1024)
            .await
            .expect("verify runs")
    );
    assert!(
        !verified_file(&path, &digest, 8, 1024)
            .await
            .expect("size differs")
    );
}

/// Input staging exposes only verified read-only bytes and removes rejected temporary copies.
#[tokio::test]
async fn copies_only_verified_bytes_to_the_input_target() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let source = directory.path().join("source.bin");
    let target = directory.path().join("target.bin");
    tokio::fs::write(&source, b"wrong-map")
        .await
        .expect("invalid source writes");
    let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));

    assert!(matches!(
        copy_verified_atomic(&source, &target, &digest, 9, 1024).await,
        Err(ArtifactError::ManifestMismatch(_))
    ));
    assert!(!target.exists());

    tokio::fs::write(&source, b"map-bytes")
        .await
        .expect("valid source writes");
    copy_verified_atomic(&source, &target, &digest, 9, 1024)
        .await
        .expect("verified input stages");
    assert_eq!(
        tokio::fs::read(&target).await.expect("target reads"),
        b"map-bytes"
    );
    assert!(
        tokio::fs::metadata(&target)
            .await
            .expect("target metadata reads")
            .permissions()
            .readonly()
    );
}

/// Replica completion requires the exact regular staged file and rejects later mutation.
#[tokio::test]
async fn verifies_staged_input_against_immutable_manifest() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let stager = client(directory.path());
    let digest = format!("sha256:{:x}", Sha256::digest(b"maps"));
    let binding = ArtifactInputBindingConfig {
        id: "lab-r1-input".to_string(),
        map_id: "lab".to_string(),
        revision_id: "r1".to_string(),
        content_digest: Some(digest.clone()),
        target_path: PathBuf::from("inputs/lab-r1.bundle"),
    };
    let manifest = manifest(&digest);
    let target = stager
        .target_path(&binding.target_path)
        .expect("target path validates");
    ensure_directory_tree(target.parent().expect("target has parent"))
        .expect("target directories exist");

    assert!(matches!(
        stager.verify_staged_input(&binding, &manifest).await,
        Err(ArtifactError::ManifestMismatch(_))
    ));
    tokio::fs::write(&target, b"maps")
        .await
        .expect("staged bytes write");
    stager
        .verify_staged_input(&binding, &manifest)
        .await
        .expect("exact staged bytes verify");
    tokio::fs::write(&target, b"evil")
        .await
        .expect("staged bytes mutate");
    assert!(matches!(
        stager.verify_staged_input(&binding, &manifest).await,
        Err(ArtifactError::ManifestMismatch(_))
    ));
}

/// Cache roots and binding parents cannot traverse symbolic-link directory components.
#[cfg(unix)]
#[test]
fn rejects_symlinked_cache_and_binding_directory_components() {
    use std::os::unix::fs::symlink;

    let directory = tempfile::tempdir().expect("temporary directory");
    let outside = directory.path().join("outside");
    std::fs::create_dir(&outside).expect("outside directory creates");
    let linked_cache = directory.path().join("linked-cache");
    symlink(&outside, &linked_cache).expect("cache symlink creates");
    let config = ArtifactServiceConfig {
        endpoint: "http://127.0.0.1:18080".to_string(),
        cache_directory: PathBuf::from("linked-cache"),
        max_artifact_bytes: 1024,
        chunk_size_bytes: 4,
        connect_timeout_ms: 5_000,
        read_timeout_ms: 30_000,
        input_bindings: Vec::new(),
        output_bindings: Vec::new(),
    };
    assert!(matches!(
        ArtifactStager::from_config(&config, directory.path()),
        Err(ArtifactError::Configuration(_))
    ));

    let stager = client(directory.path());
    ensure_directory_tree(stager.cache_directory()).expect("cache directory creates");
    symlink(&outside, stager.cache_directory().join("inputs"))
        .expect("binding parent symlink creates");
    assert!(matches!(
        stager.resolve_path(Path::new("inputs/lab-r1.bundle")),
        Err(ArtifactError::Configuration(_))
    ));
}

/// Symbolic-link leaves fail closed before verification, freezing, or upload transport.
#[cfg(unix)]
#[tokio::test]
async fn rejects_symlinked_artifact_source_leaves() {
    use std::os::unix::fs::symlink;

    let directory = tempfile::tempdir().expect("temporary directory");
    let outside = directory.path().join("outside-map.bin");
    tokio::fs::write(&outside, b"map-bytes")
        .await
        .expect("outside fixture writes");
    let linked = directory.path().join("linked-map.bin");
    symlink(&outside, &linked).expect("source symlink creates");
    let digest = format!("sha256:{:x}", Sha256::digest(b"map-bytes"));

    assert!(matches!(
        verified_file(&linked, &digest, 9, 1024).await,
        Err(ArtifactError::Configuration(_))
    ));

    let stager = client(directory.path());
    assert!(matches!(
        stager.client.upload_file(&output_binding(), &linked).await,
        Err(ArtifactError::Configuration(_))
    ));

    let binding = output_binding();
    let output_path = stager
        .prepare_output_path(&binding)
        .await
        .expect("output parent prepares");
    symlink(&outside, &output_path).expect("configured output symlink creates");
    assert!(matches!(
        stager.freeze_output(&binding, &build_provenance()).await,
        Err(ArtifactError::Configuration(_))
    ));
}

/// Open descriptor reads remain pinned to their inode after the source path is replaced.
#[cfg(unix)]
#[tokio::test]
async fn digest_and_snapshot_use_the_already_opened_regular_file() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let source = directory.path().join("source.bin");
    let replacement = directory.path().join("replacement.bin");
    tokio::fs::write(&source, b"original-map")
        .await
        .expect("original fixture writes");
    let mut digest_source = open_regular_file(&source)
        .await
        .expect("original source opens safely");
    tokio::fs::write(&replacement, b"replacement-map")
        .await
        .expect("replacement fixture writes");
    tokio::fs::rename(&replacement, &source)
        .await
        .expect("source path is replaced");

    let (digest, size) = digest_open_file(&mut digest_source, 4, 1024)
        .await
        .expect("open descriptor hashes");
    assert_eq!(
        digest,
        format!("sha256:{:x}", Sha256::digest(b"original-map"))
    );
    assert_eq!(size, 12);

    let snapshot_source = open_regular_file(&source)
        .await
        .expect("replacement source opens safely");
    tokio::fs::write(&replacement, b"latest-map")
        .await
        .expect("latest fixture writes");
    tokio::fs::rename(&replacement, &source)
        .await
        .expect("source path is replaced again");
    let (snapshot_digest, snapshot_size, snapshot_path) =
        snapshot_open_file(snapshot_source, &directory.path().join("prepared"), 4, 1024)
            .await
            .expect("open descriptor snapshots");
    assert_eq!(
        snapshot_digest,
        format!("sha256:{:x}", Sha256::digest(b"replacement-map"))
    );
    assert_eq!(snapshot_size, 15);
    assert_eq!(
        tokio::fs::read(snapshot_path)
            .await
            .expect("snapshot reads"),
        b"replacement-map"
    );
}

/// Freezing preserves exact completed bytes and build provenance after the source changes.
#[tokio::test]
async fn freezes_output_before_later_publication_can_observe_mutable_source() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let stager = client(directory.path());
    let binding = output_binding();
    let source = stager
        .prepare_output_path(&binding)
        .await
        .expect("output parent prepares");
    tokio::fs::write(&source, b"map-bytes")
        .await
        .expect("producer output writes");

    let prepared = stager
        .freeze_output(&binding, &build_provenance())
        .await
        .expect("output freezes");
    tokio::fs::write(&source, b"changed-after-completion")
        .await
        .expect("mutable source changes");

    assert_eq!(
        tokio::fs::read(&prepared.path)
            .await
            .expect("frozen bytes read"),
        b"map-bytes"
    );
    assert!(
        tokio::fs::metadata(&prepared.path)
            .await
            .expect("frozen metadata reads")
            .permissions()
            .readonly()
    );
    assert_eq!(
        prepared.manifest.source_execution_id(),
        Some("build-execution-a")
    );
    assert_eq!(
        prepared
            .manifest
            .source_task_ref()
            .map(|task| task.task_id().as_str()),
        Some("build-map")
    );
    assert_eq!(prepared.manifest.artifact().byte_size(), 9);
    assert_eq!(prepared.manifest.anchor_id().as_str(), "anchor-lab");
}

/// An idempotent Published retry still requires its durable frozen bytes to exist locally.
#[tokio::test]
async fn published_retry_rejects_missing_frozen_copy() {
    use tokio::io::AsyncWriteExt;

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("catalog listener binds");
    let endpoint = format!(
        "http://{}",
        listener.local_addr().expect("catalog address reads")
    );
    let directory = tempfile::tempdir().expect("temporary directory");
    let stager = client_at(directory.path(), &endpoint);
    let binding = output_binding();
    let source = stager
        .prepare_output_path(&binding)
        .await
        .expect("output parent prepares");
    tokio::fs::write(&source, b"map-bytes")
        .await
        .expect("producer output writes");
    let prepared = stager
        .freeze_output(&binding, &build_provenance())
        .await
        .expect("output freezes");
    let response_body = serde_json::to_vec(&serde_json::json!({
        "status": "published",
        "manifest": prepared.manifest,
    }))
    .expect("published response serializes");
    let catalog = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("catalog request accepts");
        let response = format!(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
            response_body.len()
        );
        socket
            .write_all(response.as_bytes())
            .await
            .expect("response headers write");
        socket
            .write_all(&response_body)
            .await
            .expect("response body writes");
    });
    tokio::fs::remove_file(&prepared.path)
        .await
        .expect("frozen copy is removed");

    assert!(matches!(
        stager.publish_prepared(&binding, &prepared).await,
        Err(ArtifactError::ManifestMismatch(_))
    ));
    catalog.abort();
}

/// Manifest envelopes retain typed immutable fields and the catalog lifecycle separately.
#[test]
fn parses_revision_status_without_losing_manifest_fields() {
    let digest = "a".repeat(64);
    let envelope = ArtifactManifestEnvelope {
        manifest: manifest(&digest),
        status: Some(MapRevisionStatus::Published),
    };
    assert!(envelope.is_published());
    assert_eq!(
        envelope.normalized_digest().expect("digest parses"),
        format!("sha256:{digest}")
    );
}

/// A restart retry may change observation time but must retain all durable publication IDs.
#[test]
fn publication_attempt_identity_ignores_only_creation_time() {
    let digest = "a".repeat(64);
    let existing = manifest_at(&digest, 1);
    let retried = manifest_at(&digest, 2);
    assert!(is_same_publication_attempt(&existing, &retried));

    let conflicting = manifest_at(&"b".repeat(64), 2);
    assert!(!is_same_publication_attempt(&existing, &conflicting));
}

/// Content-addressed cache paths omit the public digest algorithm prefix.
#[test]
fn blob_path_strips_digest_prefix() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let stager = client(directory.path());
    let raw = "b".repeat(64);
    let digest = format!("sha256:{raw}");
    let path = stager.blob_path(&digest).expect("digest path");
    assert_eq!(
        path.file_name().and_then(|name| name.to_str()),
        Some(raw.as_str())
    );
    assert_eq!(
        path.parent()
            .and_then(Path::file_name)
            .and_then(|name| name.to_str()),
        Some("bb")
    );
}
