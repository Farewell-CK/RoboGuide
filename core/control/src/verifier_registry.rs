//! Durable source authorization and admission receipts; Orchestration retains semantic lifecycle authority.

use crate::{ControlError, ControlPlane};
use domain::{
    SemanticVerifierEvidence, SemanticVerifierReceipt, SemanticVerifierRegistry, TimestampMs,
};
use sha2::{Digest, Sha256};

impl ControlPlane {
    /// Finds one explicitly enrolled measurement authority; ambiguous producers cannot race to satisfaction.
    pub fn registered_verifier_source(
        &self,
        spec: &domain::VerifierSatisfactionSpec,
        tenant: &str,
    ) -> Result<domain::SemanticVerifierSource, ControlError> {
        let sources = self
            .semantic_verifier_registry
            .as_ref()
            .into_iter()
            .flat_map(|registry| registry.sources.values())
            .filter(|source| {
                source.enabled
                    && source.tenant_id == tenant
                    && &source.verifier == spec.verifier()
                    && source.predicates.contains(spec.predicate())
            })
            .collect::<Vec<_>>();
        if sources.len() != 1 {
            return Err(ControlError::InvalidProposal(
                "Task requires one unique authorized verifier source".into(),
            ));
        }
        Ok(sources[0].clone())
    }

    /// Freezes complete Task measurement source contracts during accepted-plan admission.
    pub fn freeze_mission_verifier_sources(
        &mut self,
        plan: &domain::MissionPlan,
        tenant: &str,
    ) -> Result<(), ControlError> {
        self.freeze_verifier_sources_except(plan, tenant, &std::collections::BTreeSet::new())
    }

    /// Retains actual satisfied source contracts while admitting only new revision Tasks from current sources.
    pub fn freeze_revision_verifier_sources(
        &mut self,
        source_group: &domain::ExecutionGroupId,
        plan: &domain::MissionPlan,
        tenant: &str,
    ) -> Result<(), ControlError> {
        let source = self
            .group(source_group)
            .ok_or_else(|| ControlError::UnknownGroup(source_group.clone()))?;
        if source.mission_id() != plan.goal().mission_id() {
            return Err(ControlError::InvalidProposal(
                "revision verifier source belongs to another Mission".into(),
            ));
        }
        let completed = source
            .task_executions()
            .filter(|task| task.lifecycle() == domain::TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect();
        self.freeze_verifier_sources_except(plan, tenant, &completed)
    }

    /// Resolves the complete current source set before any immutable Task-source map changes.
    fn freeze_verifier_sources_except(
        &mut self,
        plan: &domain::MissionPlan,
        tenant: &str,
        completed: &std::collections::BTreeSet<domain::TaskId>,
    ) -> Result<(), ControlError> {
        let sources = plan
            .task_graph()
            .tasks()
            .iter()
            .filter(|task| !completed.contains(task.task_id()))
            .filter_map(|task| match task.satisfaction_basis() {
                domain::TaskSatisfactionBasis::VerifierEvidence(spec) => {
                    Some((task.requirement().task_ref(), spec))
                }
                domain::TaskSatisfactionBasis::ExecutionReport => None,
            })
            .map(|(task, spec)| {
                self.registered_verifier_source(spec, tenant)
                    .map(|source| (verifier_task_key(task), source))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if self
            .mission_verifier_sources
            .len()
            .saturating_add(sources.len())
            > 8192
        {
            return Err(ControlError::InvalidProposal(
                "frozen verifier source history is full".into(),
            ));
        }
        for (key, source) in &sources {
            if self
                .mission_verifier_sources
                .get(key)
                .is_some_and(|old| old != source)
            {
                return Err(ControlError::InvalidProposal(
                    "accepted Task verifier source cannot change".into(),
                ));
            }
        }
        self.mission_verifier_sources.extend(sources);
        Ok(())
    }

    /// Returns the immutable source contract frozen with one accepted Task.
    pub fn task_verifier_source(
        &self,
        task: &domain::TaskRef,
    ) -> Option<&domain::SemanticVerifierSource> {
        self.mission_verifier_sources.get(&verifier_task_key(task))
    }

    /// Installs one original continuous-verification grace fence before command delivery.
    pub fn monitor_semantic_verifier(
        &mut self,
        task: domain::TaskRef,
        plan_digest: String,
        attempts: Vec<domain::VerifierAttemptRef>,
        now: TimestampMs,
    ) -> Result<(), ControlError> {
        let Some(source) = self
            .task_verifier_source(&task)
            .filter(|source| source.continuous)
            .cloned()
        else {
            return Ok(());
        };
        let key = verifier_watch_key(&task, &source.source_id, &attempts);
        if self.semantic_verifier_watches.contains_key(&key) {
            return Ok(());
        }
        if self.semantic_verifier_watches.len() >= 8192 {
            return Err(ControlError::InvalidProposal(
                "continuous verifier history is full".into(),
            ));
        }
        let initial_deadline_ms =
            now.as_millis()
                .checked_add(source.max_age_ms)
                .ok_or_else(|| {
                    ControlError::InvalidProposal("continuous verifier deadline overflows".into())
                })?;
        self.semantic_verifier_watches.insert(
            key,
            domain::SemanticVerifierWatch {
                task_ref: task,
                source,
                plan_digest,
                attempts,
                started_at_ms: now.as_millis(),
                initial_deadline_ms,
            },
        );
        Ok(())
    }

    /// Returns original running measurement fences without changing lifetime or Task ownership.
    pub fn semantic_verifier_watches(&self) -> Vec<domain::SemanticVerifierWatch> {
        self.semantic_verifier_watches.values().cloned().collect()
    }
    /// Returns current independently authorized semantic producer contracts.
    pub fn semantic_verifier_registry(&self) -> Option<&SemanticVerifierRegistry> {
        self.semantic_verifier_registry.as_ref()
    }

    /// Applies an expected-revision source update without changing execution or semantic facts.
    pub fn accept_semantic_verifier_registry(
        &mut self,
        next: SemanticVerifierRegistry,
        expected_revision: u64,
    ) -> Result<(), ControlError> {
        next.validate().map_err(ControlError::InvalidProposal)?;
        match &self.semantic_verifier_registry {
            Some(previous) if previous == &next && previous.revision == expected_revision => {
                return Ok(());
            }
            Some(previous) if previous.revision == expected_revision => previous
                .validates_successor(&next)
                .map_err(ControlError::InvalidProposal)?,
            None if expected_revision == 0 && next.revision == 1 => {}
            _ => {
                return Err(ControlError::InvalidProposal(
                    "semantic verifier expected revision conflicts".into(),
                ));
            }
        }
        if self.semantic_verifier_history.len() >= 1024 {
            return Err(ControlError::InvalidProposal(
                "semantic verifier history is full".into(),
            ));
        }
        self.semantic_verifier_history
            .insert(next.revision, next.clone());
        self.semantic_verifier_registry = Some(next);
        Ok(())
    }

    /// Admits one exact authenticated producer input, preserving original time and idempotent response.
    pub fn admit_semantic_verifier_evidence(
        &mut self,
        evidence: SemanticVerifierEvidence,
        subject: &str,
        now: TimestampMs,
    ) -> Result<SemanticVerifierReceipt, ControlError> {
        evidence.validate().map_err(ControlError::InvalidProposal)?;
        let key = verifier_receipt_key(&evidence.source_id, &evidence.evidence_id);
        let digest = format!(
            "sha256:{:x}",
            Sha256::digest(
                serde_json::to_vec(&evidence)
                    .map_err(|error| ControlError::InvalidProposal(error.to_string()))?
            )
        );
        let registry = self.semantic_verifier_registry.as_ref().ok_or_else(|| {
            ControlError::InvalidProposal("semantic verifier registry is absent".into())
        })?;
        let source = self
            .semantic_verifier_registry
            .as_ref()
            .and_then(|registry| registry.sources.get(&evidence.source_id))
            .ok_or_else(|| {
                ControlError::InvalidProposal("semantic verifier source is not enrolled".into())
            })?;
        if !source.enabled
            || source.tenant_id != evidence.tenant_id
            || source.revision != evidence.source_revision
            || !source.producer_principals.contains(subject)
            || !source.predicates.contains(&evidence.predicate)
            || evidence.valid_for_ms > source.max_age_ms
            || !self.mission_tenant(evidence.task_ref.mission_id()).map_or(
                self.node_identity_registry().is_none() && evidence.tenant_id == "local",
                |tenant| tenant == evidence.tenant_id,
            )
        {
            return Err(ControlError::InvalidProposal(
                "semantic verifier source/tenant/principal/revision is not authorized".into(),
            ));
        }
        if let Some(previous) = self.semantic_verifier_receipts.get(&key) {
            return if previous.input_digest == digest {
                Ok(previous.clone())
            } else {
                Err(ControlError::InvalidProposal(
                    "semantic verifier evidence identity conflicts".into(),
                ))
            };
        }
        if self.semantic_verifier_receipts.len() >= 8192
            || self.semantic_verifier_receipts.values().any(|receipt| {
                receipt.evidence.source_id == evidence.source_id
                    && receipt.evidence.source_revision == evidence.source_revision
                    && receipt.evidence.task_ref == evidence.task_ref
                    && receipt.evidence.sequence >= evidence.sequence
            })
        {
            return Err(ControlError::InvalidProposal(
                "semantic verifier source sequence/history conflicts".into(),
            ));
        }
        let until = now
            .as_millis()
            .checked_add(evidence.valid_for_ms)
            .ok_or_else(|| {
                ControlError::InvalidProposal("semantic verifier validity overflows".into())
            })?;
        let receipt = SemanticVerifierReceipt {
            evidence,
            registry_revision: registry.revision,
            input_digest: digest,
            authority_subject: subject.into(),
            received_at_ms: now.as_millis(),
            valid_until_ms: until,
            applied: false,
            incident: None,
        };
        self.semantic_verifier_receipts.insert(key, receipt.clone());
        Ok(receipt)
    }

    /// Returns durable evidence receipts in deterministic identity order for the owning consumer.
    pub fn semantic_verifier_receipts(&self) -> Vec<SemanticVerifierReceipt> {
        self.semantic_verifier_receipts.values().cloned().collect()
    }

    /// Stores only the owning application's semantic-consumption or incident disposition.
    pub fn mark_semantic_verifier_receipt(
        &mut self,
        source: &str,
        evidence: &str,
        applied: bool,
        incident: Option<String>,
    ) -> Result<(), ControlError> {
        let receipt = self
            .semantic_verifier_receipts
            .get_mut(&verifier_receipt_key(source, evidence))
            .ok_or_else(|| {
                ControlError::InvalidProposal("semantic verifier receipt is absent".into())
            })?;
        if receipt.applied && !applied
            || receipt
                .incident
                .as_ref()
                .is_some_and(|old| incident.as_ref() != Some(old))
            || incident
                .as_ref()
                .is_some_and(|reason| reason.trim().is_empty() || reason.len() > 256)
        {
            return Err(ControlError::InvalidProposal(
                "semantic verifier disposition is invalid".into(),
            ));
        }
        receipt.applied = applied;
        receipt.incident = incident;
        Ok(())
    }

    /// Revalidates retained source meanings and original receipt content/timing without renewing freshness.
    pub(crate) fn validate_semantic_verifier_checkpoint(&self) -> Result<(), ControlError> {
        if self.semantic_verifier_history.len() > 1024
            || self.semantic_verifier_receipts.len() > 8192
        {
            return Err(ControlError::InvalidProposal(
                "semantic verifier checkpoint bounds are invalid".into(),
            ));
        }
        let mut previous: Option<&SemanticVerifierRegistry> = None;
        for (revision, registry) in &self.semantic_verifier_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if revision != &registry.revision {
                return Err(ControlError::InvalidProposal(
                    "semantic verifier history revision conflicts".into(),
                ));
            }
            if let Some(old) = previous {
                old.validates_successor(registry)
                    .map_err(ControlError::InvalidProposal)?;
            } else if *revision != 1 {
                return Err(ControlError::InvalidProposal(
                    "verifier history does not start at one".into(),
                ));
            }
            previous = Some(registry);
        }
        if let Some(registry) = &self.semantic_verifier_registry {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if previous != Some(registry) {
                return Err(ControlError::InvalidProposal(
                    "semantic verifier source history is absent".into(),
                ));
            }
        } else if !self.semantic_verifier_history.is_empty()
            || !self.semantic_verifier_receipts.is_empty()
        {
            return Err(ControlError::InvalidProposal(
                "semantic verifier evidence has no source authority".into(),
            ));
        }
        for (key, receipt) in &self.semantic_verifier_receipts {
            receipt
                .evidence
                .validate()
                .map_err(ControlError::InvalidProposal)?;
            let digest = format!(
                "sha256:{:x}",
                Sha256::digest(
                    serde_json::to_vec(&receipt.evidence)
                        .map_err(|error| ControlError::InvalidProposal(error.to_string()))?
                )
            );
            if key
                != &verifier_receipt_key(&receipt.evidence.source_id, &receipt.evidence.evidence_id)
                || receipt.input_digest != digest
                || receipt.authority_subject.trim().is_empty()
                || receipt.valid_until_ms
                    != receipt
                        .received_at_ms
                        .checked_add(receipt.evidence.valid_for_ms)
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "semantic verifier validity overflows".into(),
                            )
                        })?
                || !self
                    .mission_tenant(receipt.evidence.task_ref.mission_id())
                    .map_or(
                        self.node_identity_registry().is_none()
                            && receipt.evidence.tenant_id == "local",
                        |tenant| tenant == receipt.evidence.tenant_id,
                    )
            {
                return Err(ControlError::InvalidProposal(
                    "semantic verifier receipt authority/timing is invalid".into(),
                ));
            }
            let source = self
                .semantic_verifier_history
                .get(&receipt.registry_revision)
                .and_then(|registry| registry.sources.get(&receipt.evidence.source_id))
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "verifier receipt lacks original source contract".into(),
                    )
                })?;
            if !source.enabled
                || source.revision != receipt.evidence.source_revision
                || source.tenant_id != receipt.evidence.tenant_id
                || !source
                    .producer_principals
                    .contains(&receipt.authority_subject)
                || !source.predicates.contains(&receipt.evidence.predicate)
                || source.max_age_ms < receipt.evidence.valid_for_ms
            {
                return Err(ControlError::InvalidProposal(
                    "verifier receipt original authorization differs".into(),
                ));
            }
        }
        if self.mission_verifier_sources.len() > 8192 || self.semantic_verifier_watches.len() > 8192
        {
            return Err(ControlError::InvalidProposal(
                "verifier admission/watch history is full".into(),
            ));
        }
        for source in self.mission_verifier_sources.values() {
            if !self
                .semantic_verifier_history
                .values()
                .any(|registry| registry.sources.get(&source.source_id) == Some(source))
            {
                return Err(ControlError::InvalidProposal(
                    "frozen Task verifier source lacks history".into(),
                ));
            }
        }
        for (key, watch) in &self.semantic_verifier_watches {
            if key != &verifier_watch_key(&watch.task_ref, &watch.source.source_id, &watch.attempts)
                || self.task_verifier_source(&watch.task_ref) != Some(&watch.source)
                || watch.initial_deadline_ms
                    != watch
                        .started_at_ms
                        .checked_add(watch.source.max_age_ms)
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "verifier watch deadline overflows".into(),
                            )
                        })?
                || watch.attempts.is_empty()
                || watch.attempts.len() > 32
            {
                return Err(ControlError::InvalidProposal(
                    "verifier watch authority/deadline differs".into(),
                ));
            }
        }
        Ok(())
    }
}

/// Encodes complete Task identity without collapsing Mission namespaces or delimiter collisions.
fn verifier_task_key(task: &domain::TaskRef) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(task).expect("typed Task serializes"))
    )
}

/// Encodes current source and complete attempt-set identity; repeat installation cannot renew the grace.
fn verifier_watch_key(
    task: &domain::TaskRef,
    source: &str,
    attempts: &[domain::VerifierAttemptRef],
) -> String {
    format!(
        "{:x}",
        Sha256::digest(
            serde_json::to_vec(&(task, source, attempts)).expect("typed watch identity serializes")
        )
    )
}

/// Qualifies opaque source/evidence identities without ambiguous string delimiters or tuple JSON keys.
fn verifier_receipt_key(source: &str, evidence: &str) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&(source, evidence)).expect("bounded strings serialize"))
    )
}
