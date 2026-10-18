//! Negotiated readonly original-provider queries with durable identity and once-only budgets.

use super::*;
use integration::grpc::v0_4::{MemoryImportQuery, MemoryImportQueryReceipt};

impl NodeService {
    /// Checks the negotiated query envelope before admitting its original-provider readonly operation.
    pub(super) async fn handle_memory_query(
        &self,
        query: MemoryImportQuery,
        session: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        if self.engine.catalog().node_contract_version()
            != integration::grpc::v0_4::CONTINUITY_NODE_CONTRACT_VERSION
            && self.engine.catalog().node_contract_version()
                != integration::grpc::v0_4::SAFETY_NODE_CONTRACT_VERSION
            || query.session_id != session
            || query.authorization_digest != integration::memory_import_query_digest(&query)
        {
            return Err(NodeServiceError::Protocol(
                "Memory query was not negotiated or its authorization differs".into(),
            ));
        }
        self.engine
            .admit_controller_epoch(&query.controller_epoch)?;
        let spec = crate::ImportQuerySpec {
            query_id: query.query_id.clone(),
            import_id: query.import_id.clone(),
            command_id: query.command_id.clone(),
            provider_id: query.consumer_provider_id.clone(),
            original_content_digest: query.original_content_digest.clone(),
            generation: query.generation,
            valid_for_ms: query.valid_for_ms,
        };
        let result = self.engine.query_original_memory_import(&spec).await;
        let (mut result, mut reason) = match &result {
            Ok(record) => (
                record.result.as_ref(),
                if record.result.is_none() {
                    "query remains interrupted or unavailable".to_string()
                } else {
                    String::new()
                },
            ),
            Err(error) => (None, error.to_string()),
        };
        if let Some(evidence) = result
            && let Err(error) = self
                .engine
                .publish_reconciled_memory_import(&query.import_id, evidence, session)
                .await
        {
            result = None;
            reason = format!(
                "original backend result is journaled; metadata publication remains pending: {error}"
            );
        }
        self.send_memory_query_receipt(session, &query, result, &reason, outbound, sequence)
            .await
    }

    /// Sends durable query evidence without changing import history, execution or replica placement.
    pub(super) async fn send_memory_query_receipt(
        &self,
        session: &str,
        query: &MemoryImportQuery,
        result: Option<&crate::ImportQueryResult>,
        reason: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        let original = self.engine.original_import_continuity(&query.import_id)?;
        let status = match (result, original.as_ref()) {
            (Some(result), Some(original)) => match result
                .status(original)
                .map_err(EngineError::Journal)?
            {
                MemoryImportStatus::Completed => MemoryImportReceiptStatus::MemoryImportCompleted,
                MemoryImportStatus::Rejected => MemoryImportReceiptStatus::MemoryImportRejected,
                _ => MemoryImportReceiptStatus::MemoryImportUnknown,
            },
            _ => MemoryImportReceiptStatus::MemoryImportUnknown,
        };
        let mut seq = sequence.lock().await;
        *seq = seq.saturating_add(1);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::MemoryImportQueryReceipt(
                    MemoryImportQueryReceipt {
                        session_id: session.to_string(),
                        sequence: *seq,
                        query_id: query.query_id.clone(),
                        import_id: query.import_id.clone(),
                        command_id: query.command_id.clone(),
                        generation: query.generation,
                        status: status as i32,
                        reason: reason.chars().take(1024).collect(),
                        identity_digest: original
                            .as_ref()
                            .map(|e| e.identity_digest.clone())
                            .unwrap_or_default(),
                        provider_instance_id: original
                            .as_ref()
                            .map(|e| e.provider_instance_id.clone())
                            .unwrap_or_default(),
                        provider_contract_digest: original
                            .as_ref()
                            .map(|e| e.provider_contract_digest.clone())
                            .unwrap_or_default(),
                        result_json: result
                            .map(serde_json::to_string)
                            .transpose()
                            .map_err(|e| NodeServiceError::Protocol(e.to_string()))?
                            .unwrap_or_default(),
                    },
                )),
            })
            .map_err(|_| NodeServiceError::Closed)
    }
}
