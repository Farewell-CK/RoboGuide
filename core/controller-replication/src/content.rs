//! Original Artifact identities and same-member durability checks for the explicit content profile.
//!
//! These DTO checks do not observe files, authenticate peers or create an ACK. Only the actual
//! receiving storage/transport adapter may supply observations after its SQL and CAS checks.

use crate::ControllerEpoch;
use openraft::{BasicNode, StoredMembership};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Maximum complete content input count, independent of payload byte and transport budgets.
const MAX_INPUTS: usize = 16_384;
/// Maximum explicitly configured infrastructure members; these are never embodied Nodes.
const MAX_MEMBERS: usize = 9;

/// Actual original Artifact backend identity; Local Memory providers retain their separate authority.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactContentKey {
    /// Original actual software backend owner, never substituted with the receiving member's owner.
    pub source_owner: String,
    /// Complete canonical hash of opaque original bytes.
    pub content_digest: String,
    /// Complete original byte count, including zero-length content.
    pub byte_size: u64,
}

/// Checks lowercase full SHA-256 without accepting a short hash, uppercase alias or arbitrary label.
pub(super) fn hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// Validates the distinct original Artifact and receiving infrastructure storage identity namespaces.
fn owner(value: &str, prefix: &str) -> bool {
    value.strip_prefix(prefix).is_some_and(hex_digest)
}

impl ArtifactContentKey {
    /// Validates only immutable identity; no local placement, read grant or availability is inferred.
    pub fn validate(&self) -> Result<(), String> {
        if !owner(&self.source_owner, "artifact-store:")
            || !self
                .content_digest
                .strip_prefix("sha256:")
                .is_some_and(hex_digest)
        {
            return Err("invalid original Artifact content identity".into());
        }
        Ok(())
    }
}

/// Complete immutable target of one actual same-member majority observation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentQuorumTarget {
    /// Startup-frozen cluster/profile identity, independent of a reused member number or endpoint.
    pub profile_identity: String,
    /// Actual current authority under which the observation is admitted, not a renewed Task budget.
    pub epoch: ControllerEpoch,
    /// Original accepted application revision whose complete metadata must remain durable.
    pub revision: u64,
    /// Existing State checkpoint identity; its original lowercase-hex convention is preserved.
    pub checkpoint_digest: String,
    /// Complete original content/application command identity.
    pub batch_digest: String,
    /// Exact observed consensus membership including its original log identity.
    pub membership: StoredMembership<u64, BasicNode>,
    /// Consensus-bound actual receiving software stores for every current voter.
    pub member_stores: BTreeMap<u64, String>,
    /// Complete sorted original requirements; equal bytes under another owner remain independent.
    pub inputs: Vec<ArtifactContentKey>,
}

/// Actual adapter observation; a serialized instance alone is never storage or TLS evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemberContentObservation {
    /// Actual original request/connection profile; another restored cluster cannot lend evidence.
    pub profile_identity: String,
    /// Actual authenticated infrastructure sender, supplied by its real connection.
    pub member: u64,
    /// Actual opened receiver backend, independently checked against the original registry.
    pub store_owner: String,
    /// Current observing epoch bound to the original request.
    pub epoch: ControllerEpoch,
    /// Exact original application identity independently read from accepted SQL history.
    pub revision: u64,
    /// Exact original complete checkpoint digest.
    pub checkpoint_digest: String,
    /// Exact original content/application command digest.
    pub batch_digest: String,
    /// Exact observed membership; another accepted configuration cannot lend voters.
    pub membership: StoredMembership<u64, BasicNode>,
    /// Complete actually verified original content set, never an aggregate caller `ok` flag.
    pub inputs: Vec<ArtifactContentKey>,
}

impl ContentQuorumTarget {
    /// Requires a complete exact voting/owner/content target before collecting any observation.
    pub fn validate(&self) -> Result<(), String> {
        let groups = self.membership.membership().get_joint_config();
        let voters: BTreeSet<_> = self.membership.voter_ids().collect();
        if !(1..=2).contains(&groups.len())
            || !hex_digest(&self.profile_identity)
            || self.membership.log_id().is_none()
            || self.epoch.term == 0
            || !voters.contains(&self.epoch.replica_id)
            || self.revision == 0
            || voters.is_empty()
            || voters.len() > MAX_MEMBERS
            || groups.iter().any(BTreeSet::is_empty)
            || self.member_stores.keys().copied().collect::<BTreeSet<_>>() != voters
            || self
                .member_stores
                .values()
                .any(|value| !owner(value, "replica-content:"))
            || self.member_stores.values().collect::<BTreeSet<_>>().len() != voters.len()
            || !hex_digest(&self.checkpoint_digest)
            || !hex_digest(&self.batch_digest)
            || self.inputs.len() > MAX_INPUTS
            || self.inputs.windows(2).any(|keys| keys[0] >= keys[1])
        {
            return Err(
                "content quorum target lacks exact bounded voting/store/input identity".into(),
            );
        }
        for input in &self.inputs {
            input.validate()?;
        }
        if self.inputs.windows(2).any(|keys| {
            keys[0].source_owner == keys[1].source_owner
                && keys[0].content_digest == keys[1].content_digest
        }) {
            return Err("original content identity has conflicting byte counts".into());
        }
        Ok(())
    }

    /// Counts only members which independently observed the complete metadata and content target.
    /// The caller must still verify actual peer TLS/storage and recheck current epoch/membership.
    pub fn complete_members(
        &self,
        observations: &[MemberContentObservation],
    ) -> Result<BTreeSet<u64>, String> {
        self.validate()?;
        if observations.len() > MAX_MEMBERS * 2 {
            return Err("content quorum observations exceed the original member bound".into());
        }
        let mut candidates: BTreeMap<u64, &MemberContentObservation> = BTreeMap::new();
        let mut conflicts = BTreeSet::new();
        for observed in observations {
            if let Some(original) = candidates.insert(observed.member, observed)
                && original != observed
            {
                conflicts.insert(observed.member);
            }
        }
        let complete: BTreeSet<_> = candidates
            .into_iter()
            .filter(|(member, observed)| {
                !conflicts.contains(member)
                    && observed.profile_identity == self.profile_identity
                    && self.member_stores.get(member) == Some(&observed.store_owner)
                    && observed.epoch == self.epoch
                    && observed.revision == self.revision
                    && observed.checkpoint_digest == self.checkpoint_digest
                    && observed.batch_digest == self.batch_digest
                    && observed.membership == self.membership
                    && observed.inputs == self.inputs
            })
            .map(|(member, _)| member)
            .collect();
        if self
            .membership
            .membership()
            .get_joint_config()
            .iter()
            .any(|group| group.intersection(&complete).count() <= group.len() / 2)
        {
            return Err(
                "content and metadata lack the same complete majority in every voting group".into(),
            );
        }
        Ok(complete)
    }
}

#[cfg(test)]
#[path = "content_tests.rs"]
mod tests;
