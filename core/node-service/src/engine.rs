//! Generic declarative workflow execution with durable identity and local locking.

use crate::local_engine::driver::{DriverKind, LocalDriver};
use crate::{
    ArtifactError, ArtifactFinalizationKind, ArtifactOperationConfig, ArtifactProvenance,
    ArtifactStager, CompiledCapability, CompiledLocalCatalog, ExecutionJournal, ExecutionSpec,
    JournalError, JournalExecution, JournalStatus, LocalHealthState, MappedExecutionFact,
    MappedExecutionPhase, PrepareArtifactFreeze, PrepareDispatch, PreparedArtifact,
    PreparedArtifactRecord, ReplicaEvidenceStatus, WorkflowContext,
};
use domain::{LocalSystemId, MapArtifactManifest, MissionId, NodeId, TaskId, TaskRef, TimestampMs};
use integration::grpc::v0_2::{CanonicalInvocation, ExecutionPhase, ExecutionSnapshot};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::broadcast;

/// One progressive local execution fact independent of a remote transport session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalExecutionEvent {
    /// Stable execution identity.
    pub execution_id: String,
    /// Monotonic execution-local sequence.
    pub sequence: u64,
    /// Canonical lifecycle phase.
    pub phase: ExecutionPhase,
    /// Local diagnostic detail.
    pub reason: String,
}

/// Result of accepting a remote Execute command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecuteDisposition {
    /// Local dispatch was durably authorized exactly once.
    Started,
    /// The same identity already exists and its current snapshot must be replayed.
    Existing(ExecutionSnapshot),
}

/// Cloneable generic Local Integration Engine.
#[derive(Clone)]
pub struct LocalIntegrationEngine {
    /// Shared immutable configuration and process-owned runtime state.
    inner: Arc<EngineInner>,
}

/// Process-owned engine state shared by workflow tasks.
struct EngineInner {
    /// Immutable startup-compiled local catalog.
    catalog: Arc<CompiledLocalCatalog>,
    /// Durable execution identity and lifecycle authority.
    journal: Arc<ExecutionJournal>,
    /// Generic transport drivers keyed by family.
    drivers: BTreeMap<DriverKind, Arc<dyn LocalDriver>>,
    /// Execution-scoped resource and local-lock ownership.
    locks: Mutex<BTreeMap<String, String>>,
    /// Cancellation workflows currently in flight, preventing duplicate local requests.
    cancellations_in_flight: Mutex<BTreeSet<String>>,
    /// Explicit artifact-finalization resumes currently in flight.
    artifact_finalizations_in_flight: Mutex<BTreeSet<String>>,
    /// Process-level fact bus surviving Node Protocol sessions.
    events: broadcast::Sender<LocalExecutionEvent>,
    /// Optional Spatial Memory artifact stager configured independently of Node Protocol.
    artifact_stager: Option<ArtifactStager>,
}

/// Configuration-owned artifact operation reused by validated execution directives.
type ArtifactOperation = ArtifactOperationConfig;

/// Validated artifact directive carried opaquely through Node Protocol parameters.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ArtifactDirective<'a> {
    /// Deployment-owned static binding identity.
    slot: &'a str,
    /// Exact node-side action to perform for the selected binding.
    operation: ArtifactOperation,
    /// Mission-selected logical map identity.
    map_id: &'a str,
    /// Mission-selected immutable revision identity.
    revision_id: &'a str,
    /// Mission-selected fixed spatial anchor.
    spatial_anchor_id: &'a str,
}

impl LocalIntegrationEngine {
    /// Creates an engine and opens its durable SQLite WAL journal.
    pub fn new(
        catalog: CompiledLocalCatalog,
        drivers: impl IntoIterator<Item = Arc<dyn LocalDriver>>,
    ) -> Result<Self, EngineError> {
        std::fs::create_dir_all(catalog.state_directory()).map_err(EngineError::Io)?;
        let artifact_stager = catalog
            .artifact_service()
            .map(ArtifactStager::from_compiled)
            .transpose()
            .map_err(EngineError::Artifact)?;
        let journal_path = catalog.state_directory().join("execution-journal.sqlite3");
        let journal = ExecutionJournal::open(journal_path)?;
        let mut driver_map = BTreeMap::new();
        for driver in drivers {
            if driver_map.insert(driver.kind(), driver).is_some() {
                return Err(EngineError::Configuration(
                    "duplicate local driver implementation".to_string(),
                ));
            }
        }
        for connection in catalog.connections().values() {
            if !driver_map.contains_key(&connection.driver_kind()) {
                return Err(EngineError::Configuration(format!(
                    "no driver installed for connection {}",
                    connection.id()
                )));
            }
        }
        let (events, _) = broadcast::channel(256);
        Ok(Self {
            inner: Arc::new(EngineInner {
                catalog: Arc::new(catalog),
                journal: Arc::new(journal),
                drivers: driver_map,
                locks: Mutex::new(BTreeMap::new()),
                cancellations_in_flight: Mutex::new(BTreeSet::new()),
                artifact_finalizations_in_flight: Mutex::new(BTreeSet::new()),
                events,
                artifact_stager,
            }),
        })
    }

    /// Returns the immutable compiled catalog.
    pub fn catalog(&self) -> &CompiledLocalCatalog {
        &self.inner.catalog
    }

    /// Returns the optional node-owned Spatial Memory stager configured at startup.
    ///
    /// Execution workflows must call this explicit facade for static artifact bindings; the
    /// canonical invocation and Node Protocol remain unaware of local cache paths.
    pub fn artifact_stager(&self) -> Option<&ArtifactStager> {
        self.inner.artifact_stager.as_ref()
    }

    /// Subscribes one Node Protocol session to process-level execution facts.
    pub fn subscribe(&self) -> broadcast::Receiver<LocalExecutionEvent> {
        self.inner.events.subscribe()
    }

    /// Observes every configured Local EAIOS and aggregates a truthful Node heartbeat status.
    pub async fn status(&self) -> integration::grpc::v0_2::NodeStatus {
        let mut tasks = tokio::task::JoinSet::new();
        let checks = self
            .inner
            .catalog
            .health_checks()
            .values()
            .cloned()
            .collect::<Vec<_>>();
        for check in checks {
            let engine = self.clone();
            tasks.spawn(async move {
                let mut context = WorkflowContext::new(serde_json::json!({}));
                let fact = match engine
                    .run_steps(std::slice::from_ref(check.step()), &mut context)
                    .await
                {
                    Ok(()) => check.map(&context).map_err(EngineError::Mapping),
                    Err(error) => Err(error),
                };
                match fact {
                    Ok(fact) => (check.owner().to_string(), fact.state, fact.detail),
                    Err(error) => (
                        check.owner().to_string(),
                        LocalHealthState::Offline,
                        error.to_string(),
                    ),
                }
            });
        }
        let mut facts = Vec::new();
        while let Some(result) = tasks.join_next().await {
            match result {
                Ok(fact) => facts.push(fact),
                Err(error) => facts.push((
                    "health-task".to_string(),
                    LocalHealthState::Offline,
                    error.to_string(),
                )),
            }
        }
        let state = if facts
            .iter()
            .all(|(_, state, _)| *state == LocalHealthState::Online)
        {
            "online"
        } else if facts
            .iter()
            .all(|(_, state, _)| *state == LocalHealthState::Offline)
        {
            "offline"
        } else {
            "degraded"
        };
        let detail = facts
            .into_iter()
            .filter(|(_, state, detail)| *state != LocalHealthState::Online || !detail.is_empty())
            .map(|(owner, state, detail)| format!("{owner}={state:?}:{detail}"))
            .collect::<Vec<_>>()
            .join("; ");
        integration::grpc::v0_2::NodeStatus {
            health: state.to_string(),
            detail,
        }
    }

    /// Returns all durable snapshots for reconnect replay.
    pub fn snapshots(&self) -> Result<Vec<ExecutionSnapshot>, EngineError> {
        self.inner
            .journal
            .replay_records()?
            .into_iter()
            .map(snapshot_from_record)
            .collect()
    }

    /// Recovers status polling for known active executions without redispatching them.
    pub fn recover(&self) -> Result<(), EngineError> {
        for record in self.inner.journal.replay_records()? {
            if !matches!(
                record.status(),
                JournalStatus::Accepted
                    | JournalStatus::Running
                    | JournalStatus::ReconciliationRequired
            ) {
                continue;
            }
            let invocation = decode_invocation_json(record.spec().invocation_content())?;
            let contract = invocation
                .get("capability_contract")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| EngineError::Protocol("journal invocation lacks contract".into()))?;
            let capability = self.capability(contract)?.clone();
            let current_workflow_digest =
                workflow_digest(&self.inner.catalog, &capability, &invocation)?;
            if current_workflow_digest != record.spec().workflow_digest() {
                self.acquire_resource_locks(record.execution_id(), record.spec().resource_ids())?;
                if record.status() != JournalStatus::ReconciliationRequired {
                    self.inner.journal.record_status(
                        record.execution_id(),
                        record.sequence().saturating_add(1),
                        JournalStatus::ReconciliationRequired,
                        "local workflow or artifact binding changed while execution was active",
                    )?;
                }
                return Err(EngineError::ReconciliationRequired(format!(
                    "{}: startup configuration drift prevents complete lock recovery",
                    record.execution_id()
                )));
            }
            self.acquire_locks(
                record.execution_id(),
                &capability,
                record.spec().resource_ids(),
                &invocation,
            )?;
            // A pending artifact finalization is an external recovery decision boundary. The
            // physical Local EAIOS execution may be status-polled only after its handle is known,
            // but publication/replica evidence must not be retried implicitly during restart.
            // The exact immutable Execute request below is the explicit authorization path.
            if self
                .inner
                .journal
                .artifact_finalization(record.execution_id())?
                .is_some()
            {
                if record.status() != JournalStatus::ReconciliationRequired {
                    self.inner.journal.record_status(
                        record.execution_id(),
                        record.sequence().saturating_add(1),
                        JournalStatus::ReconciliationRequired,
                        "artifact finalization requires explicit recovery authorization",
                    )?;
                }
                continue;
            }
            // An unresolved output preparation proves that this execution already consumed its
            // one mutable-source read. Status polling could observe Completed again and freeze
            // different bytes, so only external reconciliation may resolve this fence.
            if self
                .inner
                .journal
                .artifact_preparation(record.execution_id())?
                .is_some()
            {
                if record.status() != JournalStatus::ReconciliationRequired {
                    self.inner.journal.record_status(
                        record.execution_id(),
                        record.sequence().saturating_add(1),
                        JournalStatus::ReconciliationRequired,
                        "artifact output preparation requires explicit reconciliation",
                    )?;
                }
                continue;
            }
            // A handle-bearing Dispatching row is conservatively fenced during journal open,
            // but polling that handle is safe because it never replays Local EAIOS execute. Rows
            // without a handle still require an external reconciliation decision.
            if record.status() == JournalStatus::ReconciliationRequired
                && record.local_handle().is_none()
            {
                continue;
            }
            let Some(handle) = record.local_handle() else {
                return Err(EngineError::ReconciliationRequired(
                    record.execution_id().to_string(),
                ));
            };
            self.spawn_status_loop(
                record.execution_id().to_string(),
                invocation,
                handle.to_string(),
                capability,
                None,
            );
        }
        Ok(())
    }

    /// Accepts a canonical invocation only when resources and durable identity match.
    pub fn execute(
        &self,
        execution_id: String,
        invocation: CanonicalInvocation,
        mut resource_ids: Vec<String>,
    ) -> Result<ExecuteDisposition, EngineError> {
        validate_invocation_identity(&execution_id, &invocation)?;
        let contract = invocation.capability_contract.as_str();
        let capability = self.capability(contract)?.clone();
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
        let record = self
            .inner
            .journal
            .get(execution_id)?
            .ok_or_else(|| EngineError::UnknownExecution(execution_id.to_string()))?;
        if record.status().is_terminal() {
            return Ok(());
        }
        if record.cancellation_requested() {
            return Ok(());
        }
        let handle = record
            .local_handle()
            .ok_or_else(|| EngineError::ReconciliationRequired(execution_id.to_string()))?;
        let invocation = decode_invocation_json(record.spec().invocation_content())?;
        let contract = invocation
            .get("capability_contract")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| EngineError::Protocol("journal invocation lacks contract".into()))?;
        let capability = self.capability(contract)?.clone();
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

    /// Returns one configured canonical capability or a stable rejection.
    fn capability(&self, contract: &str) -> Result<&CompiledCapability, EngineError> {
        self.inner
            .catalog
            .capabilities()
            .get(contract)
            .ok_or_else(|| EngineError::UnsupportedCapability(contract.to_string()))
    }

    /// Starts the one permitted local dispatch and then status polling.
    fn spawn_dispatch(
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
    fn spawn_status_loop(
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
    async fn run_steps(
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
    async fn prepare_artifacts(
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
    async fn complete_artifacts(
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
    fn prepare_artifact_finalization(
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
    fn spawn_artifact_finalization_resume(
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
    async fn freeze_artifact_output(
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
    async fn publish_artifact_output(
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
    async fn record_input_completion(
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
    fn record_mapped_fact(
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
    fn record_fact(
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
    fn record_ambiguous(&self, execution_id: &str, reason: String) {
        let _ = self.record_fact(
            execution_id,
            JournalStatus::ReconciliationRequired,
            ExecutionPhase::Unknown,
            reason,
        );
    }

    /// Records a known failure before any Local EAIOS execution side effect and frees locks.
    fn record_pre_dispatch_failure(&self, execution_id: &str, reason: String) {
        self.record_deterministic_failure(execution_id, reason);
    }

    /// Records a conclusive execution failure, clears retry state, and releases every lock.
    fn record_deterministic_failure(&self, execution_id: &str, reason: String) {
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
    fn record_artifact_completion_failure(&self, execution_id: &str, error: EngineError) {
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
    fn record_artifact_resume_failure(&self, execution_id: &str, error: EngineError) {
        self.record_ambiguous(execution_id, error.to_string());
    }

    /// Atomically acquires committed resources and configured local locks.
    fn acquire_locks(
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
    fn acquire_resource_locks(
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
    fn acquire_lock_keys(
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
    fn release_locks(&self, execution_id: &str) {
        if let Ok(mut owners) = self.inner.locks.lock() {
            owners.retain(|_, owner| owner != execution_id);
        }
    }
}

/// Returns whether an artifact completion error conclusively proves no successful finalization.
fn artifact_error_is_deterministic(error: &EngineError) -> bool {
    match error {
        EngineError::Artifact(ArtifactError::Status { status, .. }) => {
            !matches!(status.as_u16(), 408 | 425 | 429 | 500..=599)
        }
        EngineError::Artifact(error) => !matches!(
            error,
            ArtifactError::RemoteOutcomeUnknown { .. } | ArtifactError::Http(_)
        ),
        EngineError::Protocol(_)
        | EngineError::Configuration(_)
        | EngineError::UnsupportedCapability(_)
        | EngineError::MissingCommittedResource
        | EngineError::ExecutionConflict(_)
        | EngineError::LocalLockConflict { .. }
        | EngineError::UnknownExecution(_)
        | EngineError::Catalog(_)
        | EngineError::Mapping(_)
        | EngineError::Json(_) => true,
        EngineError::ReconciliationRequired(_)
        | EngineError::LockState
        | EngineError::Io(_)
        | EngineError::Journal(_)
        | EngineError::Driver(_) => false,
    }
}

/// Validates canonical command identity before any lock or local side effect.
fn validate_invocation_identity(
    execution_id: &str,
    invocation: &CanonicalInvocation,
) -> Result<(), EngineError> {
    if execution_id.trim().is_empty()
        || invocation.mission_id.trim().is_empty()
        || invocation.task_id.trim().is_empty()
        || invocation.group_id.trim().is_empty()
        || invocation.role_id.trim().is_empty()
        || invocation.capability_contract.trim().is_empty()
    {
        Err(EngineError::Protocol(
            "execution and canonical invocation identities must be nonblank".to_string(),
        ))
    } else {
        Ok(())
    }
}

/// Parses and validates the complete artifact intent fixed by capability configuration.
fn artifact_directive<'a>(
    invocation: &'a serde_json::Value,
    expected_operation: Option<ArtifactOperation>,
) -> Result<Option<ArtifactDirective<'a>>, EngineError> {
    let parameters = invocation
        .get("parameters")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| EngineError::Protocol("invocation parameters must be an object".into()))?;
    let artifact_fields = [
        "artifact_slot",
        "artifact_operation",
        "map_id",
        "revision_id",
        "spatial_anchor_id",
    ];
    let carries_artifact_intent = artifact_fields
        .iter()
        .any(|field| parameters.contains_key(*field));
    match (expected_operation, carries_artifact_intent) {
        (None, false) => return Ok(None),
        (None, true) => {
            return Err(EngineError::Protocol(
                "capability does not permit artifact parameters".to_string(),
            ));
        }
        (Some(_), false) => {
            return Err(EngineError::Protocol(
                "artifact capability requires artifact_slot, artifact_operation, map_id, revision_id, and spatial_anchor_id"
                    .to_string(),
            ));
        }
        (Some(_), true) => {}
    }
    let required_string = |field: &'static str| -> Result<&'a str, EngineError> {
        parameters
            .get(field)
            .and_then(serde_json::Value::as_str)
            .filter(|value| !value.trim().is_empty() && *value == value.trim())
            .ok_or_else(|| {
                EngineError::Protocol(format!("artifact intent requires nonblank string {field}"))
            })
    };
    let slot = parameters
        .get("artifact_slot")
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty() && *value == value.trim())
        .ok_or_else(|| {
            EngineError::Protocol(
                "artifact_slot must be a nonblank string when an artifact operation is used".into(),
            )
        })?;
    let operation = match required_string("artifact_operation")? {
        "prepare-output" => ArtifactOperation::PrepareOutput,
        "publish" => ArtifactOperation::Publish,
        "import" => ArtifactOperation::Import,
        "verify" => ArtifactOperation::Verify,
        _ => {
            return Err(EngineError::Protocol(
                "artifact_operation must be prepare-output/publish/import/verify".into(),
            ));
        }
    };
    if Some(operation) != expected_operation {
        return Err(EngineError::Protocol(format!(
            "artifact_operation {} differs from capability-configured {}",
            operation.as_str(),
            expected_operation
                .expect("artifact operation was required above")
                .as_str()
        )));
    }
    Ok(Some(ArtifactDirective {
        slot,
        operation,
        map_id: required_string("map_id")?,
        revision_id: required_string("revision_id")?,
        spatial_anchor_id: required_string("spatial_anchor_id")?,
    }))
}

/// Returns the typed Mission identity carried by one canonical invocation.
fn invocation_mission_id(invocation: &serde_json::Value) -> Result<MissionId, EngineError> {
    let mission_id = invocation
        .get("mission_id")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| EngineError::Protocol("invocation mission_id is missing".into()))?;
    MissionId::new(mission_id.to_string())
        .map_err(ArtifactError::Domain)
        .map_err(EngineError::Artifact)
}

/// Builds immutable output provenance from the execution that produced the bytes.
fn artifact_provenance(
    invocation: &serde_json::Value,
    execution_id: &str,
    node_id: &str,
    local_system_id: &str,
) -> Result<ArtifactProvenance, EngineError> {
    let mission = invocation_mission_id(invocation)?;
    let task_id = invocation
        .get("task_id")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| EngineError::Protocol("invocation task_id is missing".to_string()))?;
    let task_ref = TaskRef::new(
        mission.clone(),
        TaskId::new(task_id.to_string()).map_err(ArtifactError::Domain)?,
    );
    Ok(ArtifactProvenance {
        producer_node_id: NodeId::new(node_id.to_string()).map_err(ArtifactError::Domain)?,
        producer_local_system_id: Some(
            LocalSystemId::new(local_system_id.to_string()).map_err(ArtifactError::Domain)?,
        ),
        source_mission_id: mission,
        source_execution_id: Some(execution_id.to_string()),
        source_task_ref: Some(task_ref),
        created_at: TimestampMs::new(current_timestamp_ms()),
        parent_revision_id: None,
    })
}

/// Confirms canonical map/revision parameters select the configured immutable binding.
fn validate_artifact_binding_reference(
    directive: ArtifactDirective<'_>,
    expected_map_id: &str,
    expected_revision_id: &str,
    expected_anchor_id: Option<&str>,
) -> Result<(), EngineError> {
    if directive.map_id != expected_map_id || directive.revision_id != expected_revision_id {
        return Err(EngineError::Protocol(format!(
            "artifact intent {}/{} differs from configured binding {expected_map_id}/{expected_revision_id}",
            directive.map_id, directive.revision_id
        )));
    }
    if let Some(expected_anchor_id) = expected_anchor_id
        && directive.spatial_anchor_id != expected_anchor_id
    {
        return Err(EngineError::Protocol(format!(
            "artifact intent anchor {} differs from configured binding {}",
            directive.spatial_anchor_id, expected_anchor_id
        )));
    }
    Ok(())
}

/// Confirms an input manifest matches the intent's selector and fixed spatial anchor.
fn validate_input_manifest_reference(
    directive: ArtifactDirective<'_>,
    manifest: &MapArtifactManifest,
) -> Result<(), EngineError> {
    if manifest.selector().map_id().as_str() != directive.map_id
        || manifest.selector().revision_id().as_str() != directive.revision_id
        || manifest.anchor_id().as_str() != directive.spatial_anchor_id
    {
        return Err(EngineError::Protocol(
            "artifact manifest selector or spatial anchor differs from execution intent"
                .to_string(),
        ));
    }
    Ok(())
}

/// Returns the local wall-clock millisecond used for artifact provenance evidence.
fn current_timestamp_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default()
}

/// Validates Control commitments against the compiled node resource catalog.
fn validate_resources(
    catalog: &CompiledLocalCatalog,
    capability: &CompiledCapability,
    resource_ids: &[String],
) -> Result<(), EngineError> {
    let supplied = resource_ids.iter().collect::<BTreeSet<_>>();
    if supplied.len() != resource_ids.len() {
        return Err(EngineError::Protocol(
            "Execute resource IDs must be unique".to_string(),
        ));
    }
    if supplied
        .iter()
        .any(|resource_id| !catalog.resources().contains_key(resource_id.as_str()))
    {
        return Err(EngineError::Protocol(
            "Execute references an unknown node resource".to_string(),
        ));
    }
    if !capability
        .required_resources()
        .iter()
        .all(|required| supplied.contains(required))
    {
        return Err(EngineError::MissingCommittedResource);
    }
    Ok(())
}

/// Builds deterministic local lock keys for one execution, including its artifact binding.
fn lock_keys(
    capability: &CompiledCapability,
    resource_ids: &[String],
    invocation: &serde_json::Value,
) -> Result<BTreeSet<String>, EngineError> {
    let mut keys = resource_ids
        .iter()
        .map(|resource| format!("resource:{resource}"))
        .chain(
            capability
                .local_locks()
                .iter()
                .map(|lock| format!("local:{lock}")),
        )
        .collect::<BTreeSet<_>>();
    if let Some(directive) = artifact_directive(invocation, capability.artifact_operation())? {
        keys.insert(format!("artifact-binding:{}", directive.slot));
    }
    Ok(keys)
}

/// Converts a canonical protobuf invocation into stable JSON mapping context.
fn canonical_invocation_json(
    invocation: &CanonicalInvocation,
    resource_ids: &[String],
) -> Result<serde_json::Value, EngineError> {
    let parameters = invocation
        .parameters
        .iter()
        .map(|(name, value)| {
            let value = value
                .value
                .as_ref()
                .ok_or_else(|| EngineError::Protocol("invocation scalar is empty".to_string()))?;
            use integration::grpc::v0_2::scalar_value::Value;
            let value = match value {
                Value::BoolValue(value) => serde_json::Value::Bool(*value),
                Value::IntegerValue(value) => serde_json::Value::Number((*value).into()),
                Value::FloatValue(value) => serde_json::Number::from_f64(*value)
                    .map(serde_json::Value::Number)
                    .ok_or_else(|| {
                        EngineError::Protocol("invocation float is not finite".into())
                    })?,
                Value::StringValue(value) => serde_json::Value::String(value.clone()),
            };
            Ok((name.clone(), value))
        })
        .collect::<Result<BTreeMap<_, _>, EngineError>>()?;
    Ok(serde_json::json!({
        "mission_id": invocation.mission_id,
        "task_id": invocation.task_id,
        "group_id": invocation.group_id,
        "role_id": invocation.role_id,
        "capability_contract": invocation.capability_contract,
        "parameters": parameters,
        "resource_ids": resource_ids,
    }))
}

/// Decodes canonical JSON retained in the durable journal.
fn decode_invocation_json(bytes: &[u8]) -> Result<serde_json::Value, EngineError> {
    serde_json::from_slice(bytes).map_err(EngineError::Json)
}

/// Converts one journal record into a Node Protocol reconnect snapshot.
fn snapshot_from_record(record: JournalExecution) -> Result<ExecutionSnapshot, EngineError> {
    let phase = match record.status() {
        JournalStatus::Dispatching | JournalStatus::ReconciliationRequired => {
            ExecutionPhase::Unknown
        }
        JournalStatus::Accepted => ExecutionPhase::Accepted,
        JournalStatus::Running => ExecutionPhase::Started,
        JournalStatus::Completed => ExecutionPhase::Completed,
        JournalStatus::Failed => ExecutionPhase::Failed,
        JournalStatus::Cancelled => ExecutionPhase::Cancelled,
    };
    Ok(ExecutionSnapshot {
        session_id: String::new(),
        execution_id: record.execution_id().to_string(),
        last_sequence: record.sequence(),
        phase: phase as i32,
        reason: record.reason().to_string(),
    })
}

/// Computes a stable lowercase SHA-256 digest for workflow identity.
fn digest_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

/// Hashes capability behavior, referenced connections, and selected artifact metadata.
pub(crate) fn workflow_digest(
    catalog: &CompiledLocalCatalog,
    capability: &CompiledCapability,
    invocation: &serde_json::Value,
) -> Result<String, EngineError> {
    let mut identity = format!("{capability:?}");
    let connection_ids = capability
        .workflow()
        .execute()
        .iter()
        .chain(capability.workflow().status())
        .chain(capability.workflow().cancel())
        .map(crate::CompiledWorkflowStep::connection)
        .collect::<BTreeSet<_>>();
    for connection_id in connection_ids {
        let connection = catalog.connections().get(connection_id).ok_or_else(|| {
            EngineError::Configuration(format!(
                "workflow references unavailable connection {connection_id}"
            ))
        })?;
        identity.push_str(&format!("\n{connection:?}"));
    }
    if let Some(directive) = artifact_directive(invocation, capability.artifact_operation())? {
        let artifacts = catalog.artifact_service().ok_or_else(|| {
            EngineError::Configuration(
                "artifact capability requires configured artifact service".to_string(),
            )
        })?;
        identity.push_str(&format!(
            "\nartifact-service:{:?}:{}:{}:{}:{}:{}\nartifact-intent:{}:{}:{}:{}:{}",
            artifacts.cache_directory(),
            artifacts.endpoint(),
            artifacts.max_artifact_bytes(),
            artifacts.chunk_size_bytes(),
            artifacts.connect_timeout_ms(),
            artifacts.read_timeout_ms(),
            directive.operation.as_str(),
            directive.slot,
            directive.map_id,
            directive.revision_id,
            directive.spatial_anchor_id,
        ));
        match directive.operation {
            ArtifactOperation::PrepareOutput | ArtifactOperation::Publish => {
                let binding = artifacts
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
                identity.push_str(&format!("\nartifact-output:{binding:?}"));
            }
            ArtifactOperation::Import | ArtifactOperation::Verify => {
                let binding = artifacts
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
                identity.push_str(&format!("\nartifact-input:{binding:?}"));
            }
        }
    }
    Ok(digest_text(&identity))
}

/// Generic local execution failure.
#[derive(Debug)]
pub enum EngineError {
    /// Startup configuration or driver installation is incomplete.
    Configuration(String),
    /// A canonical capability has no configured owner/workflow.
    UnsupportedCapability(String),
    /// Control did not commit every configured required resource.
    MissingCommittedResource,
    /// The same execution identity was reused for another semantic tuple.
    ExecutionConflict(String),
    /// A local resource or lock is owned by another active execution.
    LocalLockConflict {
        /// Conflicting lock identity.
        key: String,
        /// Execution currently owning it.
        owner: String,
    },
    /// Execution requires explicit reconciliation before any further action.
    ReconciliationRequired(String),
    /// Execution identity is unknown locally.
    UnknownExecution(String),
    /// Local lock state was poisoned.
    LockState,
    /// Local state directory could not be created.
    Io(std::io::Error),
    /// Durable journal rejected an operation.
    Journal(JournalError),
    /// Compiled catalog failed to render a request.
    Catalog(crate::CatalogError),
    /// Mapping evaluation failed.
    Mapping(crate::MappingError),
    /// Local driver failed without implied retry safety.
    Driver(crate::DriverError),
    /// Spatial Memory artifact staging configuration or transfer failed.
    Artifact(ArtifactError),
    /// Canonical JSON encoding or decoding failed.
    Json(serde_json::Error),
    /// Protocol fact was structurally invalid.
    Protocol(String),
}

impl Display for EngineError {
    /// Formats a stable engine diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Configuration(reason) | Self::Protocol(reason) => formatter.write_str(reason),
            Self::UnsupportedCapability(contract) => {
                write!(formatter, "unsupported canonical capability {contract}")
            }
            Self::MissingCommittedResource => {
                formatter.write_str("required resource is not committed")
            }
            Self::ExecutionConflict(id) => {
                write!(formatter, "execution {id} has conflicting identity")
            }
            Self::LocalLockConflict { key, owner } => {
                write!(formatter, "local lock {key} is owned by execution {owner}")
            }
            Self::ReconciliationRequired(id) => {
                write!(formatter, "execution {id} requires reconciliation")
            }
            Self::UnknownExecution(id) => write!(formatter, "unknown execution {id}"),
            Self::LockState => formatter.write_str("local lock state unavailable"),
            Self::Io(error) => error.fmt(formatter),
            Self::Journal(error) => error.fmt(formatter),
            Self::Catalog(error) => error.fmt(formatter),
            Self::Mapping(error) => error.fmt(formatter),
            Self::Driver(error) => error.fmt(formatter),
            Self::Artifact(error) => error.fmt(formatter),
            Self::Json(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for EngineError {}
impl From<JournalError> for EngineError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}
impl From<crate::CatalogError> for EngineError {
    fn from(value: crate::CatalogError) -> Self {
        Self::Catalog(value)
    }
}
impl From<crate::MappingError> for EngineError {
    fn from(value: crate::MappingError) -> Self {
        Self::Mapping(value)
    }
}
impl From<crate::DriverError> for EngineError {
    fn from(value: crate::DriverError) -> Self {
        Self::Driver(value)
    }
}

impl From<ArtifactError> for EngineError {
    /// Converts a node-local artifact failure into the engine error boundary.
    fn from(value: ArtifactError) -> Self {
        Self::Artifact(value)
    }
}

/// Returns the fixed journal path for diagnostics and tests.
pub fn journal_path(state_directory: &std::path::Path) -> PathBuf {
    state_directory.join("execution-journal.sqlite3")
}

#[cfg(test)]
mod artifact_directive_tests {
    use super::*;

    /// Builds the canonical JSON shape used by the durable execution journal.
    fn invocation(parameters: serde_json::Value) -> serde_json::Value {
        serde_json::json!({
            "mission_id": "mission-a",
            "task_id": "task-a",
            "group_id": "group-a",
            "role_id": "role-a",
            "capability_contract": "spatial.map.import@v0",
            "parameters": parameters,
            "resource_ids": []
        })
    }

    /// Explicit operations select exactly one side of a static artifact binding.
    #[test]
    fn parses_explicit_artifact_operations() {
        for (wire, expected) in [
            ("prepare-output", ArtifactOperation::PrepareOutput),
            ("publish", ArtifactOperation::Publish),
            ("import", ArtifactOperation::Import),
            ("verify", ArtifactOperation::Verify),
        ] {
            let value = invocation(serde_json::json!({
                "artifact_slot": "lab-r1",
                "artifact_operation": wire,
                "map_id": "lab",
                "revision_id": "r1",
                "spatial_anchor_id": "lab-origin"
            }));
            let directive = artifact_directive(&value, Some(expected))
                .expect("directive parses")
                .expect("directive exists");
            assert_eq!(directive.slot, "lab-r1");
            assert_eq!(directive.operation, expected);
        }
    }

    /// A slot alone cannot implicitly activate both input and output behavior.
    #[test]
    fn rejects_implicit_or_incomplete_artifact_directives() {
        let slot_only = invocation(serde_json::json!({"artifact_slot": "lab-r1"}));
        assert!(matches!(
            artifact_directive(&slot_only, Some(ArtifactOperation::Import)),
            Err(EngineError::Protocol(_))
        ));
        let operation_only = invocation(serde_json::json!({"artifact_operation": "import"}));
        assert!(matches!(
            artifact_directive(&operation_only, Some(ArtifactOperation::Import)),
            Err(EngineError::Protocol(_))
        ));
        let unknown = invocation(serde_json::json!({
            "artifact_slot": "lab-r1",
            "artifact_operation": "auto",
            "map_id": "lab",
            "revision_id": "r1",
            "spatial_anchor_id": "lab-origin"
        }));
        assert!(matches!(
            artifact_directive(&unknown, Some(ArtifactOperation::Import)),
            Err(EngineError::Protocol(_))
        ));
        let wrong_operation = invocation(serde_json::json!({
            "artifact_slot": "lab-r1",
            "artifact_operation": "publish",
            "map_id": "lab",
            "revision_id": "r1",
            "spatial_anchor_id": "lab-origin"
        }));
        assert!(matches!(
            artifact_directive(&wrong_operation, Some(ArtifactOperation::Import)),
            Err(EngineError::Protocol(_))
        ));
    }

    /// Canonical selectors cannot silently diverge from deployment-owned binding metadata.
    #[test]
    fn validates_artifact_selector_against_static_binding() {
        let value = invocation(serde_json::json!({
            "map_id": "lab",
            "revision_id": "r1",
            "artifact_slot": "lab-r1",
            "artifact_operation": "import",
            "spatial_anchor_id": "lab-origin"
        }));
        let directive = artifact_directive(&value, Some(ArtifactOperation::Import))
            .expect("directive parses")
            .expect("directive exists");
        validate_artifact_binding_reference(directive, "lab", "r1", None)
            .expect("matching selector is accepted");
        assert!(matches!(
            validate_artifact_binding_reference(directive, "other", "r1", None),
            Err(EngineError::Protocol(_))
        ));
        assert!(matches!(
            validate_artifact_binding_reference(directive, "lab", "r1", Some("different-origin")),
            Err(EngineError::Protocol(_))
        ));
    }

    /// An execution without artifact parameters remains a normal generic workflow.
    #[test]
    fn leaves_non_artifact_invocations_unchanged() {
        let value = invocation(serde_json::json!({"distance": 1}));
        assert_eq!(artifact_directive(&value, None).expect("parses"), None);
    }

    /// Capabilities without an artifact contract reject every artifact-shaped parameter.
    #[test]
    fn rejects_artifact_parameters_for_generic_capability() {
        let value = invocation(serde_json::json!({"map_id": "lab"}));
        assert!(matches!(
            artifact_directive(&value, None),
            Err(EngineError::Protocol(_))
        ));
    }

    /// Transient artifact HTTP statuses retain the recovery fence after physical completion.
    #[test]
    fn classifies_retryable_artifact_statuses_as_ambiguous() {
        for status in [408, 425, 429, 500, 503, 599] {
            let error = EngineError::Artifact(ArtifactError::Status {
                status: reqwest::StatusCode::from_u16(status).expect("status is valid"),
                endpoint: "http://artifact.test".to_string(),
            });
            assert!(!artifact_error_is_deterministic(&error));
        }
    }

    /// Conclusive client rejections remain deterministic artifact completion failures.
    #[test]
    fn classifies_nonretryable_artifact_statuses_as_deterministic() {
        for status in [400, 404, 409, 422] {
            let error = EngineError::Artifact(ArtifactError::Status {
                status: reqwest::StatusCode::from_u16(status).expect("status is valid"),
                endpoint: "http://artifact.test".to_string(),
            });
            assert!(artifact_error_is_deterministic(&error));
        }
    }
}
