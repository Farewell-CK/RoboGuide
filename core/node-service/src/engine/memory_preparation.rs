//! Original prepared contexts and actual backend observations before any Local How dispatch.

use super::*;
use crate::{BeginMemoryObservation, JournalMemoryPreparation, ReceiverClockObservation};
use domain::{
    MemoryControlCommand, MemoryControlKind, MemoryControlReceipt, MemoryControlStatus,
    MemoryPreparation,
};

impl LocalIntegrationEngine {
    /// Validates immutable startup configuration once and rereads durable fences per bounded chunk.
    pub(super) fn prepared_import_acquisition_guard(
        &self,
        invocation: &serde_json::Value,
    ) -> Result<crate::artifact::AcquisitionGuard, EngineError> {
        self.check_prepared_import_invocation(invocation)?;
        let Some(id) = invocation
            .pointer("/memory_import/original_invocation/memory_preparation_id")
            .and_then(serde_json::Value::as_str)
            .filter(|id| !id.is_empty())
        else {
            return Ok(std::sync::Arc::new(|| Ok(())));
        };
        let original = self.inner.journal.memory_preparation(id)?.ok_or_else(|| {
            EngineError::Configuration("original import preparation is unavailable".into())
        })?;
        let guard = self.inner.journal.memory_acquisition_guard(&original)?;
        Ok(std::sync::Arc::new(move || {
            guard().map_err(|error| ArtifactError::Configuration(error.to_string()))
        }))
    }

    /// Rechecks the original prepared import before acquisition and each bounded streamed write.
    pub(super) fn check_prepared_import_invocation(
        &self,
        invocation: &serde_json::Value,
    ) -> Result<(), EngineError> {
        let Some(id) = invocation
            .pointer("/memory_import/original_invocation/memory_preparation_id")
            .and_then(serde_json::Value::as_str)
            .filter(|id| !id.is_empty())
        else {
            return Ok(());
        };
        let original = self.inner.journal.memory_preparation(id)?.ok_or_else(|| {
            EngineError::Configuration("original import preparation is unavailable".into())
        })?;
        self.current_memory_preparation(
            id,
            &original
                .preparation
                .digest()
                .map_err(EngineError::Configuration)?,
            &original.preparation.source_session_id,
        )?;
        if self
            .inner
            .journal
            .cancellation_pending(&original.preparation.execution_id)?
        {
            return Err(EngineError::Configuration(
                "prepared import was cancelled".into(),
            ));
        }
        Ok(())
    }

    /// Checks a prepared invocation before artifact reads without authorizing Local How dispatch.
    pub(super) fn check_prepared_execution_invocation(
        &self,
        invocation: &serde_json::Value,
    ) -> Result<(), EngineError> {
        if let Some(memory) = invocation.get("memory_preparation") {
            let id = memory
                .get("preparation_id")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    EngineError::Configuration("prepared dispatch lacks original identity".into())
                })?;
            let digest = memory
                .get("preparation_digest")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    EngineError::Configuration("prepared dispatch lacks original digest".into())
                })?;
            let original = self.inner.journal.memory_preparation(id)?.ok_or_else(|| {
                EngineError::Configuration("prepared dispatch context disappeared".into())
            })?;
            self.current_memory_preparation(id, digest, &original.preparation.source_session_id)?;
        }
        Ok(())
    }
    /// Freezes actual operation, provider and connection identities without invoking a backend.
    pub(super) fn memory_preparation_configuration(
        &self,
        preparation: &MemoryPreparation,
    ) -> Result<String, EngineError> {
        preparation.validate().map_err(EngineError::Configuration)?;
        if !integration::grpc::v0_4::supports_memory_preparation(
            self.catalog().node_contract_version(),
        ) || preparation.node_id.as_str() != self.catalog().node_id()
        {
            return Err(EngineError::Configuration(
                "prepared context requires the exact v0.9 receiver".into(),
            ));
        }
        let operation = preparation
            .invocation
            .get("operation")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                EngineError::Configuration(
                    "prepared invocation lacks its semantic operation".into(),
                )
            })?;
        let capability = self.operation(operation)?;
        validate_resources(self.catalog(), capability, &preparation.resource_ids)?;
        if preparation.invocation.get("resource_ids")
            != Some(&serde_json::json!(preparation.resource_ids))
        {
            return Err(EngineError::Configuration(
                "prepared invocation resources differ from commitment".into(),
            ));
        }
        if let Some(session) = preparation.invocation.get("execution_session") {
            let session: domain::ExecutionSessionDescriptor =
                serde_json::from_value(session.clone()).map_err(EngineError::Json)?;
            session
                .validate_slot(
                    preparation.task_ref.mission_id(),
                    &preparation.group_id,
                    preparation.task_ref.task_id(),
                    &preparation.role_id,
                )
                .map_err(EngineError::Configuration)?;
        }
        let mut identity = workflow_digest(self.catalog(), capability, &preparation.invocation)?;
        for input in &preparation.inputs {
            let provider = self
                .catalog()
                .memory_providers()
                .get(input.consumer.provider_id())
                .ok_or_else(|| {
                    EngineError::Configuration("prepared consumer is not configured".into())
                })?;
            let kind: domain::MemoryKind =
                serde_json::from_value(serde_json::json!(provider.kind()))
                    .map_err(EngineError::Json)?;
            let scope = match provider.scope() {
                "local" => domain::MemoryScopeLimit::Local,
                "global" => domain::MemoryScopeLimit::Global,
                _ => {
                    return Err(EngineError::Configuration(
                        "unsupported prepared provider scope".into(),
                    ));
                }
            };
            let visibility = match provider.visibility() {
                "exchangeable" => domain::MemoryVisibility::Exchangeable,
                _ => {
                    return Err(EngineError::Configuration(
                        "prepared provider does not exchange content".into(),
                    ));
                }
            };
            let mut descriptor = domain::MemoryProviderDescriptor::new(
                provider.id(),
                domain::LocalSystemId::new(provider.owner())
                    .map_err(|e| EngineError::Configuration(e.to_string()))?,
                kind,
                scope,
                visibility,
                provider.payload_schema(),
                provider.media_type(),
            )
            .map_err(|e| EngineError::Configuration(e.to_string()))?
            .with_preparation_support();
            if provider.use_lease().is_some() {
                descriptor = descriptor.with_use_lease_support();
            }
            if provider.owner() != capability.owner() {
                return Err(EngineError::Configuration(
                    "prepared content provider and operation have different Local EAIOS owners"
                        .into(),
                ));
            }
            if descriptor != input.consumer
                || provider.availability().is_none()
                || provider.continuity().is_none()
                || provider.import().is_none()
            {
                return Err(EngineError::Configuration(
                    "prepared consumer declaration or actual observation workflow changed".into(),
                ));
            }
            identity.push_str(&self.memory_provider_contract_digest(provider.id())?);
        }
        Ok(format!("sha256:{}", digest_text(&identity)))
    }

    /// Rechecks original budget, session, immutable source and actual startup configuration.
    fn current_memory_preparation(
        &self,
        id: &str,
        digest: &str,
        session: &str,
    ) -> Result<JournalMemoryPreparation, EngineError> {
        let record = self.inner.journal.memory_preparation(id)?.ok_or_else(|| {
            EngineError::Configuration("Memory preparation is not durable".into())
        })?;
        let now = ReceiverClockObservation::read()?;
        if record.revoked
            || record.budget.remaining_ms(&now) == 0
            || record.preparation.source_session_id != session
            || record
                .preparation
                .digest()
                .map_err(EngineError::Configuration)?
                != digest
            || record.configuration_digest
                != self.memory_preparation_configuration(&record.preparation)?
        {
            return Err(EngineError::Configuration(
                "original Memory context is expired, revoked, foreign or reconfigured".into(),
            ));
        }
        Ok(record)
    }

    /// Processes one negotiated command; only explicit probes perform once-admitted backend reads.
    pub(crate) async fn process_memory_control(
        &self,
        command: &MemoryControlCommand,
        session: &str,
        valid_for_ms: u64,
    ) -> Result<MemoryControlReceipt, EngineError> {
        command.validate().map_err(EngineError::Configuration)?;
        if !integration::grpc::v0_4::supports_memory_preparation(
            self.catalog().node_contract_version(),
        ) {
            return Err(EngineError::Configuration(
                "Memory control was not negotiated".into(),
            ));
        }
        if let Some(context) = &command.preparation {
            if context.source_session_id != session {
                return Err(EngineError::Configuration(
                    "Memory prepare names another session".into(),
                ));
            }
            self.memory_preparation_configuration(context)?;
        }
        let admission = self.inner.journal.begin_memory_observation(command)?;
        if let BeginMemoryObservation::Existing(receipt) = admission {
            return Ok(receipt);
        }
        let mut receipt = MemoryControlReceipt {
            command_id: command.command_id(),
            preparation_id: command.preparation_id.clone(),
            preparation_digest: command.preparation_digest.clone(),
            configuration_digest: None,
            status: MemoryControlStatus::Unavailable,
            remaining_budget_ms: 0,
            observations: vec![],
            reason: String::new(),
        };
        match command.operation {
            MemoryControlKind::Prepare => {
                let context = command
                    .preparation
                    .as_ref()
                    .expect("validated prepare context");
                if context.source_session_id != session {
                    return Err(EngineError::Configuration(
                        "Memory prepare belongs to another original session".into(),
                    ));
                }
                let configuration = self.memory_preparation_configuration(context)?;
                let existing = self
                    .inner
                    .journal
                    .memory_preparation(&context.preparation_id)?;
                let record = match (admission, existing) {
                    (BeginMemoryObservation::Interrupted, None) => {
                        receipt.status = MemoryControlStatus::Rejected;
                        receipt.reason="prepare was interrupted before original context admission; no retry can renew it".into();
                        return self
                            .inner
                            .journal
                            .record_memory_control_receipt(command, &receipt)
                            .map_err(EngineError::Journal);
                    }
                    (_, Some(record)) => {
                        if record.preparation != *context
                            || record.configuration_digest != configuration
                        {
                            return Err(EngineError::Configuration(
                                "original Memory preparation changed".into(),
                            ));
                        }
                        record
                    }
                    (_, None) => {
                        if self.inner.journal.get(&context.execution_id)?.is_some() {
                            return Err(EngineError::Configuration(
                                "Memory context cannot be introduced after Execute admission"
                                    .into(),
                            ));
                        }
                        self.inner.journal.prepare_memory_context(
                            context,
                            &ReceiverClockObservation::read()?,
                            valid_for_ms,
                            &configuration,
                        )?
                    }
                };
                receipt.configuration_digest = Some(record.configuration_digest);
                receipt.remaining_budget_ms = record
                    .budget
                    .remaining_ms(&ReceiverClockObservation::read()?);
                receipt.status = if record.revoked || receipt.remaining_budget_ms == 0 {
                    MemoryControlStatus::Rejected
                } else {
                    MemoryControlStatus::Prepared
                };
            }
            MemoryControlKind::Revoke => {
                let _gate = self.inner.memory_context_gate.lock().map_err(|_| {
                    EngineError::Configuration("Memory context ordering is unavailable".into())
                })?;
                self.inner
                    .journal
                    .revoke_memory_context(&command.preparation_id, &command.preparation_digest)?;
                receipt.configuration_digest = self
                    .inner
                    .journal
                    .memory_preparation(&command.preparation_id)?
                    .map(|r| r.configuration_digest);
                receipt.status = MemoryControlStatus::Revoked;
            }
            MemoryControlKind::Probe => {
                let record = self.current_memory_preparation(
                    &command.preparation_id,
                    &command.preparation_digest,
                    session,
                )?;
                receipt.configuration_digest = Some(record.configuration_digest.clone());
                if admission == BeginMemoryObservation::Interrupted {
                    receipt.reason="original observation was interrupted; this identity cannot read the backend again".into();
                } else {
                    match self.observe_prepared_memory(&record.preparation).await {
                        Ok(observations) => {
                            receipt.observations = observations;
                            let current = self.current_memory_preparation(
                                &command.preparation_id,
                                &command.preparation_digest,
                                session,
                            )?;
                            receipt.remaining_budget_ms = current
                                .budget
                                .remaining_ms(&ReceiverClockObservation::read()?);
                            receipt.status = MemoryControlStatus::Available;
                        }
                        Err(error) => {
                            receipt.reason = error.to_string().chars().take(1024).collect();
                        }
                    }
                }
            }
        }
        self.inner
            .journal
            .record_memory_control_receipt(command, &receipt)
            .map_err(EngineError::Journal)
    }

    /// Reads every original provider's actual current bytes, not its historical Imported projection.
    async fn observe_prepared_memory(
        &self,
        preparation: &MemoryPreparation,
    ) -> Result<Vec<domain::MemoryAvailabilityEvidence>, EngineError> {
        let mut observed = Vec::new();
        for input in &preparation.inputs {
            if !self
                .inner
                .journal
                .memory_import_completion_known(&input.import_id)?
            {
                return Err(EngineError::Memory(
                    "prepared input has no durable original import completion".into(),
                ));
            }
            let original = self
                .original_import_continuity(&input.import_id)?
                .ok_or_else(|| {
                    EngineError::Memory("original import provenance is unavailable".into())
                })?;
            if original.provider_id != input.consumer.provider_id()
                || original.provider_contract_digest
                    != self.memory_provider_contract_digest(input.consumer.provider_id())?
                || original.provider_instance_id
                    != self
                        .memory_provider_instance(input.consumer.provider_id())
                        .await?
            {
                return Err(EngineError::Memory(
                    "original Memory provider/configuration/instance changed".into(),
                ));
            }
            let availability = self
                .catalog()
                .memory_providers()
                .get(input.consumer.provider_id())
                .and_then(|p| p.availability())
                .ok_or_else(|| {
                    EngineError::Memory("actual availability observation is not configured".into())
                })?;
            let mut invocation = preparation.invocation.clone();
            invocation
                .as_object_mut()
                .expect("validated prepared invocation")
                .extend([
                    (
                        "memory_import".into(),
                        serde_json::to_value(&original).map_err(EngineError::Json)?,
                    ),
                    (
                        "memory_manifest".into(),
                        serde_json::to_value(&input.manifest).map_err(EngineError::Json)?,
                    ),
                ]);
            let mut context =
                WorkflowContext::with_attempt_id(invocation, preparation.execution_id.clone());
            self.run_steps(
                std::slice::from_ref(availability.observation()),
                &mut context,
            )
            .await?;
            let response = context.as_json();
            let step = format!(
                "/steps/{}",
                availability
                    .observation()
                    .id()
                    .replace('~', "~0")
                    .replace('/', "~1")
            );
            let value = response
                .pointer(&step)
                .and_then(|v| v.pointer(availability.result_pointer()))
                .ok_or_else(|| {
                    EngineError::Memory("backend did not return typed current availability".into())
                })?;
            let evidence: domain::MemoryAvailabilityEvidence =
                serde_json::from_value(value.clone()).map_err(EngineError::Json)?;
            evidence.validate(input).map_err(EngineError::Memory)?;
            if !evidence.available
                || evidence.identity_digest != original.identity_digest
                || evidence.provider_instance_id != original.provider_instance_id
                || evidence.provider_contract_digest != original.provider_contract_digest
            {
                return Err(EngineError::Memory(
                    "backend current bytes or original identity is unavailable".into(),
                ));
            }
            observed.push(evidence);
        }
        Ok(observed)
    }

    /// Authorizes the exact prepared import before Execute exists; it cannot grant an alternate ID.
    pub(super) fn validate_prepared_memory_import(
        &self,
        import: &integration::grpc::v0_4::MemoryImport,
    ) -> Result<(), EngineError> {
        let record = self
            .inner
            .journal
            .memory_preparation(&import.memory_preparation_id)?
            .ok_or_else(|| {
                EngineError::Configuration("prepared import context is unavailable".into())
            })?;
        let current = self.current_memory_preparation(
            &import.memory_preparation_id,
            &record
                .preparation
                .digest()
                .map_err(EngineError::Configuration)?,
            &import.session_id,
        )?;
        let preparation = &current.preparation;
        if preparation.execution_id != import.execution_id
            || preparation.group_id.as_str() != import.group_id
            || preparation.task_ref.mission_id().as_str() != import.mission_id
            || preparation.task_ref.task_id().as_str() != import.task_id
            || preparation.role_id.as_str() != import.role_id
        {
            return Err(EngineError::Configuration(
                "prepared import belongs to another logical/physical slot".into(),
            ));
        }
        let input = preparation
            .inputs
            .iter()
            .find(|input| input.import_id == import.import_id)
            .ok_or_else(|| {
                EngineError::Configuration("import was not part of original preparation".into())
            })?;
        let manifest: MemoryArtifactManifest =
            serde_json::from_str(&import.manifest_json).map_err(EngineError::Json)?;
        let typed = if import.typed_map_manifest_json.is_empty() {
            None
        } else {
            Some(
                serde_json::from_str::<MapArtifactManifest>(&import.typed_map_manifest_json)
                    .map_err(EngineError::Json)?,
            )
        };
        if input.consumer.provider_id() != import.consumer_provider_id
            || input.manifest != manifest
            || input.typed_map != typed
        {
            return Err(EngineError::Configuration(
                "prepared import changed its original provider or complete source metadata".into(),
            ));
        }
        if self
            .inner
            .journal
            .cancellation_pending(&import.execution_id)?
        {
            return Err(EngineError::Configuration(
                "prepared consumer was cancelled".into(),
            ));
        }
        if let Some(execution) = self.inner.journal.get(&import.execution_id)?
            && (execution.status().is_terminal()
                || execution.status() == JournalStatus::ReconciliationRequired
                || execution.cancellation_requested())
        {
            return Err(EngineError::Configuration(
                "prepared consumer is stopped or ambiguous".into(),
            ));
        }
        Ok(())
    }

    /// Checks actual current backend content before admitting the first prepared Execute.
    pub(crate) async fn execute_prepared(
        &self,
        execute: &integration::grpc::v0_4::Execute,
    ) -> Result<ExecuteDisposition, EngineError> {
        if execute.command_id != format!("dispatch-{}", execute.execution_id) {
            return Err(EngineError::Protocol(
                "prepared Execute command identity differs from its attempt".into(),
            ));
        }
        self.admit_controller_epoch(&execute.controller_epoch)?;
        let record = self.current_memory_preparation(
            &execute.memory_preparation_id,
            &execute.memory_preparation_digest,
            &execute.session_id,
        )?;
        let requirements: Vec<domain::MemoryRequirement> =
            serde_json::from_str(&execute.memory_requirements_json).map_err(EngineError::Json)?;
        if requirements
            != record
                .preparation
                .inputs
                .iter()
                .map(|input| input.requirement.clone())
                .collect::<Vec<_>>()
        {
            return Err(EngineError::Configuration(
                "Execute prerequisites differ from the original prepared input set".into(),
            ));
        }
        if self.inner.journal.get(&execute.execution_id)?.is_none() {
            self.observe_prepared_memory(&record.preparation).await?;
            if integration::grpc::v0_4::supports_memory_use(self.catalog().node_contract_version())
            {
                self.verify_memory_use_before_execute(&record.preparation)
                    .await?;
            }
        }
        let _gate = self.inner.memory_context_gate.lock().map_err(|_| {
            EngineError::Configuration("Memory context ordering is unavailable".into())
        })?;
        let current = self.current_memory_preparation(
            &execute.memory_preparation_id,
            &execute.memory_preparation_digest,
            &execute.session_id,
        )?;
        if current.preparation.execution_id != execute.execution_id {
            return Err(EngineError::Configuration(
                "prepared Execute attempt differs".into(),
            ));
        }
        self.authorize_current_memory_use(&current.preparation)?;
        self.execute_with_memory_context(
            execute.execution_id.clone(),
            execute
                .invocation
                .clone()
                .ok_or_else(|| EngineError::Protocol("prepared Execute lacks invocation".into()))?,
            execute.resource_ids.clone(),
            &execute.execution_session_json,
            Some(&current.preparation),
        )
    }

    /// Linearizes the last pre-effect admission against Node revocation while retaining raw evidence.
    pub(super) fn authorize_prepared_local_dispatch(
        &self,
        execution_id: &str,
        invocation: &serde_json::Value,
    ) -> Result<(), EngineError> {
        let _gate = self.inner.memory_context_gate.lock().map_err(|_| {
            EngineError::Configuration("Memory context ordering is unavailable".into())
        })?;
        if let Some(memory) = invocation.get("memory_preparation") {
            let id = memory
                .get("preparation_id")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    EngineError::Configuration("prepared dispatch lacks original identity".into())
                })?;
            let digest = memory
                .get("preparation_digest")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    EngineError::Configuration("prepared dispatch lacks original digest".into())
                })?;
            let context = self.inner.journal.memory_preparation(id)?.ok_or_else(|| {
                EngineError::Configuration("prepared dispatch context disappeared".into())
            })?;
            self.current_memory_preparation(id, digest, &context.preparation.source_session_id)?;
            self.authorize_current_memory_use(&context.preparation)?;
        }
        self.inner.journal.authorize_local_dispatch(execution_id)?;
        Ok(())
    }
}
