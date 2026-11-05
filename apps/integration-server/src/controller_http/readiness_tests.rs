//! Readiness must observe a committed authority turn and retain real storage failure gates.

use super::*;

/// Calls the production HTTP handler with the same writer used by the actual SQL batch.
async fn probe(
    controller: Arc<Mutex<ControllerState>>,
    log: state::SqliteEventLog,
    gate: Arc<application::ApplicationWriter>,
) -> String {
    let (mut client, mut server) = tokio::io::duplex(128 * 1024);
    let clock = runtime::SystemMonotonicClock::new();
    let principal = OperatorPrincipal {
        tenants: Some(BTreeSet::from(["*".into()])),
        subject: Some("readiness-monitor".into()),
        role: Some("metrics-reader".into()),
        ..Default::default()
    };
    let serve = async {
        handle_http_connection_with_principal(
            &mut server,
            &controller,
            &log,
            &gate,
            &clock,
            None,
            None,
            &principal,
        )
        .await
        .unwrap();
        drop(server);
    };
    let call = async {
        client
            .write_all(&request("GET", "/readyz", b"", None))
            .await
            .unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).await.unwrap();
        response
    };
    tokio::join!(serve, call).1
}

/// A real in-flight commit must finish before readiness checks the original store.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn readiness_waits_for_original_application_batch_without_false_503() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let snapshot = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    let gate = Arc::new(application::ApplicationWriter::default());
    let (entered_tx, entered_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let writer_gate = gate.clone();
    let writer_log = log.clone();
    let writer = std::thread::spawn(move || {
        let _turn = writer_gate.lock().unwrap();
        writer_log.begin_batch().unwrap();
        save_controller_checkpoint(&writer_log, &snapshot).unwrap();
        entered_tx.send(()).unwrap();
        release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        writer_log.commit_batch().unwrap();
        writer_log.control_command_count().unwrap()
    });
    entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    let probe = tokio::spawn(probe(controller, log.clone(), gate.clone()));
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    while gate.queued_turns_for_test() == 0 && !probe.is_finished() {
        assert!(std::time::Instant::now() < deadline);
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    release_tx.send(()).unwrap();
    let committed_commands = writer.join().unwrap();
    let response = probe.await.unwrap();
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    assert!(response.contains("\"ready\":true"));
    assert!(log.events().unwrap().is_empty());
    assert_eq!(log.control_command_count().unwrap(), committed_commands);
}

/// Waiting for an ordinary writer does not clear the original durable failure fence.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn readiness_still_refuses_actual_disabled_write_authority() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .unwrap()
        .with_write_failure_fence("readiness-test-process")
        .unwrap();
    let controller = controller(&log);
    log.disable_writes("actual retained authority failure", false)
        .unwrap();
    let before = log.write_status().unwrap();
    let response = probe(
        controller,
        log.clone(),
        Arc::new(application::ApplicationWriter::default()),
    )
    .await;
    assert!(response.starts_with("HTTP/1.1 503"), "{response}");
    assert_eq!(log.write_status().unwrap(), before);
    assert!(log.events().unwrap().is_empty());
    assert_eq!(log.control_command_count().unwrap(), 0);
}
