#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{GrpcIntegrationService, IntegrationRuntimeBridge};
use ports::{Clock, EventSink};
use std::sync::{Arc, Mutex};

/// Process-local Runtime/Control event sink pending persistent event storage.
#[derive(Default)]
struct ProcessEventLog {
    /// Immutable domain payloads retained for diagnostics.
    records: Vec<domain::EventPayload>,
}

impl EventSink for ProcessEventLog {
    /// Retains one event without leaking gRPC types into the core evidence model.
    fn append(
        &mut self,
        _timestamp: domain::TimestampMs,
        _correlation_id: &domain::CorrelationId,
        _causation_id: Option<&domain::EventId>,
        payload: domain::EventPayload,
    ) {
        self.records.push(payload);
    }
}

/// Binds the configured integration listener and keeps accepting connector sessions.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let address: std::net::SocketAddr = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:50051".to_string())
        .parse()?;
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let (service, router) = GrpcIntegrationService::new(events);
    let bridge = Arc::new(Mutex::new(IntegrationRuntimeBridge::new(
        control::ControlPlane::new(),
        state::InMemorySharedNodeState::new(),
        ProcessEventLog::default(),
        router,
    )));
    tokio::spawn(async move {
        let correlation = domain::CorrelationId::new("integration-server")
            .expect("static correlation id is valid");
        let clock = runtime::SystemMonotonicClock::new();
        while let Some(event) = receiver.recv().await {
            if let Ok(mut bridge) = bridge.lock()
                && let Err(error) = bridge.consume(event, clock.now(), &correlation)
            {
                eprintln!("integration fact rejected by Runtime/Control: {error}");
            }
        }
    });
    tonic::transport::Server::builder()
        .add_service(RoboGuideNodeProtocolServer::new(service))
        .serve(address)
        .await?;
    Ok(())
}
