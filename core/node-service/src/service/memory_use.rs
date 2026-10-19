//! Current-use command completion over persisted Node intent, with application-committed receipts.

use super::*;

impl NodeService {
    /// Sends immutable independent observation evidence, never an execution outcome.
    pub(super) async fn handle_memory_use_query(
        &self,
        query: domain::MemoryUseQuery,
        session: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        let receipt = self.engine.complete_memory_use_query(&query).await?;
        let receipt_json = serde_json::to_string(&receipt)
            .map_err(|e| NodeServiceError::Protocol(e.to_string()))?;
        let mut number = sequence.lock().await;
        *number = number.saturating_add(1);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::MemoryUseQueryReceipt(
                    integration::grpc::v0_4::MemoryUseQueryReceipt {
                        session_id: session.into(),
                        sequence: *number,
                        receipt_json,
                    },
                )),
            })
            .map_err(|_| NodeServiceError::Closed)
    }

    /// Finishes only once-admitted original work; permission evidence never becomes an execution fact.
    pub(super) async fn handle_memory_use_control(
        &self,
        command: domain::MemoryUseControl,
        session: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        let receipt = self.engine.complete_memory_use_control(&command).await?;
        let receipt_json = serde_json::to_string(&receipt)
            .map_err(|e| NodeServiceError::Protocol(e.to_string()))?;
        let mut number = sequence.lock().await;
        *number = number.saturating_add(1);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::MemoryUseReceipt(
                    integration::grpc::v0_4::MemoryUseReceipt {
                        session_id: session.into(),
                        sequence: *number,
                        receipt_json,
                    },
                )),
            })
            .map_err(|_| NodeServiceError::Closed)
    }
}
