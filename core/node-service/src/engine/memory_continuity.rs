//! Original-provider continuity without redispatch, synthetic placement or implicit backend identity.

use super::*;
use crate::{
    ImportContinuity, ImportQueryRecord, ImportQueryResult, ImportQuerySpec, PrepareMemoryImport,
};

impl LocalIntegrationEngine {
    /// Publishes original backend completion metadata without restaging bytes or repeating import.
    pub(crate) async fn publish_reconciled_memory_import(
        &self,
        import_id: &str,
        result: &ImportQueryResult,
        session: &str,
    ) -> Result<(), EngineError> {
        let original = self
            .original_import_continuity(import_id)?
            .ok_or_else(|| EngineError::Memory("original import identity is absent".into()))?;
        if result.status(&original)? != crate::MemoryImportStatus::Completed {
            return Ok(());
        }
        if self.memory_provider_contract_digest(&original.provider_id)?
            != original.provider_contract_digest
        {
            return Err(EngineError::Memory("original provider workflow changed; cached history cannot publish under a replacement contract".into()));
        }
        let manifest: MemoryArtifactManifest = serde_json::from_str(
            original.original_invocation["manifest_json"]
                .as_str()
                .ok_or_else(|| EngineError::Memory("original import manifest is absent".into()))?,
        )
        .map_err(EngineError::Json)?;
        let ledger = self
            .inner
            .memory_ledgers
            .get(&original.provider_id)
            .ok_or_else(|| EngineError::Memory("original provider ledger is absent".into()))?;
        ledger
            .record_import(&manifest, None)
            .map_err(|e| EngineError::Memory(e.to_string()))?;
        self.artifact_stager()
            .ok_or_else(|| EngineError::Memory("original Artifact data plane is absent".into()))?
            .record_memory_replica(
                &manifest,
                &NodeId::new(self.catalog().node_id())
                    .map_err(|e| EngineError::Memory(e.to_string()))?,
                session,
                &original.provider_id,
                "imported",
                None,
            )
            .await?;
        Ok(())
    }
    /// Returns the configured provider contract including every endpoint used by import and query.
    pub(crate) fn memory_provider_contract_digest(
        &self,
        provider_id: &str,
    ) -> Result<String, EngineError> {
        let provider = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .ok_or_else(|| {
                EngineError::Configuration("original Memory provider is unavailable".into())
            })?;
        let mut content = format!("{provider:?}");
        let mut connections = BTreeSet::new();
        if let Some(import) = provider.import() {
            connections.extend(
                import
                    .steps()
                    .iter()
                    .map(crate::CompiledWorkflowStep::connection),
            );
        }
        if let Some(continuity) = provider.continuity() {
            connections.insert(continuity.instance().connection());
            connections.insert(continuity.query().connection());
        }
        for id in connections {
            content.push_str(&format!(
                "\n{:?}",
                self.catalog()
                    .connections()
                    .get(id)
                    .ok_or_else(|| EngineError::Configuration(
                        "original Memory connection is unavailable".into()
                    ))?
            ));
        }
        Ok(format!("sha256:{}", digest_text(&content)))
    }

    /// Reads the actual backend instance through its explicitly configured readonly contract.
    async fn memory_provider_instance(&self, provider_id: &str) -> Result<String, EngineError> {
        let continuity = self
            .catalog()
            .memory_providers()
            .get(provider_id)
            .and_then(|p| p.continuity())
            .ok_or_else(|| {
                EngineError::Memory("provider has no original-instance observation contract".into())
            })?;
        let mut context = WorkflowContext::new(serde_json::json!({}));
        self.run_steps(std::slice::from_ref(continuity.instance()), &mut context)
            .await?;
        let response = context.as_json();
        let pointer = format!(
            "/steps/{}",
            continuity
                .instance()
                .id()
                .replace('~', "~0")
                .replace('/', "~1")
        );
        let identity = response
            .pointer(&pointer)
            .and_then(|v| v.pointer(continuity.instance_pointer()))
            .and_then(serde_json::Value::as_str)
            .filter(|s| !s.trim().is_empty() && s.trim() == *s && s.len() <= 256)
            .ok_or_else(|| {
                EngineError::Memory(
                    "backend did not expose a bounded persistent instance identity".into(),
                )
            })?;
        Ok(identity.to_string())
    }

    /// Durably freezes actual original backend identity before an import can call its workflow.
    pub(crate) async fn prepare_authorized_memory_import(
        &self,
        import: &integration::grpc::v0_4::MemoryImport,
        identity: &str,
    ) -> Result<PrepareMemoryImport, EngineError> {
        if self
            .inner
            .journal
            .memory_import(&import.import_id)?
            .is_some()
        {
            return self.prepare_memory_import(&import.import_id, &import.command_id, identity);
        }
        let supports_continuity = self
            .catalog()
            .memory_providers()
            .get(&import.consumer_provider_id)
            .is_some_and(|p| p.continuity().is_some());
        let evidence = if supports_continuity {
            let instance = tokio::time::timeout(
                std::time::Duration::from_secs(30),
                self.memory_provider_instance(&import.consumer_provider_id),
            )
            .await
            .map_err(|_| {
                EngineError::Memory(
                    "original provider instance observation timed out before import".into(),
                )
            })??;
            Some(ImportContinuity {
                import_id: import.import_id.clone(),
                command_id: import.command_id.clone(),
                provider_id: import.consumer_provider_id.clone(),
                identity_digest: identity.to_string(),
                original_content_digest: integration::memory_import_content_digest(import),
                provider_instance_id: instance,
                provider_contract_digest: self
                    .memory_provider_contract_digest(&import.consumer_provider_id)?,
                original_invocation: serde_json::to_value(import).map_err(EngineError::Json)?,
                admitted_at_ms: continuity_now()?,
            })
        } else {
            None
        };
        self.inner
            .journal
            .prepare_memory_import_continuity(
                &import.import_id,
                &import.command_id,
                identity,
                evidence.as_ref(),
            )
            .map_err(EngineError::Journal)
    }

    /// Returns original immutable metadata for the local import/query workflow context.
    pub(crate) fn original_import_continuity(
        &self,
        import_id: &str,
    ) -> Result<Option<ImportContinuity>, EngineError> {
        self.inner
            .journal
            .import_continuity(import_id)
            .map_err(EngineError::Journal)
    }

    /// Performs one journaled readonly query; retry and restart never rerun this query or import.
    pub(crate) async fn query_original_memory_import(
        &self,
        spec: &ImportQuerySpec,
    ) -> Result<ImportQueryRecord, EngineError> {
        let (record, start) = self
            .inner
            .journal
            .prepare_import_query(spec, continuity_now()?)?;
        if !start {
            return Ok(record);
        }
        let original = self
            .original_import_continuity(&spec.import_id)?
            .ok_or_else(|| {
                EngineError::Memory("legacy import has no original backend identity".into())
            })?;
        let now = continuity_now()?;
        let expires = record.received_at_ms.saturating_add(spec.valid_for_ms);
        if now < record.received_at_ms || now >= expires {
            return Ok(record);
        }
        let result = tokio::time::timeout(
            std::time::Duration::from_millis(expires - now),
            self.read_original_import_result(&original),
        )
        .await
        .map_err(|_| EngineError::Memory("original readonly query budget expired".into()))??;
        self.inner
            .journal
            .record_import_query(&spec.query_id, &result, continuity_now()?)
            .map_err(EngineError::Journal)
    }

    /// Checks both live backend identity and the exact original workflow before reading an outcome.
    async fn read_original_import_result(
        &self,
        original: &ImportContinuity,
    ) -> Result<ImportQueryResult, EngineError> {
        if self.memory_provider_contract_digest(&original.provider_id)?
            != original.provider_contract_digest
            || self.memory_provider_instance(&original.provider_id).await?
                != original.provider_instance_id
        {
            return Err(EngineError::Memory(
                "original provider instance or workflow contract changed".into(),
            ));
        }
        let continuity = self.catalog().memory_providers()[&original.provider_id]
            .continuity()
            .ok_or_else(|| EngineError::Memory("original query contract is unavailable".into()))?;
        let mut context = WorkflowContext::new(serde_json::json!({"memory_import": original}));
        self.run_steps(std::slice::from_ref(continuity.query()), &mut context)
            .await?;
        let response = context.as_json();
        let pointer = format!(
            "/steps/{}",
            continuity
                .query()
                .id()
                .replace('~', "~0")
                .replace('/', "~1")
        );
        let result = response
            .pointer(&pointer)
            .and_then(|v| v.pointer(continuity.result_pointer()))
            .ok_or_else(|| {
                EngineError::Memory("original provider query result is unavailable".into())
            })?;
        if serde_json::to_vec(result).map_err(EngineError::Json)?.len() > 16 * 1024 {
            return Err(EngineError::Memory("query result exceeds 16 KiB".into()));
        }
        let result: ImportQueryResult =
            serde_json::from_value(result.clone()).map_err(EngineError::Json)?;
        result.status(original)?;
        Ok(result)
    }
}

/// Reads the Node-local receive clock without borrowing or comparing independent source clocks.
fn continuity_now() -> Result<u64, EngineError> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| EngineError::Memory("Node receive clock predates its epoch".into()))?;
    u64::try_from(elapsed.as_millis())
        .map_err(|_| EngineError::Memory("Node receive clock overflow".into()))
}
