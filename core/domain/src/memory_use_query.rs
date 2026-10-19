//! Independent bounded observation of an original revoke; it cannot issue a content-use effect.

use crate::{
    MemoryPreparation, MemoryUseControl, MemoryUseOperation, MemoryUseProviderAcknowledgement,
    MemoryUseReceipt, MemoryUseStatus,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// One immutable readonly query; its receive budgets belong to observation, never to permission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseQuery {
    /// Closed observation schema.
    pub schema: String,
    /// Derived original-command/generation identity.
    pub query_id: String,
    /// Exact retained revoke, never a replacement command.
    pub command_id: String,
    /// Complete original control bytes.
    pub command_digest: String,
    /// One-based bounded observation ordinal.
    pub generation: u64,
    /// Distinct selected original providers, in canonical order.
    pub provider_ids: Vec<String>,
    /// Frozen Controller-local opening time, retained only as identity on the Node.
    pub opened_at_ms: u64,
    /// Original independent session width; retries cannot extend it.
    pub session_budget_ms: u64,
    /// Maximum receiver-local time for this observation.
    pub valid_for_ms: u64,
}

impl MemoryUseQuery {
    /// Derives one query identity without changing the original effect's identity.
    pub fn expected_id(&self) -> String {
        format!(
            "memory-use-query-{:x}-{}",
            sha2::Sha256::digest(self.command_id.as_bytes()),
            self.generation
        )
    }

    /// Validates closed bounded observations before journal admission or transport.
    pub fn validate(&self) -> Result<(), String> {
        crate::memory_authority::validate_text(&self.command_id, 256)?;
        crate::memory_authority::validate_digest(&self.command_digest)?;
        let sorted = self.provider_ids.iter().collect::<BTreeSet<_>>();
        if self.schema != "roboguide.memory-use-query/v0.1"
            || self.query_id != self.expected_id()
            || !(1..=16).contains(&self.generation)
            || !(1..=300_000).contains(&self.session_budget_ms)
            || !(1..=30_000).contains(&self.valid_for_ms)
            || self.valid_for_ms > self.session_budget_ms
            || !(1..=8).contains(&sorted.len())
            || sorted.into_iter().ne(self.provider_ids.iter())
            || self
                .opened_at_ms
                .checked_add(self.session_budget_ms)
                .is_none()
        {
            return Err("Memory-use query identity, ordering or budgets are invalid".into());
        }
        for provider in &self.provider_ids {
            crate::memory_authority::validate_text(provider, 128)?;
        }
        Ok(())
    }

    /// Binds queries only to an immutable original revoke and its selected providers.
    pub fn validate_original(
        &self,
        command: &MemoryUseControl,
        preparation: &MemoryPreparation,
    ) -> Result<(), String> {
        self.validate()?;
        command.validate_preparation(preparation)?;
        if command.operation != MemoryUseOperation::Revoke
            || self.command_id != command.command_id
            || self.command_digest != command.digest()?
            || self.provider_ids.iter().any(|provider| {
                !preparation
                    .inputs
                    .iter()
                    .any(|input| input.consumer.provider_id() == provider)
            })
        {
            return Err("Memory-use query changed its original revoke or provider set".into());
        }
        Ok(())
    }

    /// Binds the complete immutable query, independently of transport/session metadata.
    pub fn digest(&self) -> Result<String, String> {
        self.validate()?;
        crate::memory_authority::metadata_digest(self)
    }
}

use sha2::Digest as _;

/// An observation result cannot create or renew permission.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryUseQueryStatus {
    /// An exact retained original provider acknowledgement was verified.
    Confirmed,
    /// The original request exists but its result remains unavailable.
    Unknown,
    /// No original provider request exists; observation cannot issue one.
    NotIssued,
    /// The current backend/configuration or immutable evidence conflicts.
    Conflict,
}

/// Separate per-provider evidence; initial aggregate Unknown is never overwritten.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseQueryObservation {
    /// Exact requested original provider.
    pub provider_id: String,
    /// Typed result, independently of the diagnostic.
    pub status: MemoryUseQueryStatus,
    /// Original immutable proof, mandatory only for Confirmed.
    pub proof: Option<MemoryUseProviderAcknowledgement>,
    /// Bounded diagnostic, never interpreted as authority.
    pub reason: String,
}

/// Immutable observation receipt, separate from the original command receipt and clock.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseQueryReceipt {
    /// Closed independent observation schema.
    pub schema: String,
    /// Exact retained query identity.
    pub query_id: String,
    /// Complete query digest.
    pub query_digest: String,
    /// Exact provider coverage, in query order.
    pub observations: Vec<MemoryUseQueryObservation>,
}

impl MemoryUseQueryReceipt {
    /// Validates exact query coverage and complete original proof without manufacturing a result.
    pub fn validate(
        &self,
        query: &MemoryUseQuery,
        command: &MemoryUseControl,
        preparation: &MemoryPreparation,
    ) -> Result<(), String> {
        query.validate_original(command, preparation)?;
        if self.schema != "roboguide.memory-use-query-receipt/v0.1"
            || self.query_id != query.query_id
            || self.query_digest != query.digest()?
            || self.observations.len() != query.provider_ids.len()
        {
            return Err("Memory-use query receipt differs from its original query".into());
        }
        let mut proofs = Vec::new();
        for (observation, provider) in self.observations.iter().zip(&query.provider_ids) {
            if &observation.provider_id != provider
                || observation.reason.len() > 1024
                || (observation.status == MemoryUseQueryStatus::Confirmed)
                    != observation.proof.is_some()
            {
                return Err(
                    "Memory-use query omitted, duplicated or substituted provider evidence".into(),
                );
            }
            if let Some(proof) = &observation.proof {
                if &proof.provider_id != provider || !proof.import_ids.is_empty() {
                    return Err(
                        "Memory-use query proof changed its provider or grants content".into(),
                    );
                }
                proofs.push(proof.clone());
            }
        }
        MemoryUseReceipt {
            schema: "roboguide.memory-use-receipt/v0.1".into(),
            command_id: command.command_id.clone(),
            lease_id: command.lease_id.clone(),
            generation: 0,
            preparation_id: command.preparation_id.clone(),
            preparation_digest: command.preparation_digest.clone(),
            configuration_digest: command.configuration_digest.clone(),
            status: MemoryUseStatus::Unknown,
            remaining_budget_ms: 0,
            providers: proofs,
            reason: String::new(),
        }
        .validate(command, preparation)
    }
}
