//! Explicit current-use control over original provider workflows, separate from physical execution.

use super::*;
use crate::{
    ImportContinuity, JournalMemoryUseCommand, MemoryReceiverBudget, ReceiverClockObservation,
};
use domain::{
    MemoryPreparation, MemoryUseControl, MemoryUseOperation, MemoryUseProviderAcknowledgement,
    MemoryUseReceipt, MemoryUseStatus,
};
use serde::Deserialize;

/// Exact private reference-backend receipt; Core never interprets this Local How clock profile.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProviderReceipt {
    /// Explicit local-profile codec.
    schema: String,
    /// Exact original control identity.
    command_id: String,
    /// Stable original prepared context.
    lease_id: String,
    /// Exact grant ordinal or irreversible revoke zero.
    generation: u64,
    /// Complete original source/purpose digest.
    preparation_digest: String,
    /// Original Node configuration.
    configuration_digest: String,
    /// Exact selected provider.
    provider_id: String,
    /// Actual original backend identity.
    provider_instance_id: String,
    /// Explicit content-use outcome only.
    status: String,
    /// Actual same-kernel/time-namespace budget, absent for revoke.
    receiver_budget: Option<MemoryReceiverBudget>,
    /// Complete granted source coverage, empty for revoke.
    import_ids: Vec<String>,
}

/// Readonly original-command observation cannot grant a new budget or repeat an effect.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProviderQuery {
    /// Original immutable provider outcome when retained.
    receipt: Option<serde_json::Value>,
    /// Current interval effectiveness independently of its historical reply.
    effective: bool,
}

/// Bounds diagnostics by bytes without splitting UTF-8 or parsing them as permission.
fn reason(error: &str) -> String {
    let mut end = error.len().min(1024);
    while !error.is_char_boundary(end) {
        end -= 1;
    }
    error[..end].into()
}

impl LocalIntegrationEngine {
    /// Persists a fence before queuing backend work; initial grants require current prepared authority.
    pub(crate) fn admit_memory_use_control(
        &self,
        command: &MemoryUseControl,
        session: &str,
        remaining_budget_ms: u64,
    ) -> Result<JournalMemoryUseCommand, EngineError> {
        command.validate().map_err(EngineError::Memory)?;
        if self.catalog().node_contract_version()
            != integration::grpc::v0_4::MEMORY_USE_NODE_CONTRACT_VERSION
            || command.node_id.as_str() != self.catalog().node_id()
        {
            return Err(EngineError::Memory(
                "current Memory-use was not negotiated for this Node".into(),
            ));
        }
        if command.operation == MemoryUseOperation::Grant {
            self.require_use_source_session(&command.source_session_id)?;
            if command.source_session_id != session {
                return Err(EngineError::Memory(
                    "Memory-use grant belongs to another source session".into(),
                ));
            }
            let context = self
                .inner
                .journal
                .memory_preparation(&command.preparation_id)?
                .ok_or_else(|| {
                    EngineError::Memory("Memory-use original preparation is absent".into())
                })?;
            command
                .validate_preparation(&context.preparation)
                .map_err(EngineError::Memory)?;
            if self.memory_preparation_configuration(&context.preparation)?
                != command.configuration_digest
                || self
                    .inner
                    .journal
                    .cancellation_pending(&command.execution_id)?
            {
                return Err(EngineError::Memory(
                    "Memory-use original configuration changed or its attempt was cancelled".into(),
                ));
            }
        }
        let _gate =
            self.inner.memory_context_gate.lock().map_err(|_| {
                EngineError::Memory("Memory context ordering is unavailable".into())
            })?;
        self.inner
            .journal
            .admit_memory_use_control(
                command,
                &ReceiverClockObservation::read()?,
                remaining_budget_ms,
            )
            .map_err(EngineError::from)
    }

    /// Checks actual accepted process-local session ownership before new grant effects or dispatch.
    fn require_use_source_session(&self, session: &str) -> Result<(), EngineError> {
        let current = self
            .inner
            .publication_session
            .lock()
            .map_err(|_| EngineError::Memory("current Node session is unavailable".into()))?;
        if current.as_deref() != Some(session) {
            return Err(EngineError::Memory(
                "original Memory-use source session ended".into(),
            ));
        }
        Ok(())
    }

    /// Runs only the configured fixed local step, with complete original metadata as mapped input.
    async fn use_step(
        &self,
        step: &crate::CompiledWorkflowStep,
        preparation: &MemoryPreparation,
        request: &serde_json::Value,
    ) -> Result<serde_json::Value, EngineError> {
        let mut invocation = preparation.invocation.clone();
        invocation
            .as_object_mut()
            .expect("validated invocation")
            .insert("memory_use".into(), request.clone());
        let mut context =
            WorkflowContext::with_attempt_id(invocation, preparation.execution_id.clone());
        self.run_steps(std::slice::from_ref(step), &mut context)
            .await?;
        context
            .as_json()
            .pointer(&format!(
                "/steps/{}",
                step.id().replace('~', "~0").replace('/', "~1")
            ))
            .cloned()
            .ok_or_else(|| {
                EngineError::Memory("Memory-use provider did not return a bounded response".into())
            })
    }

    /// Reacquires exact original instance/contracts; it never fills legacy absence from new registration.
    async fn use_originals(
        &self,
        preparation: &MemoryPreparation,
        provider: &str,
    ) -> Result<Vec<ImportContinuity>, EngineError> {
        let mut originals = Vec::new();
        let current = self.memory_provider_contract_digest(provider)?;
        let instance = self.memory_provider_instance(provider).await?;
        for input in preparation
            .inputs
            .iter()
            .filter(|input| input.consumer.provider_id() == provider)
        {
            let original = self
                .original_import_continuity(&input.import_id)?
                .ok_or_else(|| {
                    EngineError::Memory("Memory-use lost original import continuity".into())
                })?;
            if original.provider_id != provider
                || original.provider_contract_digest != current
                || original.provider_instance_id != instance
            {
                return Err(EngineError::Memory(
                    "Memory-use original provider/configuration/instance changed".into(),
                ));
            }
            originals.push(original);
        }
        if originals.is_empty() {
            return Err(EngineError::Memory(
                "Memory-use provider has no admitted inputs".into(),
            ));
        }
        Ok(originals)
    }

    /// Retains exact Rust canonical bytes, avoiding reinterpretation of typed-map floating-point JSON.
    fn use_request(
        &self,
        record: &JournalMemoryUseCommand,
        preparation: &MemoryPreparation,
        originals: &[ImportContinuity],
    ) -> Result<serde_json::Value, EngineError> {
        let grant = record.command.operation == MemoryUseOperation::Grant;
        let encoded = if grant {
            serde_json::to_string(&serde_json::to_value(preparation).map_err(EngineError::Json)?)
                .map_err(EngineError::Json)?
        } else {
            String::new()
        };
        Ok(serde_json::json!({
            "schema":"roboguide.memory-use-provider-command/v0.1","control":record.command,
            "preparation_json":encoded,"receiver_budget":if grant {serde_json::to_value(&record.budget).map_err(EngineError::Json)?}else{serde_json::Value::Null},
            "imports":if grant {serde_json::to_value(originals).map_err(EngineError::Json)?}else{serde_json::json!([])},
        }))
    }

    /// Verifies complete raw local evidence against the original Node budget and provider identity.
    fn use_provider_proof(
        &self,
        record: &JournalMemoryUseCommand,
        provider: &str,
        originals: &[ImportContinuity],
        value: serde_json::Value,
    ) -> Result<MemoryUseProviderAcknowledgement, EngineError> {
        let receipt: ProviderReceipt =
            serde_json::from_value(value.clone()).map_err(EngineError::Json)?;
        let command = &record.command;
        let granted = command.operation == MemoryUseOperation::Grant;
        let expected = originals
            .iter()
            .map(|item| item.import_id.clone())
            .collect::<BTreeSet<_>>();
        let actual = receipt.import_ids.iter().cloned().collect::<BTreeSet<_>>();
        let first = originals
            .first()
            .ok_or_else(|| EngineError::Memory("Memory-use original provider is absent".into()))?;
        if receipt.schema != "roboguide.memory-use-provider-receipt/v0.1"
            || receipt.command_id != command.command_id
            || receipt.lease_id != command.lease_id
            || receipt.generation != command.generation
            || receipt.preparation_digest != command.preparation_digest
            || receipt.configuration_digest != command.configuration_digest
            || receipt.provider_id != provider
            || originals.iter().any(|item| {
                item.provider_instance_id != receipt.provider_instance_id
                    || item.provider_contract_digest != first.provider_contract_digest
            })
            || actual.len() != receipt.import_ids.len()
            || (granted && actual != expected)
            || (!granted && !actual.is_empty())
            || receipt.status != if granted { "granted" } else { "revoked" }
            || (granted
                && (receipt.receiver_budget.as_ref() != Some(&record.budget)
                    || record
                        .budget
                        .remaining_ms(&ReceiverClockObservation::read()?)
                        == 0))
            || (!granted && receipt.receiver_budget.is_some())
        {
            return Err(EngineError::Memory("Memory-use backend proof differs from its complete original command/provider/budget".into()));
        }
        let receipt_digest = format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&value).map_err(EngineError::Json)?)
        );
        Ok(MemoryUseProviderAcknowledgement {
            provider_id: provider.into(),
            provider_instance_id: receipt.provider_instance_id,
            provider_contract_digest: first.provider_contract_digest.clone(),
            import_ids: receipt.import_ids,
            receipt_digest,
            original_receipt: value,
        })
    }

    /// Reads only the original backend outcome/effectiveness under a durable nonrenewable query count.
    async fn use_query(
        &self,
        record: &JournalMemoryUseCommand,
        preparation: &MemoryPreparation,
        provider: &str,
        request: &serde_json::Value,
    ) -> Result<serde_json::Value, EngineError> {
        let usage = self
            .catalog()
            .memory_providers()
            .get(provider)
            .and_then(|p| p.use_lease())
            .ok_or_else(|| {
                EngineError::Memory("original use query workflow is unavailable".into())
            })?;
        self.inner
            .journal
            .begin_memory_use_query(&record.command.command_id, provider)?;
        let observed: ProviderQuery =
            serde_json::from_value(self.use_step(usage.query(), preparation, request).await?)
                .map_err(EngineError::Json)?;
        let value = observed.receipt.ok_or_else(|| {
            EngineError::Memory("original backend use outcome remains unknown".into())
        })?;
        if record.command.operation == MemoryUseOperation::Grant && !observed.effective {
            return Err(EngineError::Memory(
                "original backend use interval is no longer effective".into(),
            ));
        }
        Ok(value)
    }

    /// Issues at most one POST after intent Commit; every interrupted retry uses original GET only.
    async fn use_provider(
        &self,
        record: &JournalMemoryUseCommand,
        preparation: &MemoryPreparation,
        provider: &str,
    ) -> Result<MemoryUseProviderAcknowledgement, EngineError> {
        if record.command.operation == MemoryUseOperation::Grant {
            self.require_use_source_session(&record.command.source_session_id)?;
        }
        let originals = self.use_originals(preparation, provider).await?;
        let request = self.use_request(record, preparation, &originals)?;
        let (first, effect) = self.inner.journal.prepare_memory_use_effect(
            &record.command.command_id,
            provider,
            &request,
            &ReceiverClockObservation::read()?,
        )?;
        let usage = self
            .catalog()
            .memory_providers()
            .get(provider)
            .and_then(|p| p.use_lease())
            .ok_or_else(|| {
                EngineError::Memory("original use control workflow is unavailable".into())
            })?;
        let response = if first {
            self.use_step(usage.control(), preparation, &request).await
        } else {
            self.use_query(record, preparation, provider, &effect.request)
                .await
        };
        let value = match response {
            Ok(value) => value,
            Err(_) => {
                let mut recovered = None;
                for _ in 0..3 {
                    if let Ok(value) = self
                        .use_query(record, preparation, provider, &effect.request)
                        .await
                    {
                        recovered = Some(value);
                        break;
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(25)).await;
                }
                recovered.ok_or_else(|| {
                    EngineError::Memory(
                        "original Memory-use effect requires readonly recovery".into(),
                    )
                })?
            }
        };
        let proof = self.use_provider_proof(record, provider, &originals, value)?;
        self.inner.journal.record_memory_use_effect_receipt(
            &record.command.command_id,
            provider,
            &proof.original_receipt,
        )?;
        if record.command.operation == MemoryUseOperation::Grant {
            self.require_use_source_session(&record.command.source_session_id)?;
        }
        Ok(proof)
    }

    /// Completes only original journal work, serializing duplicate frames without a second authority.
    pub(crate) async fn complete_memory_use_control(
        &self,
        command: &MemoryUseControl,
    ) -> Result<MemoryUseReceipt, EngineError> {
        let gate = {
            let mut gates = self.inner.memory_use_workers.lock().map_err(|_| {
                EngineError::Memory("Memory-use worker ordering is unavailable".into())
            })?;
            if gates.len() >= 4096 && !gates.contains_key(&command.lease_id) {
                return Err(EngineError::Memory(
                    "Memory-use worker registry is full".into(),
                ));
            }
            Arc::clone(
                gates
                    .entry(command.lease_id.clone())
                    .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))),
            )
        };
        let _guard = gate.lock().await;
        let record = self
            .inner
            .journal
            .memory_use_command(&command.command_id)?
            .ok_or_else(|| EngineError::Memory("Memory-use command was never admitted".into()))?;
        if record.command != *command {
            return Err(EngineError::Memory(
                "original Memory-use command changed".into(),
            ));
        }
        if let Some(receipt) = &record.receipt {
            return Ok(receipt.clone());
        }
        let context = self
            .inner
            .journal
            .memory_preparation(&command.preparation_id)?
            .ok_or_else(|| {
                EngineError::Memory("Memory-use original preparation is unavailable".into())
            })?;
        command
            .validate_preparation(&context.preparation)
            .map_err(EngineError::Memory)?;
        let mut receipt = MemoryUseReceipt {
            schema: "roboguide.memory-use-receipt/v0.1".into(),
            command_id: command.command_id.clone(),
            lease_id: command.lease_id.clone(),
            generation: command.generation,
            preparation_id: command.preparation_id.clone(),
            preparation_digest: command.preparation_digest.clone(),
            configuration_digest: command.configuration_digest.clone(),
            status: MemoryUseStatus::Unknown,
            remaining_budget_ms: 0,
            providers: Vec::new(),
            reason: String::new(),
        };
        let providers = context
            .preparation
            .inputs
            .iter()
            .map(|input| input.consumer.provider_id())
            .collect::<BTreeSet<_>>();
        let result: Result<(), EngineError> = async {
            if self.memory_preparation_configuration(&context.preparation)?
                != command.configuration_digest
            {
                return Err(EngineError::Memory(
                    "original Memory-use Node configuration changed".into(),
                ));
            }
            for provider in providers {
                receipt.providers.push(
                    self.use_provider(&record, &context.preparation, provider)
                        .await?,
                );
            }
            Ok(())
        }
        .await;
        match result {
            Ok(()) => {
                if command.operation == MemoryUseOperation::Grant {
                    receipt.remaining_budget_ms = record
                        .budget
                        .remaining_ms(&ReceiverClockObservation::read()?);
                    if receipt.remaining_budget_ms > 0 {
                        receipt.status = MemoryUseStatus::Granted;
                    } else {
                        receipt.reason =
                            "original Memory-use interval expired before receipt Commit".into();
                    }
                } else {
                    receipt.status = MemoryUseStatus::Revoked;
                }
            }
            Err(error) => receipt.reason = reason(&error.to_string()),
        }
        self.inner
            .journal
            .record_memory_use_receipt(&receipt)
            .map_err(EngineError::from)
    }

    /// Fresh original-provider checks fence backend restart between an ACK and first local execution.
    pub(super) async fn verify_memory_use_before_execute(
        &self,
        preparation: &MemoryPreparation,
    ) -> Result<(), EngineError> {
        let record = self.inner.journal.current_memory_use(
            &preparation.preparation_id,
            &ReceiverClockObservation::read()?,
        )?;
        self.require_use_source_session(&preparation.source_session_id)?;
        let providers = preparation
            .inputs
            .iter()
            .map(|input| input.consumer.provider_id())
            .collect::<BTreeSet<_>>();
        for provider in providers {
            let originals = self.use_originals(preparation, provider).await?;
            let request = self.use_request(&record, preparation, &originals)?;
            let value = self
                .use_query(&record, preparation, provider, &request)
                .await?;
            self.use_provider_proof(&record, provider, &originals, value)?;
        }
        Ok(())
    }

    /// Checks current local use authority at the last dispatch boundary, independently of preparation.
    pub(super) fn authorize_current_memory_use(
        &self,
        preparation: &MemoryPreparation,
    ) -> Result<(), EngineError> {
        if self.catalog().node_contract_version()
            == integration::grpc::v0_4::MEMORY_USE_NODE_CONTRACT_VERSION
        {
            let current = self.inner.journal.current_memory_use(
                &preparation.preparation_id,
                &ReceiverClockObservation::read()?,
            )?;
            self.require_use_source_session(&preparation.source_session_id)?;
            current
                .command
                .validate_preparation(preparation)
                .map_err(EngineError::Memory)?;
        }
        Ok(())
    }
}
