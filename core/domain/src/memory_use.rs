//! Bounded current content-use permission, independently of original preparation and import effects.

use crate::{ExecutionGroupId, GroupMembershipVersion, MemoryPreparation, NodeId};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::collections::{BTreeMap, BTreeSet};

/// Receiver-relative maximum for one newly authorized use interval; replay cannot renew it.
pub const MEMORY_USE_MAX_BUDGET_MS: u64 = 30_000;

/// Stable identity for one original attempt/context, independent of renewable grant generations.
pub fn memory_use_lease_id(preparation_id: &str) -> String {
    format!("use-{:x}", Sha256::digest(preparation_id.as_bytes()))
}

/// Explicit current use authority; import completion or catalog discovery cannot produce it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryUseOperation {
    /// Authorize the first or next continuous original-budget interval.
    Grant,
    /// Irreversibly fence every generation, including authorization that arrives later.
    Revoke,
}

/// Authenticated Controller command; it contains no Local How path or backend-specific clock.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseControl {
    /// Closed command contract identity.
    pub schema: String,
    /// Immutable once-admitted identity, including operation and generation.
    pub command_id: String,
    /// Stable original-context identity used by the executing Local EAIOS operation.
    pub lease_id: String,
    /// One-based contiguous grant ordinal; irreversible revoke uses zero.
    pub generation: u64,
    /// Explicit new interval or irreversible fence.
    pub operation: MemoryUseOperation,
    /// Original prepared input identity, never another import/attempt.
    pub preparation_id: String,
    /// Exact complete original source, invocation, provider and physical-purpose digest.
    pub preparation_digest: String,
    /// Exact original physical execution.
    pub execution_id: String,
    /// Current bound Node, independently of logical Actor identity.
    pub node_id: NodeId,
    /// Exact original accepted session; reconnect cannot renew authority.
    pub source_session_id: String,
    /// Immutable admitted tenant.
    pub tenant_id: String,
    /// Existing Control-owned Group, never a new resource reservation.
    pub group_id: ExecutionGroupId,
    /// Original content-policy registry identity.
    pub registry_id: String,
    /// Current Control-accepted policy revision at this authorization.
    pub registry_revision: u64,
    /// Current exact physical membership, revalidated by Control before issuance/delivery.
    pub membership: GroupMembershipVersion,
    /// Node configuration already observed by the original durable Prepare receipt.
    pub configuration_digest: String,
    /// Maximum receiver-local interval; retries only transmit its original remaining budget.
    pub valid_for_ms: u64,
}

impl MemoryUseControl {
    /// Generates the exact immutable command identity without inventing a device or execution.
    pub fn expected_command_id(&self) -> String {
        let operation = match self.operation {
            MemoryUseOperation::Grant => "grant",
            MemoryUseOperation::Revoke => "revoke",
        };
        format!(
            "memory-use-{operation}-{}-{}",
            self.lease_id, self.generation
        )
    }

    /// Rejects unsupported schemas, identity substitution and unbounded counters before effects.
    pub fn validate(&self) -> Result<(), String> {
        self.membership.validate()?;
        for text in [&self.preparation_id, &self.tenant_id, &self.registry_id] {
            crate::memory_authority::validate_text(text, 128)?;
        }
        for text in [&self.execution_id, &self.source_session_id] {
            crate::memory_authority::validate_text(text, 1024)?;
        }
        for digest in [&self.preparation_digest, &self.configuration_digest] {
            crate::memory_authority::validate_digest(digest)?;
        }
        if self.schema != "roboguide.memory-use-control/v0.1"
            || self.lease_id != memory_use_lease_id(&self.preparation_id)
            || self.command_id != self.expected_command_id()
            || self.registry_revision == 0
            || self.registry_revision > i64::MAX as u64
            || self.generation > i64::MAX as u64
            || self.membership.generation > i64::MAX as u64
        {
            return Err(
                "Memory-use schema, immutable identity or authority ordinal differs".into(),
            );
        }
        match (self.operation, self.generation, self.valid_for_ms) {
            (MemoryUseOperation::Grant, 1.., 1..=MEMORY_USE_MAX_BUDGET_MS)
            | (MemoryUseOperation::Revoke, 0, 0) => Ok(()),
            _ => Err("Memory-use operation, generation or receiver budget is invalid".into()),
        }
    }

    /// Matches the complete original context while permitting only monotonic current authority.
    pub fn validate_preparation(&self, preparation: &MemoryPreparation) -> Result<(), String> {
        self.validate()?;
        preparation.validate()?;
        if preparation.schema != "roboguide.memory-preparation/v0.2" {
            return Err(
                "current Memory use requires original versioned use-capable preparation".into(),
            );
        }
        if self.preparation_id != preparation.preparation_id
            || self.preparation_digest != preparation.digest()?
            || self.execution_id != preparation.execution_id
            || self.node_id != preparation.node_id
            || self.source_session_id != preparation.source_session_id
            || self.tenant_id != preparation.tenant_id
            || self.group_id != preparation.group_id
            || self.registry_id != preparation.registry_id
            || self.registry_revision < preparation.registry_revision
            || self.membership.generation < preparation.membership.generation
            || (self.membership.generation == preparation.membership.generation
                && self.membership != preparation.membership)
        {
            return Err(
                "Memory-use command changes its original physical/logical/source purpose".into(),
            );
        }
        Ok(())
    }

    /// Binds immutable control bytes independently of the receiver's own time observation.
    pub fn digest(&self) -> Result<String, String> {
        self.validate()?;
        crate::memory_authority::metadata_digest(self)
    }
}

/// Provider-local proof retained transparently; its clock/profile is validated by the Node adapter.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseProviderAcknowledgement {
    /// Exact currently selected receiving provider.
    pub provider_id: String,
    /// Actual persistent backend instance already observed by original import continuity.
    pub provider_instance_id: String,
    /// Exact original workflow/connection identity, independently of its display name.
    pub provider_contract_digest: String,
    /// Complete granted input coverage; revoke receipts carry no content authorization.
    pub import_ids: Vec<String>,
    /// Exact retained provider response digest, checked before Controller admission.
    pub receipt_digest: String,
    /// Bounded original private-profile evidence; Core never interprets a vendor clock or path.
    pub original_receipt: serde_json::Value,
}

/// Current use evidence is independent of physical stopping, local completion and Task satisfaction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryUseStatus {
    /// All selected providers durably accepted the exact current interval.
    Granted,
    /// All selected providers acknowledged the irreversible content-use fence.
    Revoked,
    /// Original provider effects cannot be established conclusively; retain ownership.
    Unknown,
    /// No new use permission was admitted.
    Rejected,
}

/// Node receipt bound to an original durable command and complete provider coverage.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryUseReceipt {
    /// Explicit closed receipt schema.
    pub schema: String,
    /// Exact original command, not merely a transport delivery ID.
    pub command_id: String,
    /// Stable original-context use identity.
    pub lease_id: String,
    /// Exact admitted grant generation, or zero for revoke.
    pub generation: u64,
    /// Original immutable preparation.
    pub preparation_id: String,
    /// Complete original source/purpose digest.
    pub preparation_digest: String,
    /// Actual original Node configuration from durable preparation.
    pub configuration_digest: String,
    /// Typed content-use outcome only.
    pub status: MemoryUseStatus,
    /// Original receiver-local remainder at observation, never renewed by replay.
    pub remaining_budget_ms: u64,
    /// Complete distinct provider evidence; successful admission cannot omit an input/backend.
    pub providers: Vec<MemoryUseProviderAcknowledgement>,
    /// Bounded diagnostic, never parsed as permission or a physical outcome.
    pub reason: String,
}

impl MemoryUseReceipt {
    /// Validates exact original command and successful per-provider input coverage before Commit.
    pub fn validate(
        &self,
        command: &MemoryUseControl,
        preparation: &MemoryPreparation,
    ) -> Result<(), String> {
        command.validate_preparation(preparation)?;
        if self.schema != "roboguide.memory-use-receipt/v0.1"
            || self.command_id != command.command_id
            || self.lease_id != command.lease_id
            || self.generation != command.generation
            || self.preparation_id != command.preparation_id
            || self.preparation_digest != command.preparation_digest
            || self.configuration_digest != command.configuration_digest
            || self.remaining_budget_ms > command.valid_for_ms
            || self.reason.len() > 1024
            || self.providers.len() > 8
        {
            return Err("Memory-use receipt differs from its original command or bounds".into());
        }
        if !matches!(
            (command.operation, self.status),
            (MemoryUseOperation::Grant, MemoryUseStatus::Granted)
                | (MemoryUseOperation::Revoke, MemoryUseStatus::Revoked)
                | (_, MemoryUseStatus::Unknown | MemoryUseStatus::Rejected)
        ) || (self.status == MemoryUseStatus::Granted && self.remaining_budget_ms == 0)
            || (self.status != MemoryUseStatus::Granted && self.remaining_budget_ms != 0)
        {
            return Err("Memory-use outcome or remaining original budget is invalid".into());
        }
        let positive = matches!(
            self.status,
            MemoryUseStatus::Granted | MemoryUseStatus::Revoked
        );
        let expected = preparation.inputs.iter().fold(
            BTreeMap::<String, BTreeSet<String>>::new(),
            |mut inputs, input| {
                inputs
                    .entry(input.consumer.provider_id().into())
                    .or_default()
                    .insert(input.import_id.clone());
                inputs
            },
        );
        let mut observed = BTreeSet::new();
        for provider in &self.providers {
            crate::memory_authority::validate_text(&provider.provider_instance_id, 256)?;
            for digest in [&provider.provider_contract_digest, &provider.receipt_digest] {
                crate::memory_authority::validate_digest(digest)?;
            }
            let inputs = expected
                .get(&provider.provider_id)
                .ok_or("Memory-use receipt names a foreign provider")?;
            let exact = provider.import_ids.iter().cloned().collect::<BTreeSet<_>>();
            if !observed.insert(&provider.provider_id)
                || exact.len() != provider.import_ids.len()
                || !exact.is_subset(inputs)
                || (self.status == MemoryUseStatus::Granted && &exact != inputs)
                || (self.status == MemoryUseStatus::Revoked && !exact.is_empty())
                || !provider.original_receipt.is_object()
                || serde_json::to_vec(&provider.original_receipt)
                    .map_err(|e| e.to_string())?
                    .len()
                    > 16 * 1024
                || crate::memory_authority::metadata_digest(&provider.original_receipt)?
                    != provider.receipt_digest
            {
                return Err(
                    "Memory-use provider evidence is incomplete, duplicated or changed".into(),
                );
            }
        }
        if positive && observed.len() != expected.len() {
            return Err("positive Memory-use receipt omits an original provider".into());
        }
        Ok(())
    }
}

#[cfg(test)]
#[path = "memory_use_tests.rs"]
mod tests;
