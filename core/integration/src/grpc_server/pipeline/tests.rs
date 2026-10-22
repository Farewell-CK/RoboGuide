//! Synthetic protocol dispositions only; no deployment registration or device evidence is created.

use super::*;
use ports::DiagnosticKind;

/// Owned channels for one explicitly synthetic application-session contract test.
struct TestSession {
    /// Validated messages supplied directly to the transport/application boundary.
    input: mpsc::Sender<Result<ReceivedNodeMessage, Status>>,
    /// Original in-order facts received by the test-only application consumer.
    events: mpsc::UnboundedReceiver<GrpcNodeEventDelivery>,
    /// ACKs whose only meaning is explicit application acceptance.
    output: mpsc::UnboundedReceiver<Result<ServerMessage, Status>>,
    /// Actual pipeline future, independently of the test's application dispositions.
    task: tokio::task::JoinHandle<Result<(), Status>>,
}

impl TestSession {
    /// Runs an inventory-free protocol fixture with the production bounded session implementation.
    fn start(timeout: std::time::Duration) -> Self {
        let (input, mut receiver) = mpsc::channel(VALIDATED_INGRESS_CAPACITY);
        let (events, event_receiver) = mpsc::unbounded_channel();
        let (outbound, output) = mpsc::unbounded_channel();
        let task = tokio::spawn(async move {
            let observation = GrpcSessionObservation::new(None, DiagnosticHandle::default());
            ApplicationSession {
                events: &events,
                node_id: "protocol-fixture",
                session_id: "original-session",
                acceptance_timeout: timeout,
                observation: &observation,
                outbound: &outbound,
            }
            .run(&mut receiver)
            .await
        });
        Self {
            input,
            events: event_receiver,
            output,
            task,
        }
    }

    /// Supplies a heartbeat with an original receive time that must not become dequeue time.
    async fn send(&self, sequence: u64) {
        self.input
            .send(Ok(ReceivedNodeMessage {
                message: NodeMessage {
                    message: Some(NodePayload::Heartbeat(crate::grpc::v0_4::Heartbeat {
                        sequence,
                        ..Default::default()
                    })),
                },
                metadata: Some(NodeFactMetadata {
                    received_at: domain::TimestampMs::new(17 + sequence),
                    received_instant: std::time::Instant::now(),
                    kind: DiagnosticKind::Heartbeat,
                    wire_bytes: 3,
                    identity: None,
                }),
                queue_ticket: None,
            }))
            .await
            .unwrap();
    }

    /// Checks exact original queue order and metadata before exposing its completion authority.
    async fn completion(&mut self, expected: u64) -> GrpcNodeEventCompletion {
        let delivery = self.events.recv().await.unwrap();
        let (event, metadata, completion) = delivery.into_observed_parts();
        let GrpcNodeEvent::NodeMessage {
            node_id,
            session_id,
            message,
        } = event
        else {
            panic!("original NodeMessage required");
        };
        assert_eq!(node_id, "protocol-fixture");
        assert_eq!(session_id, "original-session");
        let Some(NodePayload::Heartbeat(heartbeat)) = message.message else {
            panic!("original heartbeat required");
        };
        assert_eq!(heartbeat.sequence, expected);
        assert_eq!(metadata.unwrap().received_at.as_millis(), 17 + expected);
        completion
    }

    /// Reads the next original sequence acknowledged after actual application acceptance.
    async fn ack(&mut self, expected: u64) {
        let Some(ServerPayload::Ack(ack)) = self.output.recv().await.unwrap().unwrap().message
        else {
            panic!("original application ACK required");
        };
        assert_eq!(ack.sequence, expected);
    }
}

/// Full original window reaches the existing batch consumer, without early or reordered ACKs.
#[tokio::test]
async fn original_order_window_and_commit_before_ack() {
    let mut session = TestSession::start(std::time::Duration::from_secs(5));
    for sequence in 1..=10 {
        session.send(sequence).await;
    }
    let mut completions = VecDeque::new();
    for sequence in 1..=8 {
        completions.push_back(session.completion(sequence).await);
    }
    assert!(session.events.try_recv().is_err());
    assert!(session.output.try_recv().is_err());
    let first = completions.pop_front().unwrap();
    completions.pop_front().unwrap().accept();
    tokio::task::yield_now().await;
    assert!(session.output.try_recv().is_err());
    assert!(session.events.try_recv().is_err());
    first.accept();
    session.ack(1).await;
    session.ack(2).await;
    completions.push_back(session.completion(9).await);
    completions.push_back(session.completion(10).await);
    for completion in completions {
        completion.accept();
    }
    for sequence in 3..=10 {
        session.ack(sequence).await;
    }
    drop(session.input);
    session.task.await.unwrap().unwrap();
}

/// Later accepted facts cannot hide rejection, write unavailability or a lost original response.
#[tokio::test]
async fn failed_original_prefix_never_acknowledges_later_completion() {
    for code in [
        tonic::Code::FailedPrecondition,
        tonic::Code::Unavailable,
        tonic::Code::Cancelled,
    ] {
        let mut session = TestSession::start(std::time::Duration::from_secs(5));
        session.send(1).await;
        session.send(2).await;
        let first = session.completion(1).await;
        session.completion(2).await.accept();
        match code {
            tonic::Code::FailedPrecondition => first.reject("original rejected fact"),
            tonic::Code::Unavailable => first.unavailable("original write unavailable"),
            _ => drop(first),
        }
        assert_eq!(
            session.task.await.unwrap().unwrap_err().code(),
            if code == tonic::Code::Cancelled {
                tonic::Code::Unavailable
            } else {
                code
            }
        );
        assert!(session.output.try_recv().is_err());
    }
}

/// Poll cancellation while more input arrives cannot extend the first enqueue's fixed deadline.
#[tokio::test(start_paused = true)]
async fn additional_input_never_renews_original_completion_budget() {
    let mut session = TestSession::start(std::time::Duration::from_millis(10));
    session.send(1).await;
    let first = session.completion(1).await;
    tokio::time::advance(std::time::Duration::from_millis(6)).await;
    session.send(2).await;
    let second = session.completion(2).await;
    assert!(!session.task.is_finished());
    tokio::time::advance(std::time::Duration::from_millis(4)).await;
    assert_eq!(
        session.task.await.unwrap().unwrap_err().code(),
        tonic::Code::DeadlineExceeded
    );
    first.accept();
    second.accept();
    assert!(session.output.try_recv().is_err());
}

/// Reader termination drains only actual accepted prefix facts and preserves its original error.
#[tokio::test]
async fn reader_end_drains_ordered_committed_prefix() {
    for failed in [false, true] {
        let mut session = TestSession::start(std::time::Duration::from_secs(5));
        session.send(1).await;
        session.send(2).await;
        let first = session.completion(1).await;
        let second = session.completion(2).await;
        if failed {
            session
                .input
                .send(Err(Status::resource_exhausted("original reader bound")))
                .await
                .unwrap();
        }
        drop(session.input);
        second.accept();
        first.accept();
        let result = session.task.await.unwrap();
        for sequence in 1..=2 {
            let Some(ServerPayload::Ack(ack)) =
                session.output.recv().await.unwrap().unwrap().message
            else {
                panic!("original committed prefix ACK required");
            };
            assert_eq!(ack.sequence, sequence);
        }
        if failed {
            assert_eq!(result.unwrap_err().code(), tonic::Code::ResourceExhausted);
        } else {
            result.unwrap();
        }
        assert!(session.output.try_recv().is_err());
    }
}
