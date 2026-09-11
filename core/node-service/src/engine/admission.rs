//! Execution and cancellation admission against durable Node authority.

use super::*;

impl LocalIntegrationEngine {
    /// Accepts a canonical invocation only when resources and durable identity match.
    pub fn execute(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        mut resource_ids: Vec<String>,
    ) -> Result<ExecuteDisposition, EngineError> {
        validate_invocation_identity(&execution_id, &invocation)?;
        let operation = invocation_operation(&invocation)?;
        let capability = self.operation(&operation)?.clone();
        validate_resources(&self.inner.catalog, &capability, &resource_ids)?;
        resource_ids.sort();
        let invocation_json = canonical_invocation_json(&invocation, &resource_ids)?;
        let workflow_digest = workflow_digest(&self.inner.catalog, &capability, &invocation_json)?;
        let spec = ExecutionSpec::new(
            serde_json::to_vec(&invocation_json).map_err(EngineError::Json)?,
            workflow_digest,
            resource_ids.clone(),
        )?;

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
                    Ok(ExecuteDisposition::Existing(snapshot_from_record(record)?))
                }
                PrepareDispatch::Conflict(_) => Err(EngineError::ExecutionConflict(execution_id)),
                PrepareDispatch::Start(_) => unreachable!("existing record cannot start"),
            };
        }

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
                Ok(ExecuteDisposition::Existing(snapshot_from_record(record)?))
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
