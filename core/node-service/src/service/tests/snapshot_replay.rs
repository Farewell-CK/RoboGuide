//! Live dispatch replay must not manufacture physical ambiguity or execution activation.

use super::*;
use integration::grpc::v0_4::{CanonicalInvocation, ExecutionPhase};

/// Holds one real engine dispatch before its local handle arrives, without wall-clock races.
struct DispatchGate {
    /// Signals that the authorized local call was entered.
    entered: tokio::sync::Notify,
    /// Allows the test to release the local response exactly once.
    release: tokio::sync::Semaphore,
    /// Counts local invocations to detect accidental duplicate side effects.
    calls: AtomicUsize,
}

impl LocalDriver for DispatchGate {
    /// Implements the fixture's configured transport family.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }

    /// Blocks dispatch until released and then reports a real terminal status through polling.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            let CompiledDriverRequest::Http { path, .. } = request else {
                return Err(DriverError::KindMismatch);
            };
            if path == "/dispatch" {
                self.calls.fetch_add(1, Ordering::SeqCst);
                self.entered.notify_one();
                self.release
                    .acquire()
                    .await
                    .expect("gate remains open")
                    .forget();
            }
            GatedDriver::new(Arc::new(AtomicBool::new(true)))
                .invoke(request)
                .await
        })
    }
}

/// Lagged replay and duplicate Execute during live dispatch emit no Unknown or fake activation.
#[tokio::test]
async fn lagged_replay_during_dispatch_preserves_one_local_execution() {
    let directory = tempfile::tempdir().expect("temporary Node directory");
    let gate = Arc::new(DispatchGate {
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Semaphore::new(0),
        calls: AtomicUsize::new(0),
    });
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog("http://127.0.0.1:50051".into(), directory.path().into()),
        vec![gate.clone() as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let node = NodeService::new(engine.clone());
    let mut events = engine.subscribe();
    let invocation = CanonicalInvocation {
        mission_id: "parallel-mission".into(),
        task_id: "task-a".into(),
        group_id: "parallel-group".into(),
        role_id: "worker".into(),
        capability_contract: "mobility.reach_region@v1".into(),
        ..Default::default()
    };
    engine
        .execute("attempt-a".into(), invocation.clone(), vec!["base".into()])
        .expect("one dispatch authorized");
    tokio::time::timeout(std::time::Duration::from_secs(2), gate.entered.notified())
        .await
        .expect("local dispatch enters");
    assert!(engine.snapshots().expect("snapshot reads").is_empty());

    // Force a real receiver overrun deterministically; the same session handler handles Lagged.
    let (sender, mut lagged) = broadcast::channel(1);
    for sequence in 1..=3 {
        sender
            .send(crate::LocalExecutionEvent {
                execution_id: "overrun".into(),
                sequence,
                phase: ExecutionPhase::Started,
                reason: String::new(),
            })
            .expect("receiver exists");
    }
    let event = lagged.recv().await;
    assert!(matches!(event, Err(broadcast::error::RecvError::Lagged(_))));
    let (outbound, mut messages) = mpsc::unbounded_channel();
    node.forward_execution_event(event, "session-a", &outbound)
        .expect("lag replay succeeds");
    assert!(
        messages.try_recv().is_err(),
        "dispatch is not an execution fact"
    );
    node.handle_server_message(
        ServerMessage {
            message: Some(ServerPayload::Execute(integration::grpc::v0_4::Execute {
                session_id: "session-a".into(),
                command_id: "dispatch-attempt-a".into(),
                execution_id: "attempt-a".into(),
                invocation: Some(invocation),
                resource_ids: vec!["base".into()],
                execution_session_json: String::new(),
            })),
        },
        "session-a",
        &outbound,
        &Arc::new(tokio::sync::Mutex::new(0)),
    )
    .await
    .expect("duplicate returns only admission receipt");
    assert!(matches!(messages.try_recv().expect("receipt").message,
        Some(NodePayload::CommandReceipt(receipt))
        if receipt.status == integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted as i32));
    assert!(messages.try_recv().is_err());
    assert_eq!(gate.calls.load(Ordering::SeqCst), 1);

    gate.release.add_permits(1);
    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            let event = events.recv().await.expect("local fact arrives");
            assert_ne!(event.phase, ExecutionPhase::Unknown);
            let terminal = event.phase == ExecutionPhase::Completed;
            node.forward_execution_event(Ok(event), "session-a", &outbound)
                .expect("real fact forwards");
            if terminal {
                break;
            }
        }
    })
    .await
    .expect("original attempt completes");
    let snapshots = engine.snapshots().expect("terminal snapshot reads");
    assert_eq!(snapshots.len(), 1);
    assert_eq!(snapshots[0].phase, ExecutionPhase::Completed as i32);
    assert_eq!(gate.calls.load(Ordering::SeqCst), 1);
    node.replay_snapshots("reconnected", &outbound)
        .expect("terminal replay succeeds");
    let mut completed = false;
    while let Ok(message) = messages.try_recv() {
        if let Some(NodePayload::ExecutionSnapshot(snapshot)) = message.message {
            assert_eq!(snapshot.session_id, "reconnected");
            assert_eq!(snapshot.phase, ExecutionPhase::Completed as i32);
            completed = true;
        }
    }
    assert!(completed);
}
