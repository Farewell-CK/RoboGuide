//! Session receive metadata and optional, bounded queue observations; never application authority.

use super::*;
use ports::{
    Clock, DiagnosticHandle, DiagnosticIdentity, DiagnosticKind, DiagnosticQueue,
    DiagnosticQueueTicket, DiagnosticStage, NodeFactMetadata,
};
use prost::Message as _;

impl ports::NodeLivenessReader for GrpcNodeRouter {
    /// Reads only current active session summaries; expired routes and removed sessions provide no renewal evidence.
    fn node_liveness_evidence(&self) -> Result<Vec<domain::NodeSessionLivenessEvidence>, String> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| "session registry unavailable".to_string())?;
        Ok(sessions
            .values()
            .filter(|route| route.active && route.last_heartbeat.elapsed() < route.lease_duration)
            .filter_map(|route| route.liveness_evidence.clone())
            .collect())
    }
}

/// Per-session immutable timing and observer dependencies selected at application startup.
#[derive(Clone)]
pub(super) struct GrpcSessionSettings {
    /// Original TLS certificate validity, independent of heartbeat or observation clocks.
    pub(super) certificate_expires_at_ms: Option<u64>,
    /// Startup-frozen lease and completion timing.
    pub(super) policy: NodeProtocolPolicy,
    /// Receive-time metadata and best-effort diagnostics.
    pub(super) observation: GrpcSessionObservation,
    /// Startup-frozen peer identity policy.
    pub(super) identity_policy: NodeIdentityPolicy,
    /// Leaf certificate digest captured before the streaming body is consumed.
    pub(super) certificate_digest: Option<String>,
}

/// Shared receive-time and observation dependencies; compatibility constructors remain disabled.
#[derive(Clone)]
pub(super) struct GrpcSessionObservation {
    /// Application's exact process Clock, never a remote Node clock.
    pub(super) clock: Option<Arc<dyn Clock + Send + Sync>>,
    /// Optional best-effort observer.
    pub(super) diagnostics: DiagnosticHandle,
    /// Shared application queue totals across sessions.
    pub(super) application_queue: DiagnosticQueue,
}

impl GrpcSessionObservation {
    /// Installs receive metadata and queue probes without changing delivery or authority decisions.
    pub(super) fn new(
        clock: Option<Arc<dyn Clock + Send + Sync>>,
        diagnostics: DiagnosticHandle,
    ) -> Self {
        let application_queue =
            DiagnosticQueue::new(diagnostics.clone(), DiagnosticStage::ApplicationQueue);
        Self {
            clock,
            diagnostics,
            application_queue,
        }
    }

    /// Captures receive time before any queue wait; absent only on compatibility/test constructors.
    pub(super) fn received(&self) -> Option<(domain::TimestampMs, std::time::Instant)> {
        self.clock
            .as_ref()
            .map(|clock| (clock.now(), std::time::Instant::now()))
    }

    /// Builds exact diagnostic identity and size without copying any wire payload bytes.
    pub(super) fn metadata(
        &self,
        received: Option<(domain::TimestampMs, std::time::Instant)>,
        node_id: &str,
        session_id: &str,
        kind: DiagnosticKind,
        wire_bytes: usize,
    ) -> Option<NodeFactMetadata> {
        received.map(|(received_at, received_instant)| NodeFactMetadata {
            received_at,
            received_instant,
            kind,
            wire_bytes,
            identity: DiagnosticIdentity::bounded(node_id, session_id),
        })
    }
}

/// Message plus receive metadata and cancellation-safe queue ticket.
pub(super) struct ReceivedNodeMessage {
    /// Original validated message, never interpreted by diagnostics.
    pub(super) message: NodeMessage,
    /// Original ingress receive metadata.
    pub(super) metadata: Option<NodeFactMetadata>,
    /// Reader-to-session queue accounting.
    pub(super) queue_ticket: Option<DiagnosticQueueTicket>,
}

/// Classifies protocol shape for observation without interpreting objective, outcome or retry policy.
pub(super) fn diagnostic_kind(message: &NodeMessage) -> DiagnosticKind {
    match &message.message {
        Some(NodePayload::Heartbeat(_)) => DiagnosticKind::Heartbeat,
        Some(NodePayload::RegistrationUpdate(_)) => DiagnosticKind::RegistrationUpdate,
        Some(NodePayload::StateObservationBatch(_)) => DiagnosticKind::State,
        Some(NodePayload::PeerChannelReadiness(_)) => DiagnosticKind::PeerReadiness,
        Some(NodePayload::CommandReceipt(_)) => DiagnosticKind::Receipt,
        Some(NodePayload::ExecutionEvent(_) | NodePayload::ExecutionSnapshot(_)) => {
            DiagnosticKind::Execution
        }
        _ => DiagnosticKind::Other,
    }
}

/// Measures encoded protobuf size without retaining or serializing payload content.
pub(super) fn wire_size(message: &NodeMessage) -> usize {
    message.encoded_len()
}

/// Emits route loss with receive attribution; it still has no remote acknowledgement.
pub(super) fn emit_unavailable_observed(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    node_id: String,
    session_id: String,
    observation: &GrpcSessionObservation,
) {
    let metadata = observation.metadata(
        observation.received(),
        &node_id,
        &session_id,
        DiagnosticKind::Unavailable,
        0,
    );
    let mut delivery = GrpcNodeEventDelivery::observation(GrpcNodeEvent::Unavailable {
        node_id,
        session_id,
    });
    delivery.queue_ticket = metadata
        .as_ref()
        .and_then(|metadata| observation.application_queue.enqueue(metadata));
    delivery.metadata = metadata;
    let _ = events.send(delivery);
}

/// Waits for durable application acceptance while retaining original receive metadata for observation.
pub(super) async fn deliver_received_fact(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    event: GrpcNodeEvent,
    acceptance_timeout: std::time::Duration,
    metadata: Option<NodeFactMetadata>,
    observation: &GrpcSessionObservation,
) -> Result<(), Status> {
    let (response, decision) = oneshot::channel();
    let mut delivery = GrpcNodeEventDelivery::requiring_acceptance(event, response);
    delivery.queue_ticket = metadata
        .as_ref()
        .and_then(|metadata| observation.application_queue.enqueue(metadata));
    delivery.metadata = metadata.clone();
    events
        .send(delivery)
        .map_err(|_| Status::unavailable("Controller fact consumer is closed"))?;
    let started = std::time::Instant::now();
    let result = tokio::time::timeout(acceptance_timeout, decision).await;
    if let Some(metadata) = &metadata {
        observation.diagnostics.timing(
            metadata.kind,
            DiagnosticStage::Completion,
            started.elapsed(),
            metadata.identity.as_ref(),
        );
    }
    let decision = result
        .map_err(|_| Status::deadline_exceeded("Controller fact acceptance timed out"))?
        .map_err(|_| Status::unavailable("Controller fact response was dropped"))?;
    decision.map_err(|failure| match failure {
        ApplicationAcceptanceFailure::Rejected(reason) => {
            Status::failed_precondition(format!("Controller rejected fact: {reason}"))
        }
        ApplicationAcceptanceFailure::Unavailable(reason) => {
            Status::unavailable(format!("Controller fact acceptance unavailable: {reason}"))
        }
    })
}
