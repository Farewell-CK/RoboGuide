//! Bounded durable Node-fact transactions; Integration only receives post-Commit dispositions.

use crate::*;
use integration::{GrpcNodeEventCompletion, GrpcNodeEventDelivery};
use ports::{DiagnosticHandle, NodeFactMetadata};

/// Shared or unclassified failures remain fatal to the application driver.
type DriverError = Box<dyn std::error::Error + Send + Sync>;

/// Distinguishes conclusive input rejection from failures after authoritative reduction.
enum FactFailure {
    /// Discard only this input's private memory/event candidate.
    Rejected(String),
    /// Abort the entire uncommitted batch without accepting any input.
    Fatal(DriverError),
}

/// References the existing single-writer authorities; it owns no second execution or resource map.
pub(crate) struct NodeFactDriver<'a, C: Clock> {
    /// Live projection published only after the complete batch commits.
    pub(crate) controller: &'a Arc<Mutex<ControllerState>>,
    /// Atomic event/checkpoint store.
    pub(crate) events: &'a state::SqliteEventLog,
    /// Ordering gate shared by timer, HTTP and localization writes.
    pub(crate) write_gate: &'a Arc<Mutex<()>>,
    /// Exact current-session validation, separate from Control eligibility.
    pub(crate) router: &'a integration::GrpcNodeRouter,
    /// Fresh decision/delivery clock; original receive time stays in each input.
    pub(crate) clock: &'a C,
    /// Optional observation which never influences authority or completion.
    pub(crate) diagnostics: &'a DiagnosticHandle,
}

/// Registration/topology changes and route loss must finish their own durable transaction.
pub(crate) fn fact_is_barrier(event: &GrpcNodeEvent) -> bool {
    !matches!(event, GrpcNodeEvent::NodeMessage { message, .. }
        if !matches!(message.message,
            Some(integration::grpc::v0_4::node_message::Message::RegistrationUpdate(_))))
}

/// Collects in order under one bounded optional window, returning a barrier for the next transaction.
pub(crate) async fn collect_fact_batch(
    first: GrpcNodeEventDelivery,
    receiver: &mut tokio::sync::mpsc::UnboundedReceiver<GrpcNodeEventDelivery>,
    limit: usize,
    wait: Duration,
) -> (Vec<GrpcNodeEventDelivery>, Option<GrpcNodeEventDelivery>) {
    let singleton = fact_is_barrier(first.event());
    let mut deliveries = vec![first];
    let deadline = tokio::time::Instant::now() + wait;
    if !singleton {
        while deliveries.len() < limit {
            let next = match receiver.try_recv() {
                Ok(delivery) => Some(delivery),
                Err(_) if wait.is_zero() => None,
                Err(_) => tokio::time::timeout_at(deadline, receiver.recv())
                    .await
                    .unwrap_or_default(),
            };
            let Some(delivery) = next else { break };
            if fact_is_barrier(delivery.event()) {
                return (deliveries, Some(delivery));
            }
            deliveries.push(delivery);
        }
    }
    (deliveries, None)
}

impl<C: Clock> NodeFactDriver<'_, C> {
    /// Commits one bounded ordered batch, then completes every fact in its original order.
    pub(crate) fn run(&self, deliveries: Vec<GrpcNodeEventDelivery>) -> Result<(), DriverError> {
        let (inputs, completions): (Vec<_>, Vec<GrpcNodeEventCompletion>) = deliveries
            .into_iter()
            .map(|delivery| {
                let (event, metadata, completion) = delivery.into_observed_parts();
                ((event, metadata), completion)
            })
            .unzip();
        let result = self.apply(inputs);
        match result {
            Ok(dispositions) => {
                for (completion, disposition) in completions.into_iter().zip(dispositions) {
                    match disposition {
                        Ok(()) => completion.accept(),
                        Err(reason) => completion.reject(reason),
                    }
                }
                Ok(())
            }
            Err(error) => {
                for completion in completions {
                    completion.unavailable(error.to_string());
                }
                Err(error)
            }
        }
    }

    /// Preserves each input's full transition while sharing the final serialization and Commit.
    fn apply(
        &self,
        inputs: Vec<(GrpcNodeEvent, Option<NodeFactMetadata>)>,
    ) -> Result<Vec<Result<(), String>>, DriverError> {
        if inputs.is_empty() || inputs.len() > 32 {
            return Err("Node fact batch must contain 1..32 inputs".into());
        }
        if inputs.len() > 1 && inputs.iter().any(|(event, _)| fact_is_barrier(event)) {
            return Err("registration and route barriers require independent transactions".into());
        }
        self.diagnostics.value(
            DiagnosticKind::Other,
            DiagnosticStage::BatchFacts,
            inputs.len() as u64,
            None,
        );
        let wait = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::WriteGate,
            None,
        );
        let _guard = self
            .write_gate
            .lock()
            .map_err(|_| "event-log write gate is poisoned")?;
        drop(wait);
        self.events.begin_batch()?;
        let result = self.apply_open_batch(inputs);
        if result.is_err() {
            let _ = self.events.rollback_batch();
        }
        result
    }

    /// Nested input savepoints cannot publish evidence, route commands or complete an early Ack.
    fn apply_open_batch(
        &self,
        inputs: Vec<(GrpcNodeEvent, Option<NodeFactMetadata>)>,
    ) -> Result<Vec<Result<(), String>>, DriverError> {
        let lock = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::ControllerLock,
            None,
        );
        let live = self
            .controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        drop(lock);
        let mut working = live.clone();
        drop(live);
        let mut dispositions = Vec::with_capacity(inputs.len());
        let mut rejected_sessions = BTreeSet::new();
        let correlation = domain::CorrelationId::new("integration-server")?;
        for (event, metadata) in inputs {
            let kind = metadata
                .as_ref()
                .map_or(DiagnosticKind::Other, |meta| meta.kind);
            let identity = metadata.as_ref().and_then(|meta| meta.identity.as_ref());
            let session = match &event {
                GrpcNodeEvent::NodeMessage {
                    node_id,
                    session_id,
                    ..
                } => Some((node_id.clone(), session_id.clone())),
                _ => None,
            };
            if let Some((node, session_id)) = &session
                && (rejected_sessions.contains(&(node.clone(), session_id.clone()))
                    || !self.router.session_is_current(node, session_id)?)
            {
                rejected_sessions.insert((node.clone(), session_id.clone()));
                dispositions.push(Err(
                    "Node session is no longer current or was rejected".into()
                ));
                continue;
            }
            let cloning =
                StageObservation::start(self.diagnostics, kind, DiagnosticStage::Clone, identity);
            let mut candidate = working.clone();
            drop(cloning);
            let now = self.clock.now();
            let received = metadata.as_ref().map_or(now, |meta| meta.received_at);
            let registration = matches!(event, GrpcNodeEvent::Registered { .. });
            let reduction =
                StageObservation::start(self.diagnostics, kind, DiagnosticStage::Reduce, identity);
            let transition = self.events.with_input_savepoint(|| {
                candidate
                    .bridge
                    .consume_received(event, received, now, &correlation)
                    .map_err(|error| FactFailure::Rejected(error.to_string()))?;
                self.derive(&mut candidate, registration, now, &correlation)
                    .map_err(FactFailure::Fatal)
            })?;
            drop(reduction);
            if let Some(error) = self.events.take_error()? {
                return Err(format!("durable fact event sink failed: {error}").into());
            }
            match transition {
                Ok(()) => {
                    working = candidate;
                    dispositions.push(Ok(()));
                }
                Err(FactFailure::Rejected(reason)) => {
                    if let Some(session) = session {
                        rejected_sessions.insert(session);
                    }
                    dispositions.push(Err(reason));
                }
                Err(FactFailure::Fatal(error)) => return Err(error),
            }
        }
        if dispositions.iter().all(Result::is_err) {
            self.events.rollback_batch()?;
            return Ok(dispositions);
        }
        let serialization = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::Serialize,
            None,
        );
        let checkpoint = server_checkpoint_json(&working)?;
        drop(serialization);
        self.diagnostics.value(
            DiagnosticKind::Other,
            DiagnosticStage::CheckpointBytes,
            checkpoint.len() as u64,
            None,
        );
        let save = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::Save,
            None,
        );
        self.events
            .save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)?;
        drop(save);
        let commit = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::Commit,
            None,
        );
        self.events.commit_batch()?;
        drop(commit);
        for (node, session) in rejected_sessions {
            self.router.fence_current_session(&node, &session)?;
        }
        let mut live = self
            .controller
            .lock()
            .map_err(|_| "controller lock is poisoned after Commit")?;
        *live = working;
        let _outbox = StageObservation::start(
            self.diagnostics,
            DiagnosticKind::Other,
            DiagnosticStage::Outbox,
            None,
        );
        if let Err(error) = flush_unisolated_dispatch(&mut live, self.clock) {
            eprintln!("durable command outbox delivery deferred: {error}");
        }
        Ok(dispositions)
    }

    /// Runs the unchanged authority sequence; registration still cannot activate Ready work.
    fn derive(
        &self,
        candidate: &mut ControllerState,
        registration: bool,
        now: domain::TimestampMs,
        correlation: &domain::CorrelationId,
    ) -> Result<(), DriverError> {
        let mut events = self.events.clone();
        apply_runtime_events(candidate, now, correlation, &mut events)?;
        if !registration {
            begin_current_ambiguity_recoveries(candidate, now, correlation, &mut events)?;
            resume_pending_recoveries(candidate, now, correlation, &mut events)?;
        }
        apply_pending_cancellations(candidate, now, correlation, &mut events)?;
        apply_runtime_outcomes(candidate, now, correlation, &mut events)?;
        if !registration {
            drive_ready_tasks(candidate, now, correlation, &mut events)?;
            drive_rebound_attempts(candidate, now, correlation)?;
        }
        Ok(())
    }
}
