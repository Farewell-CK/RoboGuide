//! Control-owned independent original-goal source contracts and exact immutable ingress receipts.

use crate::*;
use sha2::{Digest as _, Sha256};

/// Hashes complete typed source/context identities using canonical JSON object ordering.
fn digest(value: &impl serde::Serialize) -> Result<String, ControlError> {
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(
                &serde_json::to_value(value)
                    .map_err(|e| ControlError::InvalidProposal(e.to_string()))?
            )
            .map_err(|e| ControlError::InvalidProposal(e.to_string()))?
        )
    ))
}

/// Validates one original digest without turning an unavailable legacy string into authority.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

/// Checks the complete original measurement shape independently of current source availability.
fn valid_evidence(
    input: &domain::MissionGoalEvidence,
    contract: &domain::MissionGoalContract,
    principal: &str,
) -> bool {
    let unique: BTreeSet<_> = input.attempts.iter().collect();
    !(input.schema_version != "roboguide.mission-goal-evidence/v0.1"
        || input.evidence_id.trim().is_empty()
        || input.evidence_id.len() > 128
        || input.contract_digest != contract.digest
        || input.tenant_id != contract.tenant_id
        || input.source_id != contract.source.source_id
        || input.source_revision != contract.source.revision
        || !contract.source.producer_principals.contains(principal)
        || input.predicate != contract.spec.predicate()
        || input.valid_for_ms == 0
        || input.valid_for_ms
            > contract
                .spec
                .max_evidence_age_ms()
                .min(contract.source.max_age_ms)
        || input.sequence == 0
        || !valid_digest(&input.plan_digest)
        || unique.len() != input.attempts.len()
        || input.attempts.len() > 4096
        || input.attempts.iter().any(|attempt| {
            attempt.task_ref.mission_id() != &input.mission_id
                || attempt.attempt_id.trim().is_empty()
                || attempt.attempt_id.len() > 2048
        })
        || input.artifact_digest.is_some() != input.artifact_size_bytes.is_some()
        || input
            .artifact_digest
            .as_deref()
            .is_some_and(|value| !valid_digest(value))
        || contract.source.require_artifact
            && input.verdict == domain::SemanticVerdict::Satisfied
            && input.artifact_digest.is_none())
}

impl ControlPlane {
    /// Freezes the actual independently enrolled goal source; a new candidate cannot redefine it.
    pub fn freeze_mission_goal(
        &mut self,
        goal: &domain::MissionGoal,
        spec: domain::VerifierSatisfactionSpec,
        context_digest: String,
        tenant: &str,
    ) -> Result<domain::MissionGoalContract, ControlError> {
        if self.mission_tenant(goal.mission_id()) != Some(tenant)
            || !valid_digest(&context_digest)
            || !(100..=60_000).contains(&spec.max_evidence_age_ms())
        {
            return Err(ControlError::InvalidProposal(
                "invalid original goal context, tenant or freshness".into(),
            ));
        }
        let source = self.registered_verifier_source(&spec, tenant)?;
        let original_goal_digest = digest(goal)?;
        let mut contract = domain::MissionGoalContract {
            mission_id: goal.mission_id().clone(),
            tenant_id: tenant.into(),
            original_goal_digest,
            context_digest,
            spec,
            source,
            digest: String::new(),
        };
        contract.digest = digest(&contract)?;
        if let Some(original) = self.mission_goal_contracts.get(goal.mission_id()) {
            if original != &contract {
                return Err(ControlError::InvalidProposal(
                    "original Mission goal/source contract cannot change".into(),
                ));
            }
            return Ok(original.clone());
        }
        if self.mission_goal_contracts.len() >= 128 {
            return Err(ControlError::InvalidProposal(
                "retained Mission goal authority is full".into(),
            ));
        }
        self.mission_goal_contracts
            .insert(goal.mission_id().clone(), contract.clone());
        Ok(contract)
    }

    /// Returns the original immutable independent goal source, not the current candidate's claim.
    pub fn mission_goal_contract(
        &self,
        mission: &MissionId,
    ) -> Option<&domain::MissionGoalContract> {
        self.mission_goal_contracts.get(mission)
    }

    /// Reads bounded original goal authorities for cross-module orphan and lineage validation.
    pub fn mission_goal_contracts(&self) -> impl Iterator<Item = &domain::MissionGoalContract> {
        self.mission_goal_contracts.values()
    }

    /// Rechecks current enrolled source identity; revocation never rewrites its original contract.
    pub fn mission_goal_source_current(&self, mission: &MissionId) -> bool {
        self.mission_goal_contracts
            .get(mission)
            .is_some_and(|contract| {
                self.registered_verifier_source(&contract.spec, &contract.tenant_id)
                    .is_ok_and(|source| source == contract.source)
            })
    }

    /// Admits exact source/principal/schema/sequence evidence after application verifies current attempts/CAS.
    pub fn admit_mission_goal_evidence(
        &mut self,
        input: domain::MissionGoalEvidence,
        principal: &str,
        leaf_digest: &str,
        now: TimestampMs,
    ) -> Result<domain::MissionGoalReceipt, ControlError> {
        let contract = self
            .mission_goal_contracts
            .get(&input.mission_id)
            .ok_or_else(|| {
                ControlError::InvalidProposal("Mission has no original goal contract".into())
            })?;
        let input_digest = digest(&input)?;
        let prior = self.mission_goal_receipts.values().find(|receipt| {
            receipt.evidence.mission_id == input.mission_id
                && receipt.evidence.source_id == input.source_id
                && receipt.evidence.evidence_id == input.evidence_id
        });
        if let Some(original) = prior {
            if original.input_digest != input_digest || original.authority_subject != principal {
                return Err(ControlError::InvalidProposal(
                    "goal evidence identity was reused by another input/principal".into(),
                ));
            }
            return Ok(original.clone());
        }
        if !valid_evidence(&input, contract, principal)
            || !valid_digest(leaf_digest)
            || self
                .operator_authorization
                .as_ref()
                .is_some_and(|registry| {
                    registry.grants.get(leaf_digest).is_none_or(|grant| {
                        grant.principal_id != principal || !grant.active(now.as_millis())
                    })
                })
            || !self.mission_goal_source_current(&input.mission_id)
            || self.mission_goal_receipts.values().any(|receipt| {
                receipt.evidence.mission_id == input.mission_id
                    && receipt.evidence.source_id == input.source_id
                    && receipt.evidence.plan_revision == input.plan_revision
                    && receipt.evidence.group_id == input.group_id
                    && receipt.evidence.sequence >= input.sequence
            })
        {
            return Err(ControlError::InvalidProposal(
                "goal source evidence, original scope or sequence is invalid".into(),
            ));
        }
        if self.mission_goal_receipts.len() >= 4096 {
            return Err(ControlError::InvalidProposal(
                "retained goal evidence is full".into(),
            ));
        }
        let receipt = domain::MissionGoalReceipt {
            evidence: input,
            authority_subject: principal.into(),
            authority_leaf_digest: leaf_digest.into(),
            input_digest: input_digest.clone(),
            received_at: now,
        };
        self.mission_goal_receipts
            .insert(input_digest, receipt.clone());
        Ok(receipt)
    }

    /// Reads complete retained goal measurements without renewing their original receive times.
    pub fn mission_goal_receipts(&self, mission: &MissionId) -> Vec<domain::MissionGoalReceipt> {
        self.mission_goal_receipts
            .values()
            .filter(|receipt| &receipt.evidence.mission_id == mission)
            .cloned()
            .collect()
    }

    /// Returns the exact immutable admitted input independently of current source expiry.
    pub fn mission_goal_receipt(&self, digest: &str) -> Option<&domain::MissionGoalReceipt> {
        self.mission_goal_receipts.get(digest)
    }

    /// Checks a current exact final-positive receipt at the original decision time, not a caller's boolean.
    pub fn mission_goal_confirmed(
        &self,
        mission: &MissionId,
        revision: u64,
        group: &ExecutionGroupId,
        plan_digest: &str,
        receipt_digest: &str,
        now: TimestampMs,
    ) -> bool {
        !self.mission_goal_receipts.values().any(|later| {
            &later.evidence.mission_id == mission
                && later.evidence.plan_revision == revision
                && &later.evidence.group_id == group
                && self
                    .mission_goal_receipts
                    .get(receipt_digest)
                    .is_some_and(|original| {
                        later.evidence.source_id == original.evidence.source_id
                            && later.evidence.sequence > original.evidence.sequence
                    })
        }) && self.mission_goal_source_current(mission)
            && self
                .mission_goal_receipts
                .get(receipt_digest)
                .is_some_and(|receipt| {
                    let input = &receipt.evidence;
                    &input.mission_id == mission
                        && input.plan_revision == revision
                        && &input.group_id == group
                        && input.plan_digest == plan_digest
                        && input.finality == domain::VerifierFinality::Final
                        && input.verdict == domain::SemanticVerdict::Satisfied
                        && now
                            .as_millis()
                            .checked_sub(receipt.received_at.as_millis())
                            .is_some_and(|age| age < input.valid_for_ms)
                })
    }

    /// Validates immutable original source history and raw receipts without fabricating current availability.
    pub(crate) fn validate_mission_goal_checkpoint(&self) -> Result<(), ControlError> {
        if self.mission_goal_contracts.len() > 128 || self.mission_goal_receipts.len() > 4096 {
            return Err(ControlError::InvalidProposal(
                "goal history bound exceeded".into(),
            ));
        }
        for (mission, contract) in &self.mission_goal_contracts {
            let mut original = contract.clone();
            original.digest.clear();
            if mission != &contract.mission_id
                || self.mission_tenant(mission) != Some(contract.tenant_id.as_str())
                || !valid_digest(&contract.original_goal_digest)
                || !valid_digest(&contract.context_digest)
                || contract.digest != digest(&original)?
                || contract.source.tenant_id != contract.tenant_id
                || contract.source.verifier != *contract.spec.verifier()
                || !contract
                    .source
                    .predicates
                    .contains(contract.spec.predicate())
                || !self.semantic_verifier_history.values().any(|registry| {
                    registry.sources.get(&contract.source.source_id) == Some(&contract.source)
                })
            {
                return Err(ControlError::InvalidProposal(
                    "original goal/source history is inconsistent".into(),
                ));
            }
        }
        let mut ids = BTreeSet::new();
        let mut sequences = BTreeSet::new();
        for (key, receipt) in &self.mission_goal_receipts {
            let contract = self
                .mission_goal_contracts
                .get(&receipt.evidence.mission_id)
                .ok_or_else(|| {
                    ControlError::InvalidProposal("goal receipt lacks its original contract".into())
                })?;
            let input = &receipt.evidence;
            if !valid_evidence(input, contract, &receipt.authority_subject)
                || !valid_digest(&receipt.authority_leaf_digest)
                || self
                    .operator_authorization
                    .as_ref()
                    .is_some_and(|registry| {
                        registry
                            .grants
                            .get(&receipt.authority_leaf_digest)
                            .is_none_or(|grant| grant.principal_id != receipt.authority_subject)
                    })
                || key != &receipt.input_digest
                || key != &digest(input)?
                || input.contract_digest != contract.digest
                || !contract
                    .source
                    .producer_principals
                    .contains(&receipt.authority_subject)
                || input.source_id != contract.source.source_id
                || input.source_revision != contract.source.revision
                || input.tenant_id != contract.tenant_id
                || input.valid_for_ms == 0
                || input.valid_for_ms
                    > contract
                        .source
                        .max_age_ms
                        .min(contract.spec.max_evidence_age_ms())
                || !ids.insert((&input.mission_id, &input.source_id, &input.evidence_id))
                || !sequences.insert((
                    &input.mission_id,
                    &input.source_id,
                    input.plan_revision,
                    &input.group_id,
                    input.sequence,
                ))
            {
                return Err(ControlError::InvalidProposal(
                    "original goal receipt history is inconsistent".into(),
                ));
            }
        }
        Ok(())
    }
}
