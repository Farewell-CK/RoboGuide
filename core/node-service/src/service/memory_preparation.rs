//! Version-negotiated prepared-context commands and durable management receipts.

use super::*;

impl NodeService {
    /// Processes only the exact current authenticated session and retains SQL failures as ambiguity.
    pub(super) async fn handle_memory_control(
        &self,
        frame: integration::grpc::v0_4::MemoryControl,
        session: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        if frame.session_id != session
            || !integration::grpc::v0_4::supports_memory_preparation(
                self.engine.catalog().node_contract_version(),
            )
            || frame.command_json.len() > 256 * 1024
        {
            return Err(NodeServiceError::Protocol(
                "Memory control was not negotiated or exceeds its exact session/bounds".into(),
            ));
        }
        let command: domain::MemoryControlCommand = serde_json::from_str(&frame.command_json)
            .map_err(|e| NodeServiceError::Protocol(e.to_string()))?;
        command.validate().map_err(NodeServiceError::Protocol)?;
        self.engine
            .admit_controller_epoch(&frame.controller_epoch)?;
        let receipt = self
            .engine
            .process_memory_control(&command, session, frame.valid_for_ms)
            .await?;
        self.send_memory_control_receipt(session, &receipt, outbound, sequence)
            .await
    }

    /// Sends an immutable admitted observation, independently of execution outcome and satisfaction.
    pub(super) async fn send_memory_control_receipt(
        &self,
        session: &str,
        receipt: &domain::MemoryControlReceipt,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        let receipt_json = serde_json::to_string(receipt)
            .map_err(|e| NodeServiceError::Protocol(e.to_string()))?;
        let mut number = sequence.lock().await;
        *number = number.saturating_add(1);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::MemoryControlReceipt(
                    integration::grpc::v0_4::MemoryControlReceipt {
                        session_id: session.into(),
                        sequence: *number,
                        receipt_json,
                    },
                )),
            })
            .map_err(|_| NodeServiceError::Closed)
    }
}
