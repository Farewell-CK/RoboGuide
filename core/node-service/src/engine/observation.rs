//! Health, readiness, State, execution, and restart observations.

use super::*;

impl LocalIntegrationEngine {
    /// Subscribes one Node Protocol session to process-level execution facts.
    pub fn subscribe(&self) -> broadcast::Receiver<LocalExecutionEvent> {
        self.inner.events.subscribe()
    }

    /// Publishes one Local EAIOS peer-channel acknowledgement for the Node Service session.
    pub fn publish_peer_channel_readiness(&self, fact: LocalPeerChannelReadiness) {
        let _ = self.inner.peer_readiness.send(fact);
    }

    /// Subscribes one Node Protocol session to Local EAIOS peer-channel acknowledgements.
    pub fn subscribe_peer_channel_readiness(
        &self,
    ) -> broadcast::Receiver<LocalPeerChannelReadiness> {
        self.inner.peer_readiness.subscribe()
    }

    /// Observes every configured Local EAIOS and aggregates a truthful Node heartbeat status.
    pub async fn status(&self) -> integration::grpc::v0_4::NodeStatus {
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
        integration::grpc::v0_4::NodeStatus {
            health: state.to_string(),
            detail,
        }
    }

    /// Observes health and exact capability readiness without changing execution lifecycle.
    ///
    /// Readiness probe failures fence only the affected contract. Legacy v0.2/v0.3
    /// capabilities retain their historical static-ready behavior until migrated to v0.4.
    pub async fn observe(&self) -> NodeObservation {
        let mut status = self.status().await;
        let mut tasks = tokio::task::JoinSet::new();
        for (contract, capability) in self.inner.catalog.capabilities() {
            let contract = contract.clone();
            let readiness = capability.readiness().cloned();
            let engine = self.clone();
            tasks.spawn(async move {
                let fact = if let Some(readiness) = readiness {
                    let mut context = WorkflowContext::new(serde_json::json!({}));
                    match engine
                        .run_steps(std::slice::from_ref(readiness.step()), &mut context)
                        .await
                    {
                        Ok(()) => readiness.map(&context).unwrap_or_else(|error| {
                            CapabilityReadinessFact {
                                available: false,
                                detail: error.to_string(),
                            }
                        }),
                        Err(error) => CapabilityReadinessFact {
                            available: false,
                            detail: error.to_string(),
                        },
                    }
                } else {
                    CapabilityReadinessFact {
                        available: true,
                        detail: "legacy static readiness".to_string(),
                    }
                };
                (contract, fact)
            });
        }
        let mut capabilities = BTreeMap::new();
        while let Some(result) = tasks.join_next().await {
            match result {
                Ok((contract, fact)) => {
                    capabilities.insert(contract, fact);
                }
                Err(error) => {
                    capabilities.insert(
                        "readiness-task".to_string(),
                        CapabilityReadinessFact {
                            available: false,
                            detail: error.to_string(),
                        },
                    );
                }
            }
        }
        let readiness_detail = capabilities
            .iter()
            .filter(|(_, fact)| !fact.available)
            .map(|(contract, fact)| format!("{contract}=Unavailable:{}", fact.detail))
            .collect::<Vec<_>>()
            .join("; ");
        if !readiness_detail.is_empty() {
            if !status.detail.is_empty() {
                status.detail.push_str("; ");
            }
            status.detail.push_str(&readiness_detail);
        }
        NodeObservation {
            status,
            capabilities,
        }
    }

    /// Samples selected configured State exports without changing health or execution lifecycle.
    ///
    /// A failed local sample is omitted so the Controller retains the previous record until its
    /// configured validity expires. Other exports in the same polling cycle remain available.
    pub async fn observe_state_exports(
        &self,
        export_ids: &BTreeSet<String>,
    ) -> Vec<StateExportFact> {
        let mut tasks = tokio::task::JoinSet::new();
        for export_id in export_ids {
            let Some(export) = self.inner.catalog.state_exports().get(export_id).cloned() else {
                continue;
            };
            let engine = self.clone();
            tasks.spawn(async move {
                let mut context = WorkflowContext::new(serde_json::json!({}));
                engine
                    .run_steps(std::slice::from_ref(export.step()), &mut context)
                    .await?;
                export.map(&context).map_err(EngineError::Mapping)
            });
        }
        let mut facts = Vec::new();
        while let Some(result) = tasks.join_next().await {
            if let Ok(Ok(fact)) = result {
                facts.push(fact);
            }
        }
        facts.sort_by(|left, right| left.export_id.cmp(&right.export_id));
        facts
    }

    /// Samples selected fixed Local EAIOS peer-channel observers.
    ///
    /// Failed or malformed observations are omitted. Existing positive evidence then expires at
    /// the Controller rather than being silently renewed or converted into an invented negative.
    pub async fn observe_peer_channel_readiness(
        &self,
        observer_ids: &BTreeSet<String>,
    ) -> Vec<LocalPeerChannelReadiness> {
        let mut tasks = tokio::task::JoinSet::new();
        for observer_id in observer_ids {
            let Some(observer) = self
                .inner
                .catalog
                .peer_channel_observers()
                .get(observer_id)
                .cloned()
            else {
                continue;
            };
            let engine = self.clone();
            tasks.spawn(async move {
                let mut context = WorkflowContext::new(serde_json::json!({}));
                engine
                    .run_steps(std::slice::from_ref(observer.step()), &mut context)
                    .await?;
                observer.map(&context).map_err(EngineError::Mapping)
            });
        }
        let mut facts = Vec::new();
        while let Some(result) = tasks.join_next().await {
            if let Ok(Ok(observed)) = result {
                facts.extend(observed.into_iter().map(LocalPeerChannelReadiness::from));
            }
        }
        facts.sort_by(|left, right| {
            (
                left.group_id.as_str(),
                left.context_id.as_str(),
                left.context_role_id.as_str(),
                left.local_system_id.as_str(),
            )
                .cmp(&(
                    right.group_id.as_str(),
                    right.context_id.as_str(),
                    right.context_role_id.as_str(),
                    right.local_system_id.as_str(),
                ))
        });
        facts
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
}
