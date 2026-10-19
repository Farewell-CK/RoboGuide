//! Provider-complete revoke progress and independently authorized readonly original-result queries.

use super::*;
use domain::{
    MemoryUseQuery, MemoryUseQueryObservation, MemoryUseQueryReceipt, MemoryUseQueryStatus,
};

/// Separates unavailable I/O from an explicit original identity/proof conflict without string parsing.
enum ObservationOutcome {
    /// Exact original provider evidence passed every identity check.
    Confirmed(MemoryUseProviderAcknowledgement),
    /// A declared identity or retained proof conflicts with the original command.
    Conflict(String),
}

impl LocalIntegrationEngine {
    /// Admits only negotiated independent observation, without reopening the original revoke.
    pub(crate) fn admit_memory_use_query(
        &self,
        query: &MemoryUseQuery,
        remaining: u64,
        session_remaining: u64,
    ) -> Result<(), EngineError> {
        if self.catalog().node_contract_version()
            != integration::grpc::v0_4::MEMORY_USE_QUERY_NODE_CONTRACT_VERSION
        {
            return Err(EngineError::Memory(
                "independent use queries were not negotiated".into(),
            ));
        }
        self.inner.journal.admit_memory_use_observation(
            query,
            &ReceiverClockObservation::read()?,
            remaining,
            session_remaining,
        )?;
        Ok(())
    }

    /// Loads original durable identity without requiring a previously confirmed backend to be online.
    fn retained_use_originals(
        &self,
        preparation: &MemoryPreparation,
        provider: &str,
    ) -> Result<Vec<ImportContinuity>, EngineError> {
        let current = self.memory_provider_contract_digest(provider)?;
        let mut originals = Vec::new();
        for input in preparation
            .inputs
            .iter()
            .filter(|input| input.consumer.provider_id() == provider)
        {
            let original = self
                .original_import_continuity(&input.import_id)?
                .ok_or_else(|| {
                    EngineError::Memory("original provider continuity is unavailable".into())
                })?;
            if original.provider_id != provider || original.provider_contract_digest != current {
                return Err(EngineError::Memory(
                    "original provider contract changed".into(),
                ));
            }
            originals.push(original);
        }
        if originals.is_empty() {
            return Err(EngineError::Memory(
                "original provider has no admitted inputs".into(),
            ));
        }
        Ok(originals)
    }

    /// Performs one possible original POST or one reserved GET; it never reconstructs an old request.
    async fn progress_revoke_provider(
        &self,
        record: &JournalMemoryUseCommand,
        preparation: &MemoryPreparation,
        provider: &str,
    ) -> Result<Option<MemoryUseProviderAcknowledgement>, EngineError> {
        let originals = self.retained_use_originals(preparation, provider)?;
        let saved = self
            .inner
            .journal
            .memory_use_effect(&record.command.command_id, provider)?;
        if let Some(value) = saved.as_ref().and_then(|effect| effect.receipt.clone()) {
            return self
                .use_provider_proof(record, provider, &originals, value)
                .map(Some);
        }
        self.use_originals(preparation, provider).await?;
        let request = match saved.as_ref() {
            Some(effect) => effect.request.clone(),
            None => self.use_request(record, preparation, &originals)?,
        };
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
            .ok_or_else(|| EngineError::Memory("original provider workflow is absent".into()))?;
        let value = if first {
            self.use_step(usage.control(), preparation, &effect.request)
                .await?
        } else {
            if !self
                .inner
                .journal
                .begin_memory_use_revoke_query(&record.command.command_id, provider)?
            {
                return Ok(None);
            }
            let observed: ProviderQuery = serde_json::from_value(
                self.use_step(usage.query(), preparation, &effect.request)
                    .await?,
            )
            .map_err(EngineError::Json)?;
            let Some(value) = observed.receipt else {
                return Ok(None);
            };
            value
        };
        let proof = self.use_provider_proof(record, provider, &originals, value)?;
        self.inner.journal.record_memory_use_effect_receipt(
            &record.command.command_id,
            provider,
            &proof.original_receipt,
        )?;
        Ok(Some(proof))
    }

    /// Gives independent providers bounded opportunities; one unavailable provider cannot skip peers.
    pub(super) async fn complete_original_revoke(
        &self,
        record: &JournalMemoryUseCommand,
        preparation: &MemoryPreparation,
        mut receipt: MemoryUseReceipt,
    ) -> Result<MemoryUseReceipt, EngineError> {
        let providers = preparation
            .inputs
            .iter()
            .map(|input| input.consumer.provider_id().to_string())
            .collect::<BTreeSet<_>>();
        let expected = providers.len();
        let mut workers = tokio::task::JoinSet::new();
        for provider in providers {
            let engine = self.clone();
            let record = record.clone();
            let preparation = preparation.clone();
            workers.spawn(async move {
                let mut last=String::from("original provider outcome is unavailable");
                loop {
                    let remaining=record.budget.remaining_ms(&ReceiverClockObservation::read()?);
                    if remaining==0 {return Ok::<_,EngineError>((None,last));}
                    let permit=tokio::time::timeout(std::time::Duration::from_millis(remaining),Arc::clone(&engine.inner.memory_use_provider_workers).acquire_owned()).await;
                    let Ok(Ok(permit))=permit else{return Ok((None,String::from("original provider worker budget expired")))};
                    let remaining=record.budget.remaining_ms(&ReceiverClockObservation::read()?);
                    let result=tokio::time::timeout(std::time::Duration::from_millis(remaining.min(1000)),engine.progress_revoke_provider(&record,&preparation,&provider)).await;
                    drop(permit);
                    match result {
                        Ok(Ok(Some(proof)))=>return Ok((Some(proof),String::new())),
                        Ok(Err(error))=>last=reason(&error.to_string()),
                        Err(_)=>last=String::from("original provider I/O timed out; only readonly recovery is permitted"),
                        Ok(Ok(None))=>{},
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
            });
        }
        while let Some(result) = workers.join_next().await {
            let (proof, why) = result.map_err(|error| EngineError::Memory(error.to_string()))??;
            if let Some(proof) = proof {
                receipt.providers.push(proof);
            } else {
                receipt.reason = why;
            }
        }
        receipt
            .providers
            .sort_by(|a, b| a.provider_id.cmp(&b.provider_id));
        if receipt.providers.len() == expected {
            receipt.status = MemoryUseStatus::Revoked;
            receipt.reason.clear();
        }
        self.inner
            .journal
            .record_memory_use_receipt(&receipt)
            .map_err(EngineError::from)
    }

    /// Completes a once-admitted independent observation; no control/import workflow is called.
    pub(crate) async fn complete_memory_use_query(
        &self,
        query: &MemoryUseQuery,
    ) -> Result<MemoryUseQueryReceipt, EngineError> {
        let gate = {
            let mut gates = self
                .inner
                .memory_use_workers
                .lock()
                .map_err(|_| EngineError::Memory("query worker ordering unavailable".into()))?;
            if gates.len() >= 5120 && !gates.contains_key(&query.query_id) {
                return Err(EngineError::Memory(
                    "bounded readonly workers are full".into(),
                ));
            }
            Arc::clone(
                gates
                    .entry(query.query_id.clone())
                    .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(()))),
            )
        };
        let _guard = gate.lock().await;
        let retained = self
            .inner
            .journal
            .memory_use_observation(&query.query_id)?
            .ok_or_else(|| EngineError::Memory("readonly use query was never admitted".into()))?;
        if retained.query != *query {
            return Err(EngineError::Memory(
                "original readonly query changed".into(),
            ));
        }
        if let Some(receipt) = retained.receipt {
            return Ok(receipt);
        }
        let original = self
            .inner
            .journal
            .memory_use_command(&query.command_id)?
            .ok_or_else(|| EngineError::Memory("original revoke is unavailable".into()))?;
        let context = self
            .inner
            .journal
            .memory_preparation(&original.command.preparation_id)?
            .ok_or_else(|| EngineError::Memory("original preparation is unavailable".into()))?;
        query
            .validate_original(&original.command, &context.preparation)
            .map_err(EngineError::Memory)?;
        let mut receipt = MemoryUseQueryReceipt {
            schema: "roboguide.memory-use-query-receipt/v0.1".into(),
            query_id: query.query_id.clone(),
            query_digest: query.digest().map_err(EngineError::Memory)?,
            observations: Vec::new(),
        };
        for provider in &query.provider_ids {
            let mut observation = MemoryUseQueryObservation {
                provider_id: provider.clone(),
                status: MemoryUseQueryStatus::Unknown,
                proof: None,
                reason: String::new(),
            };
            let effect = self
                .inner
                .journal
                .memory_use_effect(&query.command_id, provider)?;
            if let Some(effect) = effect {
                let outcome = async {
                    let _permit = Arc::clone(&self.inner.memory_use_provider_workers)
                        .acquire_owned()
                        .await
                        .map_err(|_| {
                            EngineError::Memory("provider query workers unavailable".into())
                        })?;
                    let originals = match self
                        .retained_use_originals(&context.preparation, provider)
                    {
                        Ok(originals) => originals,
                        Err(error) => {
                            return Ok(ObservationOutcome::Conflict(reason(&error.to_string())));
                        }
                    };
                    if let Some(value) = effect.receipt {
                        return Ok(
                            match self.use_provider_proof(&original, provider, &originals, value) {
                                Ok(proof) => ObservationOutcome::Confirmed(proof),
                                Err(error) => {
                                    ObservationOutcome::Conflict(reason(&error.to_string()))
                                }
                            },
                        );
                    }
                    if !self
                        .inner
                        .journal
                        .begin_memory_use_observation_provider(query, provider)?
                    {
                        return Err(EngineError::Memory("original provider query was already consumed; use a later bounded generation".into()));
                    }
                    let instance = self.memory_provider_instance(provider).await?;
                    if originals
                        .iter()
                        .any(|original| original.provider_instance_id != instance)
                    {
                        return Ok(ObservationOutcome::Conflict(
                            "original provider instance changed".into(),
                        ));
                    }
                    let usage = self
                        .catalog()
                        .memory_providers()
                        .get(provider)
                        .and_then(|p| p.use_lease())
                        .ok_or_else(|| {
                            EngineError::Memory(
                                "original readonly provider route is unavailable".into(),
                            )
                        })?;
                    let observed: ProviderQuery = serde_json::from_value(
                        self.use_step(usage.query(), &context.preparation, &effect.request)
                            .await?,
                    )
                    .map_err(EngineError::Json)?;
                    let value = observed.receipt.ok_or_else(|| {
                        EngineError::Memory("original provider result remains unknown".into())
                    })?;
                    let proof = match self
                        .use_provider_proof(&original, provider, &originals, value)
                    {
                        Ok(proof) => proof,
                        Err(error) => {
                            return Ok(ObservationOutcome::Conflict(reason(&error.to_string())));
                        }
                    };
                    self.inner.journal.record_memory_use_effect_receipt(
                        &query.command_id,
                        provider,
                        &proof.original_receipt,
                    )?;
                    Ok::<_, EngineError>(ObservationOutcome::Confirmed(proof))
                };
                let remaining = retained
                    .budget
                    .remaining_ms(&ReceiverClockObservation::read()?);
                match tokio::time::timeout(
                    std::time::Duration::from_millis(remaining.min(3000)),
                    outcome,
                )
                .await
                {
                    Ok(Ok(ObservationOutcome::Confirmed(proof))) => {
                        observation.status = MemoryUseQueryStatus::Confirmed;
                        observation.proof = Some(proof);
                    }
                    Ok(Ok(ObservationOutcome::Conflict(reason))) => {
                        observation.status = MemoryUseQueryStatus::Conflict;
                        observation.reason = reason;
                    }
                    Ok(Err(error)) => observation.reason = reason(&error.to_string()),
                    Err(_) => {
                        observation.reason = "original readonly observation budget expired".into()
                    }
                }
            } else {
                observation.status = MemoryUseQueryStatus::NotIssued;
                observation.reason =
                    "original provider request is absent; readonly queries cannot issue it".into();
            }
            receipt.observations.push(observation);
        }
        self.inner
            .journal
            .record_memory_use_observation(&receipt)
            .map_err(EngineError::from)
    }
}
