//! Versioned deployment-owned Node certificate lifecycle values.
//!
//! The registry is a deployment-owned input to the transport. A certificate digest is only a
//! transport identity binding; it does not establish a physical device or hardware attestation.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Version identifier for the persisted Node identity registry.
pub const NODE_IDENTITY_REGISTRY_SCHEMA: &str = "roboguide.node-identity-registry/v0.1";

/// Lifecycle phase of one certificate binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityPhase {
    /// The current certificate is the only accepted certificate.
    Active,
    /// A replacement certificate is prepared but cannot authenticate yet.
    Prepared,
    /// The replacement is active while the previous certificate is accepted until its fence.
    Overlap,
    /// All certificates for this Node are rejected until explicit revision-bound reenrolment.
    Revoked,
}

/// One tenant/domain-qualified Node certificate lifecycle record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeIdentityRecord {
    /// Deployment measurement policy, if explicitly configured.
    #[serde(default)]
    pub attestation_policy: Option<crate::AttestationPolicy>,
    /// Last complete verified measurement report; restart never refreshes its validity.
    #[serde(default)]
    pub attestation_report: Option<crate::AttestationReport>,
    /// Stable logical Node identity.
    pub node_id: String,
    /// Deployment tenant owning this Node identity.
    pub tenant_id: String,
    /// Control domain owning this Node identity.
    pub domain_id: String,
    /// Current certificate SHA-256 DER digest.
    pub current_digest: String,
    /// Prepared or active replacement digest.
    #[serde(default)]
    pub next_digest: Option<String>,
    /// Previously active digest retained during bounded overlap.
    #[serde(default)]
    pub previous_digest: Option<String>,
    /// Monotonic registry revision.
    pub revision: u64,
    /// Lifecycle phase.
    pub phase: IdentityPhase,
    /// Inclusive validity start in Controller receive time.
    pub not_before_ms: u64,
    /// Exclusive validity end; zero means deployment-managed indefinite validity.
    pub expires_at_ms: u64,
    /// Exclusive previous-certificate overlap fence.
    #[serde(default)]
    pub overlap_until_ms: Option<u64>,
}

/// Persistent registry for all certificate lifecycle operations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeIdentityRegistry {
    /// Retired/revoked leaves cannot be reused by a later enrolment or another Node.
    #[serde(default)]
    pub retired_digests: std::collections::BTreeSet<String>,
    /// Durable nonce/evidence history; query and replay do not create new challenges.
    #[serde(default)]
    pub attestation_challenges: BTreeMap<String, crate::AttestationChallenge>,
    /// Registry codec schema.
    pub schema: String,
    /// Deployment identity of this registry, fixed across restart and certificate rotation.
    pub registry_id: String,
    /// Node identity records keyed by logical Node ID.
    pub records: BTreeMap<String, NodeIdentityRecord>,
    /// Durable idempotent lifecycle operation receipts; never evicted by a restart.
    #[serde(default)]
    pub commands: BTreeMap<String, IdentityCommandReceipt>,
}

/// Revision-bound deployment administration command.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityCommand {
    /// Globally unique administrative idempotency key.
    pub command_id: String,
    /// Exact Node being administered.
    pub node_id: String,
    /// Zero for initial enrolment, otherwise the exact current identity revision.
    pub expected_revision: u64,
    /// Explicit lifecycle transition.
    pub action: IdentityAction,
}

/// Closed set of deployment-owned certificate transitions.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum IdentityAction {
    /// Installs a trusted measurement policy and invalidates previous admission evidence.
    ConfigureAttestation {
        /// Explicit public-key and PCR admission policy.
        policy: crate::AttestationPolicy,
    },
    /// Creates a new logical identity; it does not invent capabilities or physical entity bindings.
    Register {
        /// Owning tenant.
        tenant_id: String,
        /// Owning Control domain.
        domain_id: String,
        /// Enrolled leaf SHA-256 digest.
        digest: String,
        /// Exclusive validity end; zero is explicitly deployment managed.
        expires_at_ms: u64,
    },
    /// Stages a replacement without accepting its certificate.
    Prepare {
        /// Exact replacement leaf digest.
        digest: String,
    },
    /// Activates the staged leaf and establishes a bounded old-certificate overlap.
    Activate {
        /// Configured overlap duration, bounded to at most one day.
        overlap_ms: u64,
    },
    /// Permanently drops the previous digest after explicit operator confirmation.
    RetirePrevious,
    /// Rejects every current certificate without releasing any execution ownership.
    Revoke,
    /// Sets a new exclusive validity fence without replacing the leaf.
    Renew {
        /// Future expiry, or zero for explicitly indefinite validity.
        expires_at_ms: u64,
    },
    /// Restores a revoked logical identity with a new leaf while preserving tenant/domain/history.
    Reenroll {
        /// New leaf; a previously revoked leaf cannot regain authority.
        digest: String,
        /// Exclusive validity end, or explicitly indefinite zero.
        expires_at_ms: u64,
    },
}

/// Durable result identifying an exact accepted administrative operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityCommandReceipt {
    /// Original administrative identity.
    pub command_id: String,
    /// Exact immutable command bytes digest.
    pub input_digest: String,
    /// Affected logical Node.
    pub node_id: String,
    /// Resulting identity revision.
    pub revision: u64,
    /// Original Controller receive time, never refreshed on replay.
    pub accepted_at_ms: u64,
    /// Authenticated administrative certificate subject supplied by the application.
    pub authority_subject: String,
}

/// Lifecycle operation failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityError {
    /// Registry or identity field is invalid.
    InvalidField(&'static str),
    /// Expected revision differs from durable state.
    RevisionConflict,
    /// The requested lifecycle transition is not valid.
    InvalidTransition,
    /// The certificate does not authenticate the Node at the supplied receive time.
    Rejected,
}

impl Display for IdentityError {
    /// Formats a stable operator diagnostic without exposing certificate bytes.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidField(field) => write!(formatter, "Node identity {field} is invalid"),
            Self::RevisionConflict => formatter.write_str("Node identity revision conflicts"),
            Self::InvalidTransition => {
                formatter.write_str("Node identity lifecycle transition is invalid")
            }
            Self::Rejected => {
                formatter.write_str("Node certificate is revoked, expired, or not admitted")
            }
        }
    }
}

impl std::error::Error for IdentityError {}

impl NodeIdentityRegistry {
    /// Creates an empty validated registry.
    pub fn new(registry_id: impl Into<String>) -> Self {
        Self {
            schema: NODE_IDENTITY_REGISTRY_SCHEMA.into(),
            registry_id: registry_id.into(),
            records: BTreeMap::new(),
            retired_digests: std::collections::BTreeSet::new(),
            attestation_challenges: BTreeMap::new(),
            commands: BTreeMap::new(),
        }
    }

    /// Applies a deduplicated expected-revision command atomically to a candidate registry.
    pub fn apply(
        &mut self,
        command: &IdentityCommand,
        now_ms: u64,
        authority_subject: &str,
    ) -> Result<IdentityCommandReceipt, IdentityError> {
        use sha2::{Digest, Sha256};
        if command.command_id.trim().is_empty()
            || command.command_id.len() > 128
            || authority_subject.trim().is_empty()
            || authority_subject.len() > 256
        {
            return Err(IdentityError::InvalidField("command"));
        }
        let bytes = serde_json::to_vec(command)
            .map_err(|_| IdentityError::InvalidField("command codec"))?;
        let input_digest = format!("sha256:{:x}", Sha256::digest(bytes));
        if let Some(previous) = self.commands.get(&command.command_id) {
            return if previous.input_digest == input_digest {
                Ok(previous.clone())
            } else {
                Err(IdentityError::RevisionConflict)
            };
        }
        if self.commands.len() >= 16384 {
            return Err(IdentityError::InvalidField("command capacity"));
        }
        let mut candidate = self.clone();
        if let IdentityAction::Register {
            tenant_id,
            domain_id,
            digest,
            expires_at_ms,
        } = &command.action
        {
            if command.expected_revision != 0 {
                return Err(IdentityError::RevisionConflict);
            }
            candidate.register(&command.node_id, tenant_id, domain_id, digest, now_ms)?;
            candidate
                .records
                .get_mut(&command.node_id)
                .expect("new identity")
                .expires_at_ms = *expires_at_ms;
        } else {
            let current = candidate
                .records
                .get(&command.node_id)
                .ok_or(IdentityError::Rejected)?;
            if current.revision != command.expected_revision {
                return Err(IdentityError::RevisionConflict);
            }
            if now_ms < current.not_before_ms
                || (current.phase == IdentityPhase::Revoked
                    && !matches!(command.action, IdentityAction::Reenroll { .. }))
            {
                return Err(IdentityError::InvalidTransition);
            }
            match &command.action {
                IdentityAction::Reenroll {
                    digest,
                    expires_at_ms,
                } => {
                    let record = candidate
                        .records
                        .get_mut(&command.node_id)
                        .expect("checked identity");
                    if record.phase != IdentityPhase::Revoked
                        || record.current_digest == *digest
                        || (*expires_at_ms != 0 && *expires_at_ms <= now_ms)
                    {
                        return Err(IdentityError::InvalidTransition);
                    }
                    validate_digest(digest)?;
                    record.revision = record
                        .revision
                        .checked_add(1)
                        .ok_or(IdentityError::RevisionConflict)?;
                    record.current_digest = digest.clone();
                    record.phase = IdentityPhase::Active;
                    record.not_before_ms = now_ms;
                    record.expires_at_ms = *expires_at_ms;
                    record.attestation_report = None;
                }
                IdentityAction::Prepare { digest } => {
                    candidate.prepare_rotation(
                        &command.node_id,
                        command.expected_revision,
                        digest,
                    )?;
                }
                IdentityAction::Activate { overlap_ms } => {
                    if *overlap_ms > 86_400_000 {
                        return Err(IdentityError::InvalidField("overlap"));
                    }
                    candidate.activate_rotation(
                        &command.node_id,
                        command.expected_revision,
                        now_ms,
                        *overlap_ms,
                    )?;
                }
                IdentityAction::Revoke => {
                    candidate.revoke(&command.node_id, command.expected_revision)?;
                }
                IdentityAction::ConfigureAttestation { policy } => {
                    policy
                        .validate()
                        .map_err(|_| IdentityError::InvalidField("attestation policy"))?;
                    let record = candidate
                        .records
                        .get_mut(&command.node_id)
                        .expect("checked identity");
                    if record.attestation_policy.as_ref().is_some_and(|previous| {
                        previous.policy_id != policy.policy_id
                            || previous.revision >= policy.revision
                    }) {
                        return Err(IdentityError::RevisionConflict);
                    }
                    record.attestation_policy = Some(policy.clone());
                    record.attestation_report = None;
                    record.revision = record
                        .revision
                        .checked_add(1)
                        .ok_or(IdentityError::RevisionConflict)?;
                }
                IdentityAction::RetirePrevious | IdentityAction::Renew { .. } => {
                    let record = candidate
                        .records
                        .get_mut(&command.node_id)
                        .expect("checked identity");
                    match command.action {
                        IdentityAction::RetirePrevious
                            if record.phase == IdentityPhase::Overlap =>
                        {
                            if let Some(previous) = &record.previous_digest {
                                candidate.retired_digests.insert(previous.clone());
                            }
                            record.previous_digest = None;
                            record.overlap_until_ms = None;
                            record.phase = IdentityPhase::Active;
                        }
                        IdentityAction::Renew { expires_at_ms }
                            if expires_at_ms == 0 || expires_at_ms > now_ms =>
                        {
                            record.expires_at_ms = expires_at_ms;
                        }
                        _ => return Err(IdentityError::InvalidTransition),
                    }
                    record.revision = record
                        .revision
                        .checked_add(1)
                        .ok_or(IdentityError::RevisionConflict)?;
                }
                IdentityAction::Register { .. } => unreachable!("handled registration"),
            }
        }
        candidate.validate()?;
        let record = &candidate.records[&command.node_id];
        let receipt = IdentityCommandReceipt {
            command_id: command.command_id.clone(),
            input_digest,
            node_id: command.node_id.clone(),
            revision: record.revision,
            accepted_at_ms: now_ms,
            authority_subject: authority_subject.into(),
        };
        candidate
            .commands
            .insert(command.command_id.clone(), receipt.clone());
        *self = candidate;
        Ok(receipt)
    }

    /// Inserts one initial identity at revision one.
    pub fn register(
        &mut self,
        node_id: impl Into<String>,
        tenant_id: impl Into<String>,
        domain_id: impl Into<String>,
        digest: impl Into<String>,
        now_ms: u64,
    ) -> Result<(), IdentityError> {
        let record = NodeIdentityRecord {
            attestation_policy: None,
            attestation_report: None,
            node_id: node_id.into(),
            tenant_id: tenant_id.into(),
            domain_id: domain_id.into(),
            current_digest: digest.into(),
            next_digest: None,
            previous_digest: None,
            revision: 1,
            phase: IdentityPhase::Active,
            not_before_ms: now_ms,
            expires_at_ms: 0,
            overlap_until_ms: None,
        };
        validate_record(&record)?;
        if self.retired_digests.contains(&record.current_digest) {
            return Err(IdentityError::Rejected);
        }
        if self.records.contains_key(&record.node_id) {
            return Err(IdentityError::InvalidTransition);
        }
        self.records.insert(record.node_id.clone(), record);
        Ok(())
    }

    /// Prepares a certificate replacement without admitting it to transport.
    pub fn prepare_rotation(
        &mut self,
        node_id: &str,
        expected_revision: u64,
        new_digest: impl Into<String>,
    ) -> Result<u64, IdentityError> {
        let new_digest = new_digest.into();
        if self.retired_digests.contains(&new_digest) {
            return Err(IdentityError::Rejected);
        }
        let record = self
            .records
            .get_mut(node_id)
            .ok_or(IdentityError::Rejected)?;
        if record.revision != expected_revision
            || record.phase != IdentityPhase::Active
            || record.current_digest == new_digest
        {
            return Err(IdentityError::RevisionConflict);
        }
        validate_digest(&new_digest)?;
        let revision = record
            .revision
            .checked_add(1)
            .ok_or(IdentityError::RevisionConflict)?;
        record.next_digest = Some(new_digest);
        record.revision = revision;
        record.phase = IdentityPhase::Prepared;
        Ok(record.revision)
    }

    /// Activates a prepared replacement and retains the old digest only through the explicit fence.
    pub fn activate_rotation(
        &mut self,
        node_id: &str,
        expected_revision: u64,
        now_ms: u64,
        overlap_ms: u64,
    ) -> Result<u64, IdentityError> {
        let record = self
            .records
            .get_mut(node_id)
            .ok_or(IdentityError::Rejected)?;
        if record.revision != expected_revision
            || record.phase != IdentityPhase::Prepared
            || overlap_ms == 0
        {
            return Err(IdentityError::InvalidTransition);
        }
        let until = now_ms
            .checked_add(overlap_ms)
            .ok_or(IdentityError::InvalidField("overlap"))?;
        let revision = record
            .revision
            .checked_add(1)
            .ok_or(IdentityError::RevisionConflict)?;
        let next = record
            .next_digest
            .take()
            .ok_or(IdentityError::InvalidTransition)?;
        record.previous_digest = Some(std::mem::replace(&mut record.current_digest, next));
        record.overlap_until_ms = Some(until);
        record.not_before_ms = now_ms;
        record.phase = IdentityPhase::Overlap;
        record.revision = revision;
        Ok(record.revision)
    }

    /// Revokes every certificate and fences existing sessions on their next message.
    pub fn revoke(&mut self, node_id: &str, expected_revision: u64) -> Result<u64, IdentityError> {
        let record = self
            .records
            .get_mut(node_id)
            .ok_or(IdentityError::Rejected)?;
        if record.revision != expected_revision || record.phase == IdentityPhase::Revoked {
            return Err(IdentityError::RevisionConflict);
        }
        let revision = record
            .revision
            .checked_add(1)
            .ok_or(IdentityError::RevisionConflict)?;
        self.retired_digests.insert(record.current_digest.clone());
        self.retired_digests
            .extend(record.next_digest.iter().cloned());
        self.retired_digests
            .extend(record.previous_digest.iter().cloned());
        record.phase = IdentityPhase::Revoked;
        record.next_digest = None;
        record.previous_digest = None;
        record.overlap_until_ms = None;
        record.revision = revision;
        Ok(record.revision)
    }

    /// Returns whether a certificate is valid for one Node at the Controller receive time.
    pub fn accepts(&self, node_id: &str, digest: &str, now_ms: u64) -> bool {
        self.records.get(node_id).is_some_and(|record| {
            record.phase != IdentityPhase::Revoked
                && now_ms >= record.not_before_ms
                && (record.expires_at_ms == 0 || now_ms < record.expires_at_ms)
                && (digest == record.current_digest
                    || (record.phase == IdentityPhase::Overlap
                        && record.previous_digest.as_deref() == Some(digest)
                        && record.overlap_until_ms.is_some_and(|until| now_ms < until)))
        })
    }

    /// Validates schema, map keys and every lifecycle record before startup use.
    pub fn validate(&self) -> Result<(), IdentityError> {
        if self.schema != NODE_IDENTITY_REGISTRY_SCHEMA
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.records.len() > 4096
            || self.commands.len() > 16384
            || self.attestation_challenges.len() > 16384
            || self.retired_digests.len() > 16384
        {
            return Err(IdentityError::InvalidField("registry"));
        }
        for digest in &self.retired_digests {
            validate_digest(digest)?;
        }
        for (key, record) in &self.records {
            if key != &record.node_id {
                return Err(IdentityError::InvalidField("node key"));
            }
            validate_record(record)?;
        }
        let mut active_digests = std::collections::BTreeSet::new();
        for record in self
            .records
            .values()
            .filter(|record| record.phase != IdentityPhase::Revoked)
        {
            for digest in std::iter::once(&record.current_digest)
                .chain(record.next_digest.iter())
                .chain(record.previous_digest.iter())
            {
                if self.retired_digests.contains(digest) {
                    return Err(IdentityError::Rejected);
                }
                if !active_digests.insert(digest) {
                    return Err(IdentityError::InvalidField(
                        "certificate reused across identities",
                    ));
                }
            }
        }
        for (id, receipt) in &self.commands {
            if id != &receipt.command_id
                || receipt.revision == 0
                || receipt.authority_subject.trim().is_empty()
                || !receipt
                    .input_digest
                    .strip_prefix("sha256:")
                    .is_some_and(|digest| validate_digest(digest).is_ok())
                || self
                    .records
                    .get(&receipt.node_id)
                    .is_none_or(|record| receipt.revision > record.revision)
            {
                return Err(IdentityError::InvalidField("command receipt"));
            }
        }
        Ok(())
    }
}

/// Validates one lifecycle record before mutation or persistence.
fn validate_record(record: &NodeIdentityRecord) -> Result<(), IdentityError> {
    if let Some(policy) = &record.attestation_policy {
        policy
            .validate()
            .map_err(|_| IdentityError::InvalidField("attestation policy"))?;
    }
    for (value, field) in [
        (&record.node_id, "node"),
        (&record.tenant_id, "tenant"),
        (&record.domain_id, "domain"),
    ] {
        if value.trim().is_empty() || value.len() > 128 {
            return Err(IdentityError::InvalidField(field));
        }
    }
    validate_digest(&record.current_digest)?;
    if record.revision == 0
        || (record.expires_at_ms != 0 && record.expires_at_ms <= record.not_before_ms)
    {
        return Err(IdentityError::InvalidField("revision/validity"));
    }
    if let Some(next) = &record.next_digest {
        validate_digest(next)?;
    }
    if let Some(previous) = &record.previous_digest {
        validate_digest(previous)?;
    }
    let phase_valid = match record.phase {
        IdentityPhase::Prepared => {
            record
                .next_digest
                .as_ref()
                .is_some_and(|next| next != &record.current_digest)
                && record.previous_digest.is_none()
                && record.overlap_until_ms.is_none()
        }
        IdentityPhase::Overlap => {
            record.next_digest.is_none()
                && record
                    .previous_digest
                    .as_ref()
                    .is_some_and(|previous| previous != &record.current_digest)
                && record
                    .overlap_until_ms
                    .is_some_and(|until| until > record.not_before_ms)
        }
        IdentityPhase::Active | IdentityPhase::Revoked => {
            record.next_digest.is_none()
                && record.previous_digest.is_none()
                && record.overlap_until_ms.is_none()
        }
    };
    if !phase_valid {
        return Err(IdentityError::InvalidField("phase evidence"));
    }
    Ok(())
}

/// Requires a lowercase SHA-256 DER certificate digest.
fn validate_digest(value: &str) -> Result<(), IdentityError> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(IdentityError::InvalidField("certificate digest"));
    }
    Ok(())
}
