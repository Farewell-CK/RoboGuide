//! Actual single-network-worker and held-writer checks without Node or device facts.

use super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Creates empty real SQL-backed application authorities, without registering any Node.
fn controller(events: &state::SqliteEventLog) -> Arc<Mutex<ControllerState>> {
    Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            events.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_handoffs: BTreeMap::new(),
        runtime_revisions: Default::default(),
        safety_stops: BTreeMap::new(),
        verifier_seen: Default::default(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }))
}

/// Complete empty-authority reductions keep their exact checkpoint without new idle journal commands.
#[test]
fn unchanged_timer_reduces_without_replicating_identical_empty_authority() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("original.sqlite3")).unwrap();
    let controller = controller(&events);
    let gate = Arc::new(Mutex::new(()));
    application::drive_application_timer(
        &controller,
        &events,
        &gate,
        domain::TimestampMs::new(17),
        None,
    )
    .unwrap();
    let checkpoint = events.load_checkpoint().unwrap().unwrap();
    let original = events.control_command_journal().unwrap();
    for now in [1000, 2000, 3000, 4000, 5000] {
        application::drive_application_timer(
            &controller,
            &events,
            &gate,
            domain::TimestampMs::new(now),
            None,
        )
        .unwrap();
        assert_eq!(events.control_command_journal().unwrap(), original);
        assert_eq!(events.load_checkpoint().unwrap().unwrap(), checkpoint);
    }
}

/// A blocked production timer cannot occupy the sole network worker or commit before the real gate.
#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn original_timer_waits_for_real_writer_while_single_network_worker_serves_tcp() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("original.sqlite3")).unwrap();
    let controller = controller(&events);
    let gate = Arc::new(Mutex::new(()));
    let (held_sender, held_receiver) = tokio::sync::oneshot::channel();
    let (observed_sender, observed_receiver) = std::sync::mpsc::sync_channel(1);
    let original_gate = gate.clone();
    let owner = std::thread::spawn(move || {
        let _guard = original_gate.lock().unwrap();
        held_sender.send(()).unwrap();
        observed_receiver
            .recv_timeout(Duration::from_secs(5))
            .is_ok()
    });
    held_receiver.await.unwrap();
    let started = Arc::new(AtomicBool::new(false));
    let tick_started = started.clone();
    let tick_events = events.clone();
    let tick = tokio::spawn(async move {
        tick_started.store(true, Ordering::SeqCst);
        application::drive_application_timer_with_clock(
            &controller,
            &tick_events,
            &gate,
            &runtime::FixedClock::new(domain::TimestampMs::new(17)),
            None,
        )
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let serving = tokio::spawn(async move {
        while !started.load(Ordering::SeqCst) {
            tokio::task::yield_now().await;
        }
        let (mut stream, _) = listener.accept().await.unwrap();
        stream
            .write_all(b"actual-network-worker-ready")
            .await
            .unwrap();
    });
    let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
    let mut bytes = Vec::new();
    stream.read_to_end(&mut bytes).await.unwrap();
    assert_eq!(bytes, b"actual-network-worker-ready");
    assert!(
        events.load_checkpoint().unwrap().is_none(),
        "blocked timer committed before original writer release"
    );
    observed_sender.send(()).unwrap();
    assert!(
        owner.join().unwrap(),
        "network worker was unavailable until the original writer timed out"
    );
    tick.await.unwrap().unwrap();
    serving.await.unwrap();
    let checkpoint = events.load_checkpoint().unwrap().unwrap();
    assert_eq!(checkpoint.event_sequence, 0);
    assert_eq!(checkpoint.schema, SERVER_CHECKPOINT_SCHEMA);
}

/// Marks the actual HTTP authorization phase without adding a clock or application side effect.
struct ObservedHttpClock(Arc<AtomicBool>);

impl Clock for ObservedHttpClock {
    /// Retains a fixed source time while exposing that the production handler reached authorization.
    fn now(&self) -> domain::TimestampMs {
        self.0.store(true, Ordering::SeqCst);
        domain::TimestampMs::new(7)
    }
}

/// Original HTTP lock contention must not occupy the sole network worker or invent durable facts.
#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn original_operator_waits_for_controller_while_single_worker_serves_independent_tcp() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("original.sqlite3")).unwrap();
    let controller = controller(&events);
    let original_controller = controller.clone();
    let (held_sender, held_receiver) = tokio::sync::oneshot::channel();
    let (observed_sender, observed_receiver) = std::sync::mpsc::sync_channel(1);
    let owner = std::thread::spawn(move || {
        let _original_guard = original_controller.lock().unwrap();
        held_sender.send(()).unwrap();
        observed_receiver
            .recv_timeout(Duration::from_secs(5))
            .is_ok()
    });
    held_receiver.await.unwrap();
    let started = Arc::new(AtomicBool::new(false));
    let http_clock = ObservedHttpClock(started.clone());
    let http_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let http_address = http_listener.local_addr().unwrap();
    let http_events = events.clone();
    let http = tokio::spawn(async move {
        let (mut stream, _) = http_listener.accept().await.unwrap();
        controller_http::handle_http_connection(
            &mut stream,
            &controller,
            &http_events,
            &Arc::new(Mutex::new(())),
            &http_clock,
            None,
            None,
        )
        .await
        .unwrap();
    });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let serving = tokio::spawn(async move {
        while !started.load(Ordering::SeqCst) {
            tokio::task::yield_now().await;
        }
        let (mut stream, _) = listener.accept().await.unwrap();
        stream
            .write_all(b"independent-original-network")
            .await
            .unwrap();
    });
    let mut http_client = tokio::net::TcpStream::connect(http_address).await.unwrap();
    http_client
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .await
        .unwrap();
    let mut probe = tokio::net::TcpStream::connect(address).await.unwrap();
    let mut bytes = Vec::new();
    probe.read_to_end(&mut bytes).await.unwrap();
    assert_eq!(bytes, b"independent-original-network");
    let _ = observed_sender.send(());
    assert!(
        owner.join().unwrap(),
        "HTTP held the sole worker until the original Controller mutex timed out"
    );
    let mut response = String::new();
    http_client.read_to_string(&mut response).await.unwrap();
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    http.await.unwrap();
    serving.await.unwrap();
    assert!(events.load_checkpoint().unwrap().is_none());
    assert!(events.events().unwrap().is_empty());
    assert_eq!(events.control_command_count().unwrap(), 0);
}
