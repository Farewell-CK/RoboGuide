//! Local execution, artifact finalization, fact reduction, and resource locking.

use super::*;

impl LocalIntegrationEngine {
    /// Starts the one permitted local dispatch and then status polling.
    pub(super) fn spawn_dispatch(
        &self,
        execution_id: String,
        invocation: serde_json::Value,
        capability: CompiledCapability,
    ) {
        let engine = self.clone();
        tokio::spawn(async move {
            let mut context = WorkflowContext::new(invocation.clone());
            let prepared_input = match engine
                .prepare_artifacts(&invocation, &capability, &mut context)
                .await
            {
                Ok(prepared_input) => prepared_input,
                Err(error) => {
                    engine.record_pre_dispatch_failure(&execution_id, error.to_string());
                    return;
                }
            };
            if let Err(error) = engine.inner.journal.authorize_local_dispatch(&execution_id) {
                engine.record_pre_dispatch_failure(&execution_id, error.to_string());
                return;
            }
            if let Err(error) = engine
                .run_steps(capability.workflow().execute(), &mut context)
                .await
            {
                engine.record_ambiguous(&execution_id, error.to_string());
                return;
            }
            let handle = match capability.workflow().local_handle(&context) {
                Ok(handle) => handle,
                Err(error) => {
                    engine.record_ambiguous(&execution_id, error.to_string());
                    return;
                }
            };
            if let Err(error) = engine
                .inner
                .journal
                .record_local_handle(&execution_id, &handle)
            {
                engine.record_ambiguous(&execution_id, error.to_string());
                return;
            }
            if let Err(error) = engine.record_fact(
                &execution_id,
                JournalStatus::Accepted,
                ExecutionPhase::Accepted,
                String::new(),
            ) {
                engine.record_ambiguous(&execution_id, error.to_string());
                return;
            }
            engine.spawn_status_loop(execution_id, invocation, handle, capability, prepared_input);
        });
    }

    /// Polls configured local status until a true terminal fact is observed.
    pub(super) fn spawn_status_loop(
        &self,
        execution_id: String,
        invocation: serde_json::Value,
        handle: String,
        capability: CompiledCapability,
        prepared_input: Option<MapArtifactManifest>,
    ) {
        let engine = self.clone();
        tokio::spawn(async move {
            loop {
                if engine
                    .inner
                    .journal
                    .cancellation_pending(&execution_id)
                    .unwrap_or(false)
                {
                    let _ = engine.cancel(&execution_id);
                }
                let mut context = WorkflowContext::new(invocation.clone());
                context.set_local_handle(handle.clone());
                if let Err(error) = engine
                    .run_steps(capability.workflow().status(), &mut context)
                    .await
                {
                    engine.record_ambiguous(&execution_id, error.to_string());
                    return;
                }
                let fact = match capability.workflow().map_execution_state(&context) {
                    Ok(fact) => fact,
                    Err(error) => {
                        engine.record_ambiguous(&execution_id, error.to_string());
                        return;
                    }
                };
                let phase = fact.phase;
                if phase == MappedExecutionPhase::Completed {
                    if let Err(error) = engine.prepare_artifact_finalization(
                        &invocation,
                        &capability,
                        &execution_id,
                    ) {
                        // The physical execution has already reported Completed. Failure to
                        // durably establish the completion-side write fence cannot turn that
                        // physical fact into a conclusive Task failure.
                        engine.record_ambiguous(&execution_id, error.to_string());
                        return;
                    }
                    if let Err(error) = engine
                        .complete_artifacts(
                            &invocation,
                            &capability,
                            &execution_id,
                            prepared_input.as_ref(),
                        )
                        .await
                    {
                        engine.record_artifact_completion_failure(&execution_id, error);
                        return;
                    }
                }
                match engine.record_mapped_fact(&execution_id, fact) {
                    Ok(true) => {
                        let _ = engine
                            .inner
                            .journal
                            .clear_artifact_finalization(&execution_id);
                        engine.release_locks(&execution_id);
                        return;
                    }
                    Ok(false) => {}
                    Err(error) => {
                        engine.record_ambiguous(&execution_id, error.to_string());
                        return;
                    }
                }
                tokio::time::sleep(std::time::Duration::from_millis(
                    capability.workflow().poll_interval_ms(),
                ))
                .await;
            }
        });
    }

    /// Executes ordered fixed-route steps and retains each final structured response.
    pub(super) async fn run_steps(
        &self,
        steps: &[crate::CompiledWorkflowStep],
        context: &mut WorkflowContext,
    ) -> Result<(), EngineError> {
        for step in steps {
            let request = step.render(&self.inner.catalog, context)?;
            let driver = self
                .inner
                .drivers
                .get(&request.driver_kind())
                .ok_or_else(|| EngineError::Configuration("driver unavailable".to_string()))?;
            let mut response = driver.invoke(&request).await?.events;
            let mut last = None;
            while let Some(event) = response.recv().await {
                last = Some(event?.payload);
            }
            context.record_step(
                step.id(),
                last.ok_or_else(|| {
                    EngineError::Protocol("local call returned no response".into())
                })?,
            )?;
        }
        Ok(())
    }

    /// Stages the invocation's preallocated input and exposes controlled output paths.
    pub(super) async fn prepare_artifacts(
        &self,
        invocation: &serde_json::Value,
        capability: &CompiledCapability,
        context: &mut WorkflowContext,
    ) -> Result<Option<MapArtifactManifest>, EngineError> {
        let Some(directive) = artifact_directive(invocation, capability.artifact_operation())?
        else {
            return Ok(None);
        };
        let service = self.inner.catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration(
                "artifact directive requires an `artifacts` node configuration".into(),
            )
        })?;
        let stager = self.inner.artifact_stager.as_ref().ok_or_else(|| {
            EngineError::Configuration("artifact service is not initialized".into())
        })?;
        match directive.operation {
            ArtifactOperation::PrepareOutput | ArtifactOperation::Publish => {
                let binding = service
                    .output_bindings()
                    .get(directive.slot)
                    .ok_or_else(|| {
                        EngineError::Configuration(format!(
                            "artifact output binding `{}` is not configured",
                            directive.slot
                        ))
                    })?;
                validate_artifact_binding_reference(
                    directive,
                    &binding.map_id,
                    &binding.revision_id,
                    Some(&binding.spatial_anchor_id),
                )?;
                let output_path = match directive.operation {
                    ArtifactOperation::PrepareOutput => stager.prepare_output_path(binding).await?,
                    ArtifactOperation::Publish => {
                        let prepared = self
                            .inner
                            .journal
                            .prepared_artifact(directive.slot)?
                            .ok_or_else(|| {
                                EngineError::Configuration(format!(
                                    "artifact output binding `{}` has no durable prepared artifact",
                                    directive.slot
                                ))
                            })?;
                        validate_input_manifest_reference(directive, prepared.manifest())?;
                        let prepared = PreparedArtifact {
                            binding_id: prepared.binding_id().to_string(),
                            path: prepared.frozen_path().to_path_buf(),
                            manifest: prepared.manifest().clone(),
                        };
                        // Publish workflows may inspect or transform the prepared bundle locally.
                        // Re-prove it before granting any Local EAIOS dispatch side effect.
                        stager.verify_prepared(binding, &prepared).await?;
                        prepared.path
                    }
                    ArtifactOperation::Import | ArtifactOperation::Verify => {
                        unreachable!("output branch excludes input operations")
                    }
                };
                context.set_artifact_output_path(&binding.id, output_path.display().to_string())?;
                Ok(None)
            }
            ArtifactOperation::Import | ArtifactOperation::Verify => {
                let binding = service
                    .input_bindings()
                    .get(directive.slot)
                    .ok_or_else(|| {
                        EngineError::Configuration(format!(
                            "artifact input binding `{}` is not configured",
                            directive.slot
                        ))
                    })?;
                validate_artifact_binding_reference(
                    directive,
                    &binding.map_id,
                    &binding.revision_id,
                    None,
                )?;
                let staged = stager.stage_input(binding).await?;
                validate_input_manifest_reference(directive, &staged.manifest)?;
                context.set_artifact_input_path(&binding.id, staged.path.display().to_string())?;
                Ok(Some(staged.manifest))
            }
        }
    }

    /// Applies publication or replica evidence before a Completed fact becomes durable.
    pub(super) async fn complete_artifacts(
        &self,
        invocation: &serde_json::Value,
        capability: &CompiledCapability,
        execution_id: &str,
        prepared_input: Option<&MapArtifactManifest>,
    ) -> Result<(), EngineError> {
        let Some(directive) = artifact_directive(invocation, capability.artifact_operation())?
        else {
            return Ok(());
        };
        match directive.operation {
            ArtifactOperation::PrepareOutput => {
                self.freeze_artifact_output(invocation, capability, execution_id, directive)
                    .await
            }
            ArtifactOperation::Publish => self.publish_artifact_output(directive).await,
            ArtifactOperation::Import | ArtifactOperation::Verify => {
                self.record_input_completion(invocation, directive, prepared_input)
                    .await
            }
        }
    }

    /// Durably marks remote artifact work before the first completion-side write.
    pub(super) fn prepare_artifact_finalization(
        &self,
        invocation: &serde_json::Value,
        capability: &CompiledCapability,
        execution_id: &str,
    ) -> Result<(), EngineError> {
        let Some(directive) = artifact_directive(invocation, capability.artifact_operation())?
        else {
            return Ok(());
        };
        let kind = match directive.operation {
            ArtifactOperation::PrepareOutput => return Ok(()),
            ArtifactOperation::Publish => ArtifactFinalizationKind::Publish,
            ArtifactOperation::Import => ArtifactFinalizationKind::Import,
            ArtifactOperation::Verify => ArtifactFinalizationKind::Verify,
        };
        self.inner
            .journal
            .prepare_artifact_finalization(execution_id, kind)?;
        Ok(())
    }

    /// Resumes only durable artifact finalization after an exact Execute retry authorizes it.
    ///
    /// The Local EAIOS execute workflow is never replayed. A process-local guard makes concurrent
    /// identical retries collapse into one idempotent remote finalization attempt.
    pub(super) fn spawn_artifact_finalization_resume(
        &self,
        execution_id: String,
        invocation: serde_json::Value,
        capability: CompiledCapability,
    ) -> Result<(), EngineError> {
        let pending = self
            .inner
            .journal
            .artifact_finalization(&execution_id)?
            .ok_or_else(|| EngineError::ReconciliationRequired(execution_id.clone()))?;
        let directive = artifact_directive(&invocation, capability.artifact_operation())?
            .ok_or_else(|| {
                EngineError::Protocol(
                    "artifact finalization marker exists for a non-artifact capability".to_string(),
                )
            })?;
        let expected = match directive.operation {
            ArtifactOperation::PrepareOutput => {
                return Err(EngineError::Protocol(
                    "prepare-output cannot have remote finalization state".to_string(),
                ));
            }
            ArtifactOperation::Publish => ArtifactFinalizationKind::Publish,
            ArtifactOperation::Import => ArtifactFinalizationKind::Import,
            ArtifactOperation::Verify => ArtifactFinalizationKind::Verify,
        };
        if pending != expected {
            return Err(EngineError::Protocol(
                "artifact finalization kind differs from durable execution intent".to_string(),
            ));
        }
        {
            let mut in_flight = self
                .inner
                .artifact_finalizations_in_flight
                .lock()
                .map_err(|_| EngineError::LockState)?;
            if !in_flight.insert(execution_id.clone()) {
                return Ok(());
            }
        }
        let engine = self.clone();
        tokio::spawn(async move {
            let result = engine
                .complete_artifacts(&invocation, &capability, &execution_id, None)
                .await;
            match result {
                Ok(()) => {
                    if let Err(error) = engine.record_fact(
                        &execution_id,
                        JournalStatus::Completed,
                        ExecutionPhase::Completed,
                        "artifact finalization completed after explicit Execute retry".to_string(),
                    ) {
                        engine.record_ambiguous(&execution_id, error.to_string());
                    } else {
                        let _ = engine
                            .inner
                            .journal
                            .clear_artifact_finalization(&execution_id);
                        engine.release_locks(&execution_id);
                    }
                }
                Err(error) => {
                    engine.record_artifact_resume_failure(&execution_id, error);
                }
            }
            if let Ok(mut in_flight) = engine.inner.artifact_finalizations_in_flight.lock() {
                in_flight.remove(&execution_id);
            }
        });
        Ok(())
    }

    /// Publishes a preallocated output only after the local workflow reports completion.
    pub(super) async fn freeze_artifact_output(
        &self,
        invocation: &serde_json::Value,
        capability: &CompiledCapability,
        execution_id: &str,
        directive: ArtifactDirective<'_>,
    ) -> Result<(), EngineError> {
        let service = self.inner.catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration("artifact service is not configured".into())
        })?;
        let binding = service
            .output_bindings()
            .get(directive.slot)
            .ok_or_else(|| {
                EngineError::Configuration(format!(
                    "artifact output binding `{}` is not configured",
                    directive.slot
                ))
            })?;
        validate_artifact_binding_reference(
            directive,
            &binding.map_id,
            &binding.revision_id,
            Some(&binding.spatial_anchor_id),
        )?;
        let stager = self.inner.artifact_stager.as_ref().ok_or_else(|| {
            EngineError::Configuration("artifact service is not initialized".into())
        })?;
        if let Some(existing) = self.inner.journal.prepared_artifact(directive.slot)? {
            if existing.producer_execution_id() != execution_id {
                return Err(EngineError::Configuration(format!(
                    "artifact output binding `{}` was prepared by another execution",
                    directive.slot
                )));
            }
            validate_input_manifest_reference(directive, existing.manifest())?;
            let prepared = PreparedArtifact {
                binding_id: existing.binding_id().to_string(),
                path: existing.frozen_path().to_path_buf(),
                manifest: existing.manifest().clone(),
            };
            stager.verify_prepared(binding, &prepared).await?;
            return Ok(());
        }
        match self
            .inner
            .journal
            .prepare_artifact_freeze(execution_id, directive.slot)?
        {
            PrepareArtifactFreeze::Start => {}
            PrepareArtifactFreeze::Pending => {
                return Err(EngineError::ReconciliationRequired(format!(
                    "{execution_id}: artifact output preparation is unresolved"
                )));
            }
        }
        let provenance = artifact_provenance(
            invocation,
            execution_id,
            self.inner.catalog.node_id(),
            capability.owner(),
        )?;
        let prepared = stager.freeze_output(binding, &provenance).await?;
        let record = PreparedArtifactRecord::new(
            prepared.binding_id,
            execution_id,
            prepared.path,
            prepared.manifest,
        )?;
        self.inner.journal.record_prepared_artifact(&record)?;
        Ok(())
    }

    /// Publishes only the immutable artifact previously frozen by its build execution.
    pub(super) async fn publish_artifact_output(
        &self,
        directive: ArtifactDirective<'_>,
    ) -> Result<(), EngineError> {
        let service = self.inner.catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration("artifact service is not configured".into())
        })?;
        let binding = service
            .output_bindings()
            .get(directive.slot)
            .ok_or_else(|| {
                EngineError::Configuration(format!(
                    "artifact output binding `{}` is not configured",
                    directive.slot
                ))
            })?;
        validate_artifact_binding_reference(
            directive,
            &binding.map_id,
            &binding.revision_id,
            Some(&binding.spatial_anchor_id),
        )?;
        let record = self
            .inner
            .journal
            .prepared_artifact(directive.slot)?
            .ok_or_else(|| {
                EngineError::Configuration(format!(
                    "artifact output binding `{}` has no durable prepared artifact",
                    directive.slot
                ))
            })?;
        validate_input_manifest_reference(directive, record.manifest())?;
        let prepared = PreparedArtifact {
            binding_id: record.binding_id().to_string(),
            path: record.frozen_path().to_path_buf(),
            manifest: record.manifest().clone(),
        };
        let stager = self.inner.artifact_stager.as_ref().ok_or_else(|| {
            EngineError::Configuration("artifact service is not initialized".into())
        })?;
        stager.publish_prepared(binding, &prepared).await?;
        Ok(())
    }

    /// Records Imported or Verified evidence for a successfully completed input workflow.
    pub(super) async fn record_input_completion(
        &self,
        invocation: &serde_json::Value,
        directive: ArtifactDirective<'_>,
        prepared_input: Option<&MapArtifactManifest>,
    ) -> Result<(), EngineError> {
        let service = self.inner.catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration("artifact service is not configured".into())
        })?;
        let binding = service
            .input_bindings()
            .get(directive.slot)
            .ok_or_else(|| {
                EngineError::Configuration(format!(
                    "artifact input binding `{}` is not configured",
                    directive.slot
                ))
            })?;
        validate_artifact_binding_reference(
            directive,
            &binding.map_id,
            &binding.revision_id,
            None,
        )?;
        let stager = self.inner.artifact_stager.as_ref().ok_or_else(|| {
            EngineError::Configuration("artifact service is not initialized".into())
        })?;
        let fetched;
        let manifest = match prepared_input {
            Some(manifest) => manifest,
            None => {
                fetched = stager.published_input_manifest(binding).await?;
                &fetched
            }
        };
        validate_input_manifest_reference(directive, manifest)?;
        // The local workflow may have completed before a crash, but replica evidence describes
        // durable bytes, not the prior process's memory. Re-prove the exact staged copy on every
        // completion path, especially an explicit finalization-only retry.
        stager.verify_staged_input(binding, manifest).await?;
        let status = match directive.operation {
            ArtifactOperation::Import => ReplicaEvidenceStatus::Imported,
            ArtifactOperation::Verify => ReplicaEvidenceStatus::Verified,
            ArtifactOperation::PrepareOutput | ArtifactOperation::Publish => {
                return Err(EngineError::Configuration(
                    "output artifact operation cannot report input replica evidence".into(),
                ));
            }
        };
        let node_id =
            NodeId::new(self.inner.catalog.node_id().to_string()).map_err(ArtifactError::Domain)?;
        let mission_id = invocation_mission_id(invocation)?;
        if status == ReplicaEvidenceStatus::Imported {
            stager
                .record_replica(
                    manifest,
                    &node_id,
                    &mission_id,
                    ReplicaEvidenceStatus::Staged,
                )
                .await?;
        }
        stager
            .record_replica(manifest, &node_id, &mission_id, status)
            .await?;
        Ok(())
    }

    /// Records and broadcasts a mapped local lifecycle fact if it advanced state.
    pub(super) fn record_mapped_fact(
        &self,
        execution_id: &str,
        fact: MappedExecutionFact,
    ) -> Result<bool, EngineError> {
        let (journal_status, phase, terminal) = match fact.phase {
            MappedExecutionPhase::Accepted => {
                (JournalStatus::Accepted, ExecutionPhase::Accepted, false)
            }
            MappedExecutionPhase::Running => {
                (JournalStatus::Running, ExecutionPhase::Started, false)
            }
            MappedExecutionPhase::Completed => {
                (JournalStatus::Completed, ExecutionPhase::Completed, true)
            }
            MappedExecutionPhase::Failed => (JournalStatus::Failed, ExecutionPhase::Failed, true),
            MappedExecutionPhase::Cancelled => {
                (JournalStatus::Cancelled, ExecutionPhase::Cancelled, true)
            }
        };
        let current = self
            .inner
            .journal
            .get(execution_id)?
            .ok_or_else(|| EngineError::UnknownExecution(execution_id.to_string()))?;
        if current.status() == journal_status {
            return Ok(terminal);
        }
        self.record_fact(
            execution_id,
            journal_status,
            phase,
            fact.reason.unwrap_or_default(),
        )?;
        Ok(terminal)
    }

    /// Persists and publishes one execution fact in journal sequence order.
    pub(super) fn record_fact(
        &self,
        execution_id: &str,
        status: JournalStatus,
        phase: ExecutionPhase,
        reason: String,
    ) -> Result<(), EngineError> {
        let current = self
            .inner
            .journal
            .get(execution_id)?
            .ok_or_else(|| EngineError::UnknownExecution(execution_id.to_string()))?;
        let sequence = current.sequence().saturating_add(1);
        self.inner
            .journal
            .record_status(execution_id, sequence, status, reason.clone())?;
        let _ = self.inner.events.send(LocalExecutionEvent {
            execution_id: execution_id.to_string(),
            sequence,
            phase,
            reason,
        });
        Ok(())
    }

    /// Fences an execution whose physical outcome cannot safely be inferred.
    pub(super) fn record_ambiguous(&self, execution_id: &str, reason: String) {
        let _ = self.record_fact(
            execution_id,
            JournalStatus::ReconciliationRequired,
            ExecutionPhase::Unknown,
            reason,
        );
    }

    /// Records a known failure before any Local EAIOS execution side effect and frees locks.
    pub(super) fn record_pre_dispatch_failure(&self, execution_id: &str, reason: String) {
        self.record_deterministic_failure(execution_id, reason);
    }

    /// Records a conclusive execution failure, clears retry state, and releases every lock.
    pub(super) fn record_deterministic_failure(&self, execution_id: &str, reason: String) {
        match self.record_fact(
            execution_id,
            JournalStatus::Failed,
            ExecutionPhase::Failed,
            reason,
        ) {
            Ok(()) => {
                let _ = self.inner.journal.clear_artifact_finalization(execution_id);
                self.release_locks(execution_id);
            }
            Err(error) => self.record_ambiguous(execution_id, error.to_string()),
        }
    }

    /// Separates deterministic completion failure from an unacknowledged remote write.
    pub(super) fn record_artifact_completion_failure(
        &self,
        execution_id: &str,
        error: EngineError,
    ) {
        if !artifact_error_is_deterministic(&error) {
            self.record_ambiguous(execution_id, error.to_string());
        } else {
            self.record_deterministic_failure(execution_id, error.to_string());
        }
    }

    /// Keeps prior ambiguity fenced when a resume cannot prove or complete artifact state.
    ///
    /// Even a deterministic local validation failure during an explicit retry does not prove the
    /// earlier remote finalization outcome. The pending marker and recovery-required lifecycle
    /// therefore remain intact for operator repair or another exact retry.
    pub(super) fn record_artifact_resume_failure(&self, execution_id: &str, error: EngineError) {
        self.record_ambiguous(execution_id, error.to_string());
    }

    /// Atomically acquires committed resources and configured local locks.
    pub(super) fn acquire_locks(
        &self,
        execution_id: &str,
        capability: &CompiledCapability,
        resource_ids: &[String],
        invocation: &serde_json::Value,
    ) -> Result<(), EngineError> {
        let keys = lock_keys(capability, resource_ids, invocation)?;
        self.acquire_lock_keys(execution_id, keys)
    }

    /// Acquires only durable resource locks when workflow configuration is unavailable.
    pub(super) fn acquire_resource_locks(
        &self,
        execution_id: &str,
        resource_ids: &[String],
    ) -> Result<(), EngineError> {
        let keys = resource_ids
            .iter()
            .map(|resource| format!("resource:{resource}"))
            .collect();
        self.acquire_lock_keys(execution_id, keys)
    }

    /// Atomically inserts a prepared set of local lock keys.
    pub(super) fn acquire_lock_keys(
        &self,
        execution_id: &str,
        keys: BTreeSet<String>,
    ) -> Result<(), EngineError> {
        let mut owners = self
            .inner
            .locks
            .lock()
            .map_err(|_| EngineError::LockState)?;
        if let Some((key, owner)) = keys.iter().find_map(|key| {
            owners
                .get(key)
                .filter(|owner| owner.as_str() != execution_id)
                .map(|owner| (key, owner))
        }) {
            return Err(EngineError::LocalLockConflict {
                key: key.clone(),
                owner: owner.clone(),
            });
        }
        for key in keys {
            owners.insert(key, execution_id.to_string());
        }
        Ok(())
    }

    /// Releases every local lock held by one terminal execution.
    pub(super) fn release_locks(&self, execution_id: &str) {
        if let Ok(mut owners) = self.inner.locks.lock() {
            owners.retain(|_, owner| owner != execution_id);
        }
    }
}
