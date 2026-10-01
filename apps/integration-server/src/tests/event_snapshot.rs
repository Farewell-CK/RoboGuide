//! Actual HTTP event pages observe committed batches and preserve framed failures.

use super::*;
use domain::{CorrelationId, EventPayload, NodeId, TimestampMs};
use ports::EventSink;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Appends an identifiable heartbeat through the real durable event sink.
fn append_heartbeat(log: &state::SqliteEventLog) {
    let mut sink = log.clone();
    sink.append(
        TimestampMs::new(7),
        &CorrelationId::new("event-read-test").expect("correlation identity"),
        None,
        EventPayload::NodeHeartbeatAccepted {
            node_id: NodeId::new("event-node").expect("node identity"),
            lease_id: domain::LeaseId::new("event-lease").expect("lease identity"),
        },
    );
    assert!(log.take_error().expect("write status").is_none());
}

/// Exercises the production HTTP handler on loopback without any Node or simulator.
async fn request_events(
    event_log: state::SqliteEventLog,
    gate: Arc<Mutex<()>>,
    target: &str,
    accepted: Option<tokio::sync::oneshot::Sender<()>>,
) -> String {
    let controller = Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            event_log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_source_digest: None,
    }));
    let clock = runtime::SystemMonotonicClock::new();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listener binds");
    let address = listener.local_addr().expect("listener address");
    let server = async {
        let (mut stream, _) = listener.accept().await.expect("request connects");
        if let Some(accepted) = accepted {
            accepted.send(()).expect("test observes accepted request");
        }
        handle_http_connection(
            &mut stream,
            &controller,
            &event_log,
            &gate,
            &clock,
            None,
            None,
        )
        .await
        .expect("event reads always return a framed HTTP response");
    };
    let client = async {
        let mut stream = tokio::net::TcpStream::connect(address)
            .await
            .expect("client connects");
        let request = format!("GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n");
        stream
            .write_all(request.as_bytes())
            .await
            .expect("request writes");
        let mut response = String::new();
        stream
            .read_to_string(&mut response)
            .await
            .expect("response reads");
        response
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        let (_, response) = tokio::join!(server, client);
        response
    })
    .await
    .expect("bounded HTTP request finishes")
}

/// Checks real HTTP framing and decodes its exact JSON body.
fn response_body(response: &str, status: &str) -> serde_json::Value {
    assert!(response.starts_with(status), "{response}");
    let (headers, body) = response.split_once("\r\n\r\n").expect("HTTP body");
    let length = headers
        .lines()
        .find_map(|line| line.strip_prefix("Content-Length: "))
        .expect("framed content length")
        .parse::<usize>()
        .expect("numeric content length");
    assert_eq!(length, body.len());
    serde_json::from_str(body).expect("JSON response")
}

/// Holds an uncommitted write until the test releases it, then commits or rolls back.
async fn check_open_batch_read(commit: bool) {
    let directory = tempfile::tempdir().expect("temporary directory");
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .expect("event log opens");
    append_heartbeat(&log);
    let gate = Arc::new(Mutex::new(()));
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let writer_log = log.clone();
    let writer_gate = gate.clone();
    let writer = std::thread::spawn(move || {
        let _guard = writer_gate.lock().expect("write gate");
        writer_log.begin_batch().expect("begin write batch");
        append_heartbeat(&writer_log);
        ready_tx.send(()).expect("batch is ready");
        release_rx
            .recv_timeout(Duration::from_secs(5))
            .expect("test releases writer");
        if commit {
            writer_log.commit_batch().expect("batch commits");
        } else {
            writer_log.rollback_batch().expect("batch rolls back");
        }
    });
    ready_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("writer holds gate and open batch");
    let (accepted_tx, accepted_rx) = tokio::sync::oneshot::channel();
    let mut request = tokio::spawn(request_events(
        log.clone(),
        gate,
        "/v1/events?after=0&limit=100",
        Some(accepted_tx),
    ));
    accepted_rx.await.expect("real HTTP request has connected");
    // A read of the open connection used to fail immediately and drop the response.
    assert!(
        tokio::time::timeout(Duration::from_millis(50), &mut request)
            .await
            .is_err(),
        "read must wait while the application batch is open"
    );
    release_tx.send(()).expect("release writer");
    writer.join().expect("writer completes");
    let response = request.await.expect("HTTP task finishes");
    let document = response_body(&response, "HTTP/1.1 200 OK");
    let rows = document["events"].as_array().expect("event array");
    assert_eq!(rows.len(), if commit { 2 } else { 1 });
    assert_eq!(rows[0]["sequence"], 1);
    if commit {
        assert_eq!(rows[1]["sequence"], 2);
    }
    assert_eq!(log.len().expect("durable rows"), rows.len());
}

/// A page waits for an application's commit and then includes its durable rows.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn event_page_waits_for_commit() {
    check_open_batch_read(true).await;
}

/// A page waits for rollback and never exposes rolled-back evidence.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn event_page_waits_for_rollback() {
    check_open_batch_read(false).await;
}

/// A writer that violates the application gate produces a framed error, never dirty evidence.
#[tokio::test]
async fn event_page_open_batch_without_gate_returns_503() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .expect("event log opens");
    log.begin_batch().expect("deliberately ungated batch");
    append_heartbeat(&log);
    let response = request_events(log.clone(), Arc::new(Mutex::new(())), "/v1/events", None).await;
    let document = response_body(&response, "HTTP/1.1 503 Service Unavailable");
    assert!(
        document["error"]
            .as_str()
            .expect("diagnostic")
            .contains("event batch is still open")
    );
    assert!(document.get("events").is_none());
    log.rollback_batch().expect("rollback bad fixture");
    assert_eq!(log.len().expect("no leaked events"), 0);
}

/// A poisoned application gate is reported as a framed 503 with no false empty page.
#[tokio::test]
async fn event_page_poisoned_gate_returns_503() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .expect("event log opens");
    let gate = Arc::new(Mutex::new(()));
    let poisoned = gate.clone();
    assert!(
        std::thread::spawn(move || {
            let _guard = poisoned.lock().expect("write gate");
            panic!("deliberately poison test gate");
        })
        .join()
        .is_err()
    );
    let response = request_events(log, gate, "/v1/events", None).await;
    let document = response_body(&response, "HTTP/1.1 503 Service Unavailable");
    assert_eq!(document["error"], "event-log write gate is poisoned");
    assert!(document.get("events").is_none());
}

/// HTTP paging preserves sequence gaps, cursors and the existing upper page bound.
#[tokio::test]
async fn event_page_preserves_bounded_cursor_contract() {
    let directory = tempfile::tempdir().expect("temporary directory");
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).expect("event log opens");
    for _ in 0..1002 {
        append_heartbeat(&log);
    }
    // Simulate a historical legal gap without synthesizing an HTTP event.
    rusqlite::Connection::open(&path)
        .expect("fixture connection")
        .execute("DELETE FROM events WHERE sequence = 2", [])
        .expect("legal sequence gap");
    let gate = Arc::new(Mutex::new(()));
    let first = request_events(
        log.clone(),
        gate.clone(),
        "/v1/events?after=0&limit=2",
        None,
    )
    .await;
    let document = response_body(&first, "HTTP/1.1 200 OK");
    assert_eq!(document["events"][0]["sequence"], 1);
    assert_eq!(document["events"][1]["sequence"], 3);
    let next = request_events(
        log.clone(),
        gate.clone(),
        "/v1/events?after=3&limit=2",
        None,
    )
    .await;
    let document = response_body(&next, "HTTP/1.1 200 OK");
    assert_eq!(document["events"][0]["sequence"], 4);
    assert_eq!(document["events"][1]["sequence"], 5);
    let bounded = request_events(log.clone(), gate.clone(), "/v1/events?limit=100000", None).await;
    let document = response_body(&bounded, "HTTP/1.1 200 OK");
    assert_eq!(
        document["events"].as_array().expect("bounded page").len(),
        1000
    );
    let tail = request_events(log, gate, "/v1/events?after=1002&limit=100", None).await;
    let document = response_body(&tail, "HTTP/1.1 200 OK");
    assert_eq!(document, serde_json::json!({"events": []}));
}
