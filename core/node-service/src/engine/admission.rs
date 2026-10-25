//! Execution and cancellation admission against durable Node authority.

use super::*;

impl LocalIntegrationEngine {
    /// Binds an import to immutable request content and the current provider/workflow configuration.
    pub(crate) fn memory_import_identity(
        &self,
        import: &integration::grpc::v0_4::MemoryImport,
    ) -> Result<String, EngineError> {
        let provider = self
            .catalog()
            .memory_providers()
            .get(&import.consumer_provider_id)
            .ok_or_else(|| {
                EngineError::Configuration("Memory consumer provider is unavailable".into())
            })?;
        let content = integration::memory_import_content_digest(import);
        let mut identity = format!("{content}\n{}", provider.identity_content());
        if let Some(workflow) = provider.import() {
            let connections = workflow
                .steps()
                .iter()
                .map(crate::CompiledWorkflowStep::connection)
                .collect::<std::collections::BTreeSet<_>>();
            for connection in connections {
                identity.push_str(&format!(
                    "\n{:?}",
                    self.catalog()
                        .connections()
                        .get(connection)
                        .ok_or_else(|| EngineError::Configuration(
                            "Memory workflow connection is unavailable".into()
                        ))?
                ));
            }
        }
        if provider.continuity().is_some() {
            identity.push_str(&self.memory_provider_contract_digest(&import.consumer_provider_id)?);
        }
        Ok(digest_text(&identity))
    }

    /// Requires an admitted exact Execute invocation before any Group-scoped provider side effect.
    pub(crate) fn validate_memory_consumer(
        &self,
        import: &integration::grpc::v0_4::MemoryImport,
    ) -> Result<(), EngineError> {
        if !import.memory_preparation_id.is_empty() {
            return self.validate_prepared_memory_import(import);
        }
        let execution = self
            .inner
            .journal
            .get(&import.execution_id)?
            .ok_or_else(|| {
                EngineError::Configuration("Memory consumer Execute is not journaled".into())
            })?;
        if execution.status().is_terminal()
            || execution.status() == crate::JournalStatus::ReconciliationRequired
            || execution.cancellation_requested()
            || self
                .inner
                .journal
                .cancellation_pending(&import.execution_id)?
        {
            return Err(EngineError::Configuration(
                "Memory consumer attempt is stopped, ambiguous or cancelling".into(),
            ));
        }
        let invocation: serde_json::Value =
            serde_json::from_slice(execution.spec().invocation_content())
                .map_err(EngineError::Json)?;
        for (field, value) in [
            ("mission_id", &import.mission_id),
            ("group_id", &import.group_id),
            ("task_id", &import.task_id),
            ("role_id", &import.role_id),
        ] {
            if invocation[field].as_str() != Some(value) {
                return Err(EngineError::Configuration(format!(
                    "Memory consumer {field} differs from admitted Execute"
                )));
            }
        }
        Ok(())
    }
    /// Persists one exact Controller-authorized Memory import identity before local side effects.
    pub fn prepare_memory_import(
        &self,
        import_id: &str,
        command_id: &str,
        identity_digest: &str,
    ) -> Result<crate::PrepareMemoryImport, EngineError> {
        self.inner
            .journal
            .prepare_memory_import(import_id, command_id, identity_digest)
            .map_err(EngineError::Journal)
    }

    /// Persists the latest outcome for one exact selective Memory import.
    pub fn record_memory_import(
        &self,
        import_id: &str,
        command_id: &str,
        identity_digest: &str,
        status: crate::MemoryImportStatus,
        reason: &str,
    ) -> Result<crate::MemoryImportRecord, EngineError> {
        self.inner
            .journal
            .record_memory_import(import_id, command_id, identity_digest, status, reason)
            .map_err(EngineError::Journal)
    }

    /// Persists a replicated Controller epoch before any Execute or Cancel side effect is admitted.
    /// Empty epochs retain standalone Controller compatibility.
    pub fn admit_controller_epoch(&self, epoch: &str) -> Result<(), EngineError> {
        self.inner.journal.admit_controller_epoch(epoch)?;
        Ok(())
    }

    /// Accepts a canonical invocation only when resources and durable identity match.
    pub fn execute(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        resource_ids: Vec<String>,
    ) -> Result<ExecuteDisposition, EngineError> {
        self.execute_with_session(execution_id, invocation, resource_ids, "")
    }

    /// Admits one exact Execute with immutable accepted-plan topology evidence.
    pub fn execute_with_session(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        resource_ids: Vec<String>,
        execution_session_json: &str,
    ) -> Result<ExecuteDisposition, EngineError> {
        self.execute_with_memory_context(
            execution_id,
            invocation,
            resource_ids,
            execution_session_json,
            0,
            None,
        )
    }

    /// Admits an explicit Runtime generation without permitting it to change on replay.
    pub fn execute_with_session_generation(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        resource_ids: Vec<String>,
        execution_session_json: &str,
        execution_generation: u64,
    ) -> Result<ExecuteDisposition, EngineError> {
        self.execute_with_memory_context(
            execution_id,
            invocation,
            resource_ids,
            execution_session_json,
            execution_generation,
            None,
        )
    }

    /// Rejects missing generation only when the peer explicitly negotiated the combined contract.
    pub(crate) fn validate_execution_generation(&self, generation: u64) -> Result<(), EngineError> {
        if integration::grpc::v0_4::supports_safety(self.catalog().node_contract_version())
            && generation == 0
        {
            return Err(EngineError::Protocol(
                "generation-bound Execute requires nonzero original attempt generation".into(),
            ));
        }
        Ok(())
    }

    /// Admits the exact original invocation while retaining the prepared-context identity in SQL.
    pub(super) fn execute_with_memory_context(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        mut resource_ids: Vec<String>,
        execution_session_json: &str,
        execution_generation: u64,
        preparation: Option<&domain::MemoryPreparation>,
    ) -> Result<ExecuteDisposition, EngineError> {
        self.validate_execution_generation(execution_generation)?;
        if (integration::grpc::v0_4::supports_capacity(self.catalog().node_contract_version()))
            == invocation.resource_grants_json.is_empty()
        {
            return Err(EngineError::Protocol(
                "resource grant array must exactly match the negotiated Node contract".into(),
            ));
        }

        validate_invocation_identity(&execution_id, &invocation)?;
        let session = if execution_session_json.is_empty() {
            None
        } else {
            let session: domain::ExecutionSessionDescriptor =
                serde_json::from_str(execution_session_json).map_err(EngineError::Json)?;
            session
                .validate_slot(
                    &MissionId::new(invocation.mission_id.clone())
                        .map_err(|error| EngineError::Protocol(error.to_string()))?,
                    &domain::ExecutionGroupId::new(invocation.group_id.clone())
                        .map_err(|error| EngineError::Protocol(error.to_string()))?,
                    &TaskId::new(invocation.task_id.clone())
                        .map_err(|error| EngineError::Protocol(error.to_string()))?,
                    &domain::RoleId::new(invocation.role_id.clone())
                        .map_err(|error| EngineError::Protocol(error.to_string()))?,
                )
                .map_err(EngineError::Protocol)?;
            Some(session)
        };
        let operation = invocation_operation(&invocation)?;
        let capability = self.operation(&operation)?.clone();
        validate_resources(&self.inner.catalog, &capability, &resource_ids)?;
        resource_ids.sort();
        let normalized_generation =
            if integration::grpc::v0_4::supports_safety(self.catalog().node_contract_version()) {
                execution_generation
            } else {
                0
            };
        let mut invocation_json =
            canonical_invocation_json(&invocation, &resource_ids, normalized_generation)?;
        if let Some(session) = session {
            invocation_json
                .as_object_mut()
                .expect("canonical invocation is an object")
                .insert(
                    "execution_session".to_string(),
                    serde_json::to_value(session).map_err(EngineError::Json)?,
                );
        }
        if let Some(preparation) = preparation {
            if invocation_json != preparation.invocation {
                return Err(EngineError::Configuration(
                    "Execute differs from its immutable prepared invocation".into(),
                ));
            }
            let mut memory = serde_json::json!({"preparation_id":preparation.preparation_id,"preparation_digest":preparation.digest().map_err(EngineError::Configuration)?, "inputs":preparation.inputs});
            if integration::grpc::v0_4::supports_memory_use(self.catalog().node_contract_version())
            {
                memory
                    .as_object_mut()
                    .expect("Memory context object")
                    .extend([
                        (
                            "use_lease_id".into(),
                            serde_json::json!(domain::memory_use_lease_id(
                                &preparation.preparation_id
                            )),
                        ),
                        ("tenant_id".into(), serde_json::json!(preparation.tenant_id)),
                    ]);
            }
            invocation_json
                .as_object_mut()
                .expect("canonical invocation object")
                .insert("memory_preparation".into(), memory);
        }
        let workflow_digest = workflow_digest(&self.inner.catalog, &capability, &invocation_json)?;
        let spec = ExecutionSpec::new(
            serde_json::to_vec(&invocation_json).map_err(EngineError::Json)?,
            workflow_digest,
            resource_ids.clone(),
        )?;

        let _capacity_gate = self
            .inner
            .capacity_admission_gate
            .lock()
            .map_err(|_| EngineError::LockState)?;
        if self.inner.journal.get(&execution_id)?.is_some() {
            return match self.inner.journal.prepare_dispatch(&execution_id, &spec)? {
                PrepareDispatch::Existing(record) => {
                    if record.status() == JournalStatus::ReconciliationRequired
                        && self
                            .inner
                            .journal
                            .artifact_finalization(&execution_id)?
                            .is_some()
                    {
                        self.acquire_locks(
                            &execution_id,
                            &capability,
                            &resource_ids,
                            &invocation_json,
                        )?;
                        self.spawn_artifact_finalization_resume(
                            execution_id.clone(),
                            invocation_json,
                            capability,
                        )?;
                    }
                    Ok(existing_disposition(record))
                }
                PrepareDispatch::Conflict(_) => Err(EngineError::ExecutionConflict(execution_id)),
                PrepareDispatch::Start(_) => unreachable!("existing record cannot start"),
            };
        }

        self.validate_capacity_admission(Some((&execution_id, &invocation_json)))?;
        self.acquire_locks(&execution_id, &capability, &resource_ids, &invocation_json)?;
        let prepared = match self.inner.journal.prepare_dispatch(&execution_id, &spec) {
            Ok(prepared) => prepared,
            Err(error) => {
                self.release_locks(&execution_id);
                return Err(error.into());
            }
        };
        match prepared {
            PrepareDispatch::Start(_) => {
                self.spawn_dispatch(execution_id, invocation_json, capability);
                Ok(ExecuteDisposition::Started)
            }
            PrepareDispatch::Existing(record) => {
                self.release_locks(&execution_id);
                Ok(existing_disposition(record))
            }
            PrepareDispatch::Conflict(_) => {
                self.release_locks(&execution_id);
                Err(EngineError::ExecutionConflict(execution_id))
            }
        }
    }

    /// Submits a configured cancellation workflow without synthesizing terminal state.
    pub fn cancel(&self, execution_id: &str) -> Result<(), EngineError> {
        self.inner.journal.request_cancellation(execution_id)?;
        let Some(record) = self.inner.journal.get(execution_id)? else {
            return Ok(());
        };
        if record.status().is_terminal() {
            self.release_locks(execution_id);
            return Ok(());
        }
        if record.cancellation_requested() {
            return Ok(());
        }
        let Some(handle) = record.local_handle() else {
            return Ok(());
        };
        let invocation = decode_invocation_json(record.spec().invocation_content())?;
        let operation = journal_operation(&invocation)?;
        let capability = self.operation(operation)?.clone();
        {
            let mut in_flight = self
                .inner
                .cancellations_in_flight
                .lock()
                .map_err(|_| EngineError::LockState)?;
            if !in_flight.insert(execution_id.to_string()) {
                return Ok(());
            }
        }
        let engine = self.clone();
        let execution_id = execution_id.to_string();
        let handle = handle.to_string();
        tokio::spawn(async move {
            let mut context = WorkflowContext::new(invocation);
            context.set_local_handle(handle);
            let accepted = engine
                .run_steps(capability.workflow().cancel(), &mut context)
                .await
                .is_ok();
            if accepted {
                let _ = engine
                    .inner
                    .journal
                    .record_cancellation_requested(&execution_id);
            }
            if let Ok(mut in_flight) = engine.inner.cancellations_in_flight.lock() {
                in_flight.remove(&execution_id);
            }
        });
        Ok(())
    }

    /// Returns one configured canonical operation workflow or a stable rejection.
    pub(super) fn operation(&self, operation: &str) -> Result<&CompiledOperation, EngineError> {
        self.inner
            .catalog
            .operations()
            .get(operation)
            .ok_or_else(|| EngineError::UnsupportedCapability(operation.to_string()))
    }
}

/// Replays only an existing lifecycle fact, never inventing one for a live dispatch.
fn existing_disposition(record: JournalExecution) -> ExecuteDisposition {
    snapshot_from_record(record).map_or(
        ExecuteDisposition::DispatchPending,
        ExecuteDisposition::Existing,
    )
}
