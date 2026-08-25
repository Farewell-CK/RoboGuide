//! Generic declarative workflow execution with durable identity and local locking.

use crate::local_engine::driver::{DriverKind, LocalDriver};
use crate::{
    CompiledCapability, CompiledLocalCatalog, ExecutionJournal, ExecutionSpec, JournalError,
    JournalExecution, JournalStatus, LocalHealthState, MappedExecutionFact, MappedExecutionPhase,
    PrepareDispatch, WorkflowContext,
};
use integration::grpc::v0_2::{CanonicalInvocation, ExecutionPhase, ExecutionSnapshot};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
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
    /// Process-level fact bus surviving Node Protocol sessions.
    events: broadcast::Sender<LocalExecutionEvent>,
}

impl LocalIntegrationEngine {
    /// Creates an engine and opens its durable SQLite WAL journal.
    pub fn new(
        catalog: CompiledLocalCatalog,
        drivers: impl IntoIterator<Item = Arc<dyn LocalDriver>>,
    ) -> Result<Self, EngineError> {
        std::fs::create_dir_all(catalog.state_directory()).map_err(EngineError::Io)?;
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
                events,
            }),
        })
    }

    /// Returns the immutable compiled catalog.
    pub fn catalog(&self) -> &CompiledLocalCatalog {
        &self.inner.catalog
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
            if record.status() == JournalStatus::ReconciliationRequired {
                self.acquire_resource_locks(record.execution_id(), record.spec().resource_ids())?;
                continue;
            }
            let invocation = decode_invocation_json(record.spec().invocation_content())?;
            let contract = invocation
                .get("capability_contract")
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| EngineError::Protocol("journal invocation lacks contract".into()))?;
            let capability = self.capability(contract)?.clone();
            self.acquire_locks(
                record.execution_id(),
                &capability,
                record.spec().resource_ids(),
            )?;
            let current_workflow_digest = workflow_digest(&self.inner.catalog, &capability)?;
            if current_workflow_digest != record.spec().workflow_digest() {
                self.inner.journal.record_status(
                    record.execution_id(),
                    record.sequence().saturating_add(1),
                    JournalStatus::ReconciliationRequired,
                    "local workflow configuration changed while execution was active",
                )?;
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
        let workflow_digest = workflow_digest(&self.inner.catalog, &capability)?;
        let spec = ExecutionSpec::new(
            serde_json::to_vec(&invocation_json).map_err(EngineError::Json)?,
            workflow_digest,
            resource_ids.clone(),
        )?;

        if self.inner.journal.get(&execution_id)?.is_some() {
            return match self.inner.journal.prepare_dispatch(&execution_id, &spec)? {
                PrepareDispatch::Existing(record) => {
                    Ok(ExecuteDisposition::Existing(snapshot_from_record(record)?))
                }
                PrepareDispatch::Conflict(_) => Err(EngineError::ExecutionConflict(execution_id)),
                PrepareDispatch::Start(_) => unreachable!("existing record cannot start"),
            };
        }

        self.acquire_locks(&execution_id, &capability, &resource_ids)?;
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
            engine.spawn_status_loop(execution_id, invocation, handle, capability);
        });
    }

    /// Polls configured local status until a true terminal fact is observed.
    fn spawn_status_loop(
        &self,
        execution_id: String,
        invocation: serde_json::Value,
        handle: String,
        capability: CompiledCapability,
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
                match engine.record_mapped_fact(&execution_id, fact) {
                    Ok(true) => {
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

    /// Atomically acquires committed resources and configured local locks.
    fn acquire_locks(
        &self,
        execution_id: &str,
        capability: &CompiledCapability,
        resource_ids: &[String],
    ) -> Result<(), EngineError> {
        let keys = lock_keys(capability, resource_ids);
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

/// Builds deterministic local lock keys for one execution.
fn lock_keys(capability: &CompiledCapability, resource_ids: &[String]) -> BTreeSet<String> {
    resource_ids
        .iter()
        .map(|resource| format!("resource:{resource}"))
        .chain(
            capability
                .local_locks()
                .iter()
                .map(|lock| format!("local:{lock}")),
        )
        .collect()
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

/// Hashes capability behavior together with every fixed connection it references.
fn workflow_digest(
    catalog: &CompiledLocalCatalog,
    capability: &CompiledCapability,
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

/// Returns the fixed journal path for diagnostics and tests.
pub fn journal_path(state_directory: &std::path::Path) -> PathBuf {
    state_directory.join("execution-journal.sqlite3")
}
