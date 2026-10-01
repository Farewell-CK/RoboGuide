//! Actual HTTP admission receipts and checkpoint replay without robots or external services.

use super::*;
use sha2::{Digest, Sha256};

/// Sends one framed request through the production HTTP handler on a loopback socket.
async fn request(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    method: &str,
    path: &str,
    body: &[u8],
) -> (String, serde_json::Value) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("binds");
    let address = listener.local_addr().expect("address exists");
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::SystemMonotonicClock::new();
    let server = async {
        let (mut stream, _) = listener.accept().await.expect("connects");
        handle_http_connection(&mut stream, controller, log, &gate, &clock, None, None)
            .await
            .expect("handler completes");
    };
    let client = async {
        let mut stream = tokio::net::TcpStream::connect(address)
            .await
            .expect("connects");
        let header = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n",
            body.len()
        );
        stream
            .write_all(header.as_bytes())
            .await
            .expect("header writes");
        stream.write_all(body).await.expect("body writes");
        let mut response = String::new();
        stream.read_to_string(&mut response).await.expect("reads");
        let (headers, json) = response.split_once("\r\n\r\n").expect("HTTP framing");
        (
            headers.lines().next().expect("status").to_string(),
            serde_json::from_str(json).expect("JSON"),
        )
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(server, client).1
    })
    .await
    .expect("bounded handler")
}

/// A lost response can be reconciled by exact bytes, while rejected replacements change nothing.
#[tokio::test]
async fn admission_http_and_checkpoint_preserve_exact_accepted_input() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("opens");
    let controller = Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_source_digest: None,
    }));
    let body = include_bytes!("../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    let mission = decode_mission_plan(std::str::from_utf8(body).expect("UTF8"))
        .expect("valid plan")
        .goal()
        .mission_id()
        .clone();
    let (status, accepted) = request(&controller, &log, "POST", "/v1/missions", body).await;
    assert!(status.contains("202"), "{status}: {accepted}");
    let path = format!("/v1/missions/{mission}/admission");
    let (status, evidence) = request(&controller, &log, "GET", &path, b"").await;
    assert!(status.contains("200"));
    assert_eq!(
        evidence["accepted_request_body_sha256"],
        format!("sha256:{:x}", Sha256::digest(body))
    );
    assert_eq!(evidence["group_id"], accepted["group_id"]);
    let mut changed: serde_json::Value = serde_json::from_slice(body).expect("plan JSON");
    changed["mission"]["objective"] = serde_json::json!("different objective");
    let changed_body = serde_json::to_vec(&changed).expect("serializes");
    let (status, rejected) =
        request(&controller, &log, "POST", "/v1/missions", &changed_body).await;
    assert!(status.contains("409"), "{status}: {rejected}");
    assert_eq!(
        request(&controller, &log, "GET", &path, b"").await.1,
        evidence
    );
    let checkpoint = log.load_checkpoint().expect("loads").expect("exists");
    let saved: ServerCheckpoint =
        serde_json::from_str(&checkpoint.checkpoint_json).expect("decodes");
    let restored = Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::restore_from_checkpoint(
            &saved.integration_json,
            log.clone(),
            integration::GrpcNodeRouter::default(),
            domain::TimestampMs::new(100),
        )
        .expect("restores"),
        orchestrator: MissionOrchestrator::restore_json(&saved.orchestration_json)
            .expect("restores"),
        mission_admissions: saved.mission_admissions,
        verifier_seen: saved.verifier_seen,
        verifier_source_digest: saved.verifier_source_digest,
    }));
    assert_eq!(
        request(&restored, &log, "GET", &path, b"").await.1,
        evidence
    );
    restored.lock().expect("lock").mission_admissions.clear();
    assert!(
        request(&restored, &log, "GET", &path, b"")
            .await
            .0
            .contains("404")
    );
}
