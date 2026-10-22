//! Bounded original-session application submission with commit-before-ACK and ordered dispositions.

use super::diagnostics::{PendingNodeFactAcceptance, submit_received_fact};
use super::*;
use std::collections::VecDeque;

/// Fixed transport budget, independent of the number of Nodes, Roles, devices or resources.
const APPLICATION_WINDOW: usize = 8;

/// Existing session context; this owns no application, resource or physical authority.
pub(super) struct ApplicationSession<'a> {
    /// Existing application-owned event consumer.
    pub(super) events: &'a mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    /// Original accepted Node identity, never accepted from a body/header at this stage.
    pub(super) node_id: &'a str,
    /// Original actual active route incarnation.
    pub(super) session_id: &'a str,
    /// Existing positive configured application acceptance budget.
    pub(super) acceptance_timeout: std::time::Duration,
    /// Existing identity/time/queue observer.
    pub(super) observation: &'a GrpcSessionObservation,
    /// Original stream; ACK implies actual durable application acceptance only.
    pub(super) outbound: &'a mpsc::UnboundedSender<Result<ServerMessage, Status>>,
}

/// Original ordered ACK information kept beside one already-enqueued application's result.
struct Pending {
    /// Exact original wire sequence or snapshot coverage, never another generated identity.
    sequence: u64,
    /// Original receive metadata for ACK latency observation.
    metadata: Option<NodeFactMetadata>,
    /// Synchronously submitted original fact and its nonrenewing deadline.
    acceptance: PendingNodeFactAcceptance,
}

impl ApplicationSession<'_> {
    /// Submits synchronously in original wire order; future polling cannot reorder input effects.
    fn submit(&self, received: ReceivedNodeMessage) -> Result<Pending, Status> {
        let ReceivedNodeMessage {
            message,
            metadata,
            queue_ticket,
        } = received;
        drop(queue_ticket);
        let sequence = match &message.message {
            Some(NodePayload::Heartbeat(value)) => value.sequence,
            Some(NodePayload::RegistrationUpdate(value)) => value.sequence,
            Some(NodePayload::StateObservationBatch(value)) => value.sequence,
            Some(NodePayload::PeerChannelReadiness(value)) => value.sequence,
            Some(NodePayload::CommandReceipt(value)) => value.sequence,
            Some(NodePayload::MemoryImportReceipt(value)) => value.sequence,
            Some(NodePayload::MemoryImportQueryReceipt(value)) => value.sequence,
            Some(NodePayload::MemoryControlReceipt(value)) => value.sequence,
            Some(NodePayload::MemoryUseReceipt(value)) => value.sequence,
            Some(NodePayload::MemoryUseQueryReceipt(value)) => value.sequence,
            Some(NodePayload::ExecutionEvent(value)) => value.sequence,
            Some(NodePayload::ExecutionSnapshot(value)) => value.last_sequence,
            _ => 0,
        };
        let acceptance = submit_received_fact(
            self.events,
            GrpcNodeEvent::NodeMessage {
                node_id: self.node_id.into(),
                session_id: self.session_id.into(),
                message,
            },
            self.acceptance_timeout,
            metadata.clone(),
            self.observation,
        )?;
        Ok(Pending {
            sequence,
            metadata,
            acceptance,
        })
    }

    /// Sends an original ACK only after all earlier actual application dispositions succeeded.
    fn acknowledge(&self, pending: Pending) {
        if pending.sequence == 0 {
            return;
        }
        let _ = self.outbound.send(Ok(ServerMessage {
            message: Some(ServerPayload::Ack(Ack {
                sequence: pending.sequence,
            })),
        }));
        if let Some(metadata) = pending.metadata {
            self.observation.diagnostics.timing(
                metadata.kind,
                DiagnosticStage::Ack,
                metadata.received_instant.elapsed(),
                metadata.identity.as_ref(),
            );
        }
    }

    /// Polls the bounded validated input and the original first disposition without renewing either.
    pub(super) async fn run(
        &self,
        receiver: &mut mpsc::Receiver<Result<ReceivedNodeMessage, Status>>,
    ) -> Result<(), Status> {
        let mut pending: VecDeque<Pending> = VecDeque::new();
        let mut ending: Option<Result<(), Status>> = None;
        loop {
            if pending.is_empty() {
                if let Some(result) = ending {
                    return result;
                }
                match receiver.recv().await {
                    Some(Ok(received)) => pending.push_back(self.submit(received)?),
                    Some(Err(error)) => return Err(error),
                    None => return Ok(()),
                }
            }
            let can_receive = ending.is_none() && pending.len() < APPLICATION_WINDOW;
            tokio::select! {
                biased;
                result = pending.front_mut().expect("nonempty original prefix").acceptance.wait() => {
                    result?;
                    self.acknowledge(pending.pop_front().expect("original prefix remains"));
                }
                received = receiver.recv(), if can_receive => match received {
                    Some(Ok(received)) => pending.push_back(self.submit(received)?),
                    Some(Err(error)) => ending = Some(Err(error)),
                    None => ending = Some(Ok(())),
                }
            }
        }
    }
}

#[cfg(test)]
mod tests;
