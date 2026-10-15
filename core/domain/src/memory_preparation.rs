//! Semantic Memory prerequisites and immutable Control-selected pre-execution context.

use crate::{
    ExecutionGroupId, GroupMembershipVersion, MapArtifactManifest, MemoryArtifactManifest,
    MemoryKind, MemoryProviderDescriptor, MemorySelector, NodeId, RoleId, TaskRef,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Accepted-plan Memory requirement; it never contains a consumer Node or provider selector.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryRequirement {
    /// Exact immutable semantic/content revision supplied through admitted planning evidence.
    pub selector: MemorySelector,
    /// Required semantic kind, independently of the receiving storage implementation.
    pub kind: MemoryKind,
    /// Exact payload contract required by the operation.
    pub payload_schema: String,
    /// Exact media contract required by the operation.
    pub media_type: String,
}

impl MemoryRequirement {
    /// Requires a bounded duplicate-free set before accepting a role or restoring its command.
    pub fn validate_set(requirements: &[Self]) -> Result<(), String> {
        if requirements.len() > 8 {
            return Err("a Role supports at most eight exact Memory prerequisites".into());
        }
        let mut selectors = BTreeSet::new();
        for requirement in requirements {
            requirement.validate()?;
            if !selectors.insert(&requirement.selector) {
                return Err("Role Memory prerequisites repeat an immutable selector".into());
            }
        }
        Ok(())
    }
    /// Checks bounded semantic contracts without inventing inventory or current availability.
    pub fn validate(&self) -> Result<(), String> {
        for value in [&self.payload_schema, &self.media_type] {
            crate::memory_authority::validate_text(value, 256)?;
        }
        Ok(())
    }

    /// Compares original catalog content against the accepted semantic requirement.
    pub fn matches_manifest(&self, manifest: &MemoryArtifactManifest) -> bool {
        self.selector == *manifest.selector()
            && self.kind == manifest.kind()
            && self.payload_schema == manifest.payload_schema()
            && self.media_type == manifest.media_type()
    }

    /// Checks a live receiving declaration; Control alone selects among compatible providers.
    pub fn matches_provider(&self, provider: &MemoryProviderDescriptor) -> bool {
        self.kind == provider.kind()
            && self.payload_schema == provider.payload_schema()
            && self.media_type == provider.media_type()
            && provider.visibility() == crate::MemoryVisibility::Exchangeable
    }
}

/// One exact immutable input chosen by Control after physical binding and content authorization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparedMemoryInput {
    /// Original accepted semantic requirement, retained independently of current catalog projections.
    pub requirement: MemoryRequirement,
    /// Complete source manifest; CAS identity is checked separately by the data plane.
    pub manifest: MemoryArtifactManifest,
    /// Complete original typed-map metadata when the source is a Spatial catalog projection.
    pub typed_map: Option<MapArtifactManifest>,
    /// Exact currently registered receiver selected by Control, never model output.
    pub consumer: MemoryProviderDescriptor,
    /// Original immutable import identity; ambiguity fences alternate identities.
    pub import_id: String,
}

impl PreparedMemoryInput {
    /// Rejects source stripping and incompatible receivers before any provider effect.
    pub fn validate(&self, node: &NodeId) -> Result<(), String> {
        self.requirement.validate()?;
        crate::memory_authority::validate_text(&self.import_id, 128)?;
        if !self.requirement.matches_manifest(&self.manifest)
            || !self.requirement.matches_provider(&self.consumer)
            || !self.consumer.supports_preparation()
        {
            return Err(
                "prepared Memory source/consumer differs from accepted requirements".into(),
            );
        }
        self.consumer
            .admit_import(&self.manifest, node)
            .map_err(|e| e.to_string())?;
        if self.manifest.artifact().is_none() {
            return Err("prepared Memory requires actual immutable content".into());
        }
        if let Some(map) = &self.typed_map
            && crate::map_memory_manifest(map)? != self.manifest
        {
            return Err(
                "prepared typed-map provenance differs from its discovery projection".into(),
            );
        }
        Ok(())
    }
}

/// Authenticated Controller context persisted by the Node before imports or Execute are permitted.
/// It is a command payload, never a credential or proof of provider availability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryPreparation {
    /// Required closed schema identity.
    pub schema: String,
    /// Exact immutable preparation identity retained through replay.
    pub preparation_id: String,
    /// Original tenant admitted by Control content policy.
    pub tenant_id: String,
    /// Current Control-owned Mission Group.
    pub group_id: ExecutionGroupId,
    /// Exact logical Task and Role.
    pub task_ref: TaskRef,
    /// Exact Role inside the Task.
    pub role_id: RoleId,
    /// Exact physical attempt; no context can authorize a replacement attempt.
    pub execution_id: String,
    /// Actual bound receiver identity.
    pub node_id: NodeId,
    /// Original accepted Node session; reconnect never renews this authorization.
    pub source_session_id: String,
    /// Exact current membership generation, including physical assignment digest.
    pub membership: GroupMembershipVersion,
    /// Original current content authority identity.
    pub registry_id: String,
    /// Original current content authority revision.
    pub registry_revision: u64,
    /// Canonical invocation and accepted execution topology, without any Local How paths.
    pub invocation: serde_json::Value,
    /// Exact committed resources; preparation creates no ownership.
    pub resource_ids: Vec<String>,
    /// Immutable receive-relative budget; replay cannot renew it.
    pub valid_for_ms: u64,
    /// Complete bounded immutable input set.
    pub inputs: Vec<PreparedMemoryInput>,
}

impl MemoryPreparation {
    /// Validates complete identity and input contracts before admission or checkpoint restoration.
    pub fn validate(&self) -> Result<(), String> {
        self.membership.validate()?;
        for value in [&self.preparation_id, &self.tenant_id, &self.registry_id] {
            crate::memory_authority::validate_text(value, 256)?;
        }
        for value in [&self.execution_id, &self.source_session_id] {
            crate::memory_authority::validate_text(value, 1024)?;
        }
        crate::memory_authority::validate_text(&self.preparation_id, 128)?;
        if !matches!(
            self.schema.as_str(),
            "roboguide.memory-preparation/v0.1" | "roboguide.memory-preparation/v0.2"
        ) || !(1..=120_000).contains(&self.valid_for_ms)
            || self.registry_revision == 0
            || !(1..=8).contains(&self.inputs.len())
            || !self.invocation.is_object()
            || self.resource_ids.len() > 64
        {
            return Err("Memory preparation schema, budget or content bounds are invalid".into());
        }
        let mut selectors = BTreeSet::new();
        let mut imports = BTreeSet::new();
        for input in &self.inputs {
            input.validate(&self.node_id)?;
            if (self.schema == "roboguide.memory-preparation/v0.2"
                && !input.consumer.supports_use_lease())
                || (self.schema == "roboguide.memory-preparation/v0.1"
                    && input.consumer.use_contract().is_some())
            {
                return Err(
                    "Memory preparation version differs from its declared current-use authority"
                        .into(),
                );
            }
            if !selectors.insert(input.manifest.selector().clone())
                || !imports.insert(&input.import_id)
            {
                return Err(
                    "Memory preparation contains duplicate content/import identities".into(),
                );
            }
        }
        let mut resources = BTreeSet::new();
        for id in &self.resource_ids {
            crate::memory_authority::validate_text(id, 256)?;
            if !resources.insert(id) {
                return Err("Memory preparation repeats a committed resource".into());
            }
        }
        for (field, expected) in [
            ("mission_id", self.task_ref.mission_id().as_str()),
            ("group_id", self.group_id.as_str()),
            ("task_id", self.task_ref.task_id().as_str()),
            ("role_id", self.role_id.as_str()),
        ] {
            if self
                .invocation
                .get(field)
                .and_then(serde_json::Value::as_str)
                != Some(expected)
            {
                return Err(format!("Memory preparation invocation {field} differs"));
            }
        }
        Ok(())
    }

    /// Binds all immutable command metadata, independently of transport epoch and CAS content hash.
    pub fn digest(&self) -> Result<String, String> {
        self.validate()?;
        crate::memory_authority::metadata_digest(self)
    }
}

/// Closed Node-side operations over an original prepared context, independently of Execute.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryControlKind {
    /// Persist immutable context without calling Local How or importing content.
    Prepare,
    /// Observe original backend content once under a bounded original generation.
    Probe,
    /// Persist an irreversible context fence before acknowledging revocation.
    Revoke,
}

/// Exact authenticated command over one immutable context; retries never create a new budget.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryControlCommand {
    /// Required closed command schema.
    pub schema: String,
    /// Original context identity.
    pub preparation_id: String,
    /// Complete original metadata digest.
    pub preparation_digest: String,
    /// Explicit operation; discovery cannot create one.
    pub operation: MemoryControlKind,
    /// Zero for prepare/revoke, one through sixteen for readonly probes.
    pub generation: u64,
    /// Complete context only for initial preparation; queries cannot replace it.
    pub preparation: Option<MemoryPreparation>,
}

impl MemoryControlCommand {
    /// Derives one unambiguous once-admitted identity for transport retries and durable receipts.
    pub fn command_id(&self) -> String {
        let operation = match self.operation {
            MemoryControlKind::Prepare => "prepare",
            MemoryControlKind::Probe => "probe",
            MemoryControlKind::Revoke => "revoke",
        };
        format!(
            "memory-{operation}-{}-{}",
            self.preparation_id, self.generation
        )
    }

    /// Rejects content replacement, unbounded probing and unsupported authority before effects.
    pub fn validate(&self) -> Result<(), String> {
        crate::memory_authority::validate_text(&self.preparation_id, 128)?;
        crate::memory_authority::validate_digest(&self.preparation_digest)?;
        if self.schema != "roboguide.memory-control/v0.1" {
            return Err("unsupported Memory control command schema".into());
        }
        match (self.operation, self.generation, self.preparation.as_ref()) {
            (MemoryControlKind::Prepare, 0, Some(preparation)) => {
                if preparation.preparation_id != self.preparation_id
                    || preparation.digest()? != self.preparation_digest
                {
                    return Err("Memory preparation identity or complete metadata differs".into());
                }
            }
            (MemoryControlKind::Probe, 1..=16, None) | (MemoryControlKind::Revoke, 0, None) => {}
            _ => return Err("Memory control operation, generation or context is invalid".into()),
        }
        Ok(())
    }
}

/// Actual original-backend observation; historical import completion alone cannot construct it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryAvailabilityEvidence {
    /// Exact version of the provider-local current-content observation contract.
    pub schema: String,
    /// Original immutable import identity.
    pub import_id: String,
    /// Original full invocation/configuration digest retained before import effects.
    pub identity_digest: String,
    /// Actual persistent backend instance observed before the original import.
    pub provider_instance_id: String,
    /// Original configured provider and connections, independently of the provider's name.
    pub provider_contract_digest: String,
    /// Complete original generic manifest metadata.
    pub manifest_digest: String,
    /// Expected immutable bytes actually inspected by the provider.
    pub content_digest: String,
    /// Exact original payload size.
    pub byte_size: u64,
    /// Current full read/hash observation, never inferred from a retained Imported status.
    pub available: bool,
    /// Bounded diagnostic; no caller parses it to grant authority.
    pub reason: String,
}

impl MemoryAvailabilityEvidence {
    /// Requires exact original content and bounded typed provider evidence.
    pub fn validate(&self, input: &PreparedMemoryInput) -> Result<(), String> {
        let artifact = input
            .manifest
            .artifact()
            .ok_or("Memory input has no content")?;
        if self.schema != "roboguide.memory-provider-availability/v0.1"
            || self.import_id != input.import_id
            || self.manifest_digest != crate::memory_manifest_digest(&input.manifest)?
            || self.content_digest != artifact.content_digest().as_str()
            || self.byte_size != artifact.byte_size()
            || self.reason.len() > 1024
        {
            return Err("current Memory availability differs from its original input".into());
        }
        crate::memory_authority::validate_digest(&format!(
            "sha256:{}",
            self.identity_digest
                .strip_prefix("sha256:")
                .unwrap_or(&self.identity_digest)
        ))?;
        for digest in [
            &self.provider_contract_digest,
            &self.manifest_digest,
            &self.content_digest,
        ] {
            crate::memory_authority::validate_digest(digest)?;
        }
        crate::memory_authority::validate_text(&self.provider_instance_id, 256)
    }
}

/// Typed durable outcome; Prepared, Available and Revoked have distinct meanings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryControlStatus {
    /// Immutable preparation and its original budget were durably admitted.
    Prepared,
    /// Every exact input was freshly observed in its original backend.
    Available,
    /// The current query could not establish complete content availability.
    Unavailable,
    /// Node persisted the irreversible context fence.
    Revoked,
    /// The command was rejected without authorizing a new effect.
    Rejected,
}

/// Node receipt that becomes Controller guidance only after application Commit and exact revalidation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryControlReceipt {
    /// Original Node-observed operation/provider configuration, unavailable only for rejected/unknown revocation.
    pub configuration_digest: Option<String>,
    /// Exact durable command identity.
    pub command_id: String,
    /// Original context identity and complete metadata.
    pub preparation_id: String,
    /// Original immutable context digest.
    pub preparation_digest: String,
    /// Typed observed outcome, never an execution outcome or Task satisfaction.
    pub status: MemoryControlStatus,
    /// Remaining original receiver budget; zero cannot authorize dispatch.
    pub remaining_budget_ms: u64,
    /// Fresh actual backend observations for this once-admitted probe generation.
    pub observations: Vec<MemoryAvailabilityEvidence>,
    /// Bounded diagnostic, independently of the typed outcome.
    pub reason: String,
}

impl MemoryControlReceipt {
    /// Checks exact request identity and complete positive observations without broadening grants.
    pub fn validate(
        &self,
        command: &MemoryControlCommand,
        preparation: &MemoryPreparation,
    ) -> Result<(), String> {
        command.validate()?;
        if preparation.preparation_id != command.preparation_id
            || preparation.digest()? != command.preparation_digest
        {
            return Err("Memory receipt was checked against another original context".into());
        }
        if self.command_id != command.command_id()
            || self.preparation_id != command.preparation_id
            || self.preparation_digest != command.preparation_digest
            || self.remaining_budget_ms > preparation.valid_for_ms
            || self.reason.len() > 1024
            || self.observations.len() > 8
        {
            return Err("Memory control receipt identity or bounds differ".into());
        }
        let permitted = matches!(
            (command.operation, self.status),
            (MemoryControlKind::Prepare, MemoryControlStatus::Prepared)
                | (
                    MemoryControlKind::Probe,
                    MemoryControlStatus::Available | MemoryControlStatus::Unavailable
                )
                | (MemoryControlKind::Revoke, MemoryControlStatus::Revoked)
                | (_, MemoryControlStatus::Rejected)
        );
        if !permitted {
            return Err("Memory control receipt outcome differs from its operation".into());
        }
        if matches!(
            self.status,
            MemoryControlStatus::Prepared | MemoryControlStatus::Available
        ) {
            let configuration = self
                .configuration_digest
                .as_ref()
                .ok_or("Memory availability lacks original Node configuration")?;
            crate::memory_authority::validate_digest(configuration)?;
            if self.remaining_budget_ms == 0 {
                return Err("positive Memory receipt lacks its original live budget".into());
            }
        }
        if self.status == MemoryControlStatus::Prepared && !self.observations.is_empty() {
            return Err(
                "durable context admission is not a content availability observation".into(),
            );
        }
        if self.status == MemoryControlStatus::Available {
            if self.remaining_budget_ms == 0 || self.observations.len() != preparation.inputs.len()
            {
                return Err(
                    "Memory availability lacks a live budget or complete original input set".into(),
                );
            }
            let mut ids = BTreeSet::new();
            for evidence in &self.observations {
                if !evidence.available || !ids.insert(&evidence.import_id) {
                    return Err(
                        "Memory availability contains missing or duplicate positive evidence"
                            .into(),
                    );
                }
                let input = preparation
                    .inputs
                    .iter()
                    .find(|input| input.import_id == evidence.import_id)
                    .ok_or("Memory availability names a foreign input")?;
                evidence.validate(input)?;
            }
        }
        Ok(())
    }
}
