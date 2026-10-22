//! Quorum-ordered infrastructure leaf lifecycle, independent of application authority revisions.

use crate::*;
use sha2::{Digest as _, Sha256};

#[cfg(test)]
#[path = "certificate_tests.rs"]
mod tests;

/// Maximum immutable rotation operations retained by this bounded deployment profile.
const MAX_CERTIFICATE_COMMANDS: usize = 512;

/// Closed operator request; callers cannot provide their own TLS confirmations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateRequest {
    /// Stable original operation identity, including retries after certificate replacement.
    pub command_id: String,
    /// Exact committed infrastructure revision observed before this operation.
    pub expected_revision: u64,
    /// Stable infrastructure member whose leaf changes, never a robot Node identity.
    pub member: u64,
    /// Explicit stage or original-stage retirement.
    pub action: CertificateAction,
}

/// Separate transitions prevent a staged credential from implicitly revoking its predecessor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub enum CertificateAction {
    /// Admits one exact new leaf while retaining the original credential.
    Stage {
        /// Current committed leaf that must still own this member.
        previous_leaf: String,
        /// New CA-valid leaf whose actual private key must later be demonstrated over TLS.
        new_leaf: String,
    },
    /// Removes the original leaf only after all actual members verify the new credential.
    Retire {
        /// Exact immutable Stage operation, not a newly supplied generation.
        stage_command_id: String,
        /// Original leaf to revoke irreversibly.
        previous_leaf: String,
        /// Original staged replacement that becomes the only current leaf.
        new_leaf: String,
    },
    /// Explicitly abandons the original staged credential, which can never be reused afterwards.
    Abort {
        /// Exact original Stage operation whose pending replacement is being revoked.
        stage_command_id: String,
        /// Original current credential retained by this reduction of authority.
        previous_leaf: String,
        /// Original staged credential to retire irreversibly, even when its private key is lost.
        new_leaf: String,
    },
}

/// Current member credentials; retired digests never become reusable bootstrap identities.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemberCertificates {
    /// Current credential retained until explicit retirement completes.
    pub current: String,
    /// One explicitly staged replacement, accepted alongside the current leaf.
    pub staged: Option<String>,
    /// All irrevocably retired original leaf digests.
    pub retired: BTreeSet<String>,
}

/// Evidence produced by an actual authenticated member and its independent target TLS handshake.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateProbe {
    /// Actual authenticated member endpoint contacted by the leader.
    pub member: u64,
    /// Actual leaf presented by that endpoint, bound again by the leader's TLS connection.
    pub presented_leaf: String,
    /// Original stage revision confirmed at both ends of the target connection.
    pub revision: u64,
    /// Exact complete current trust identity observed by both endpoints.
    pub trust_digest: String,
    /// Stable target member independently contacted by this endpoint.
    pub target: u64,
    /// Actual new target leaf required before any target application bytes were sent.
    pub target_leaf: String,
    /// Canonical digest of the observer TLS root bundle actually loaded on this connection.
    pub observer_ca_bundle_digest: String,
    /// Actual target connection's loaded root bundle, retained as deployment evidence.
    pub target_ca_bundle_digest: String,
}

/// Readonly actual connection material, separate from application authority or device evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateObservation {
    /// Exact authenticated infrastructure endpoint serving this observation.
    pub member: u64,
    /// Its local committed infrastructure revision, which may lag the quorum leader.
    pub revision: u64,
    /// Exact committed trust snapshot identity at observation.
    pub trust_digest: String,
    /// Actual leaf served by this very TLS connection.
    pub leaf: String,
    /// Canonical digest of actual loaded CA DER roots on this connection.
    pub ca_bundle_digest: String,
}

/// Immutable accepted operation; its actual original caller and confirmations survive restart.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateReceipt {
    /// Complete original public request, never rewritten by retry.
    pub request: CertificateRequest,
    /// Actual authenticated infrastructure caller at first acceptance.
    pub caller: u64,
    /// Actual original TLS caller leaf, not a caller-supplied header.
    pub caller_leaf: String,
    /// Infrastructure revision produced by this command, separate from application revision.
    pub revision: u64,
    /// Complete all-member confirmations for retirement; Stage/Abort retain an empty vector.
    pub probes: Vec<CertificateProbe>,
}

/// Closed, snapshot-backed infrastructure authority initialized only by explicit replica v0.3.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateTrust {
    /// Exact versioned authority codec, rejected if changed or absent in v0.3 storage.
    pub schema: String,
    /// Immutable initial pins, bound to the original cluster profile.
    pub bootstrap: BTreeMap<u64, String>,
    /// Highest committed infrastructure command revision.
    pub revision: u64,
    /// Current and retired credentials per original member.
    pub members: BTreeMap<u64, MemberCertificates>,
    /// Original immutable operation evidence in accepted order.
    pub receipts: Vec<CertificateReceipt>,
}

/// Consensus-only mutation; the transport constructs actual caller and probe evidence internally.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CertificateMutation {
    /// Original bounded operator request.
    pub request: CertificateRequest,
    /// Actual authenticated sender; application callers cannot construct this RPC.
    pub caller: u64,
    /// Actual leaf captured from the original sender's mTLS connection.
    pub caller_leaf: String,
    /// Actual leader-collected evidence; never accepted from the administration request.
    pub probes: Vec<CertificateProbe>,
}

/// Distinguishes new infrastructure authority while preserving the original Application batch bytes.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged, deny_unknown_fields)]
pub enum ReplicaCommand {
    /// Explicit complete original recovery/content candidate, refused by metadata-only storage.
    ContentApplication {
        /// Exact private v3 candidate; metadata acceptance alone is not an application success ACK.
        content_application: Box<ContentApplicationBatch>,
    },
    /// Closed infrastructure envelope; legacy profile cannot apply it.
    Certificate {
        /// Original authenticated mutation and its real confirmations.
        certificate: CertificateMutation,
    },
    /// Existing exact opaque application acceptance, with unchanged wire representation.
    Application(state::ApplicationCommitBatch),
}

impl From<state::ApplicationCommitBatch> for ReplicaCommand {
    /// Preserves ordinary application command construction without changing encoded fields.
    fn from(batch: state::ApplicationCommitBatch) -> Self {
        Self::Application(batch)
    }
}

/// Checks a canonical SHA-256 leaf digest without accepting aliases or mixed-case encodings.
pub(crate) fn valid_leaf(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

impl CertificateTrust {
    /// Initializes the exact deployment pins; historical authority must never call this fallback.
    pub(crate) fn initial(bootstrap: BTreeMap<u64, String>) -> Result<Self, String> {
        if !(3..=9).contains(&bootstrap.len())
            || bootstrap.contains_key(&0)
            || bootstrap.values().any(|leaf| !valid_leaf(leaf))
            || bootstrap.values().collect::<BTreeSet<_>>().len() != bootstrap.len()
        {
            return Err("certificate bootstrap identity is invalid".into());
        }
        Ok(Self {
            schema: "roboguide.replica-certificate-trust/v0.1".into(),
            revision: 0,
            members: bootstrap
                .iter()
                .map(|(id, leaf)| {
                    (
                        *id,
                        MemberCertificates {
                            current: leaf.clone(),
                            staged: None,
                            retired: BTreeSet::new(),
                        },
                    )
                })
                .collect(),
            bootstrap,
            receipts: Vec::new(),
        })
    }

    /// Requires the exact current member and leaf; retired credentials cannot authorize traffic.
    pub fn admits(&self, member: u64, leaf: &str) -> bool {
        self.members.get(&member).is_some_and(|entry| {
            !entry.retired.contains(leaf)
                && (entry.current == leaf || entry.staged.as_deref() == Some(leaf))
        })
    }

    /// Hashes complete committed trust and original receipts for independent endpoint confirmations.
    pub fn digest(&self) -> String {
        format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(self).expect("scalar trust encodes"))
        )
    }

    /// Finds an immutable command before any new retirement probes or consensus effects.
    pub(crate) fn original(
        &self,
        request: &CertificateRequest,
        caller: u64,
    ) -> Result<Option<CertificateReceipt>, String> {
        let Some(receipt) = self
            .receipts
            .iter()
            .find(|entry| entry.request.command_id == request.command_id)
        else {
            return Ok(None);
        };
        if receipt.request != *request || receipt.caller != caller {
            return Err(
                "certificate command id conflicts with its original request or caller".into(),
            );
        }
        Ok(Some(receipt.clone()))
    }

    /// Replays all bounded original records to detect missing, altered or resurrected authority.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.replica-certificate-trust/v0.1"
            || self.receipts.len() > MAX_CERTIFICATE_COMMANDS
            || self.revision != self.receipts.len() as u64
        {
            return Err("certificate authority schema/revision/bounds differ".into());
        }
        let mut rebuilt = Self::initial(self.bootstrap.clone())?;
        for receipt in &self.receipts {
            let applied = rebuilt.apply(CertificateMutation {
                request: receipt.request.clone(),
                caller: receipt.caller,
                caller_leaf: receipt.caller_leaf.clone(),
                probes: receipt.probes.clone(),
            })?;
            if applied != *receipt {
                return Err("certificate original receipt differs".into());
            }
        }
        if rebuilt != *self {
            return Err("certificate current projection differs from original evidence".into());
        }
        Ok(())
    }

    /// Applies one deterministic accepted transition without changing application evidence or budgets.
    pub(crate) fn apply(
        &mut self,
        mutation: CertificateMutation,
    ) -> Result<CertificateReceipt, String> {
        let request = &mutation.request;
        if !self.admits(mutation.caller, &mutation.caller_leaf) {
            return Err("current certificate caller is not admitted".into());
        }
        if let Some(original) = self.original(request, mutation.caller)? {
            return Ok(original);
        }
        if request.command_id.trim().is_empty()
            || request.command_id.len() > 128
            || request.expected_revision != self.revision
            || self.receipts.len() >= MAX_CERTIFICATE_COMMANDS
        {
            return Err("certificate request revision/caller/identity/bounds differ".into());
        }
        let entry = self
            .members
            .get(&request.member)
            .ok_or("certificate target is not enrolled")?;
        match &request.action {
            CertificateAction::Stage {
                previous_leaf,
                new_leaf,
            } => {
                if entry.current != *previous_leaf
                    || !valid_leaf(new_leaf)
                    || self.receipts.len().saturating_add(2) > MAX_CERTIFICATE_COMMANDS
                    || !mutation.probes.is_empty()
                    || self.members.values().any(|member| {
                        member.staged.is_some()
                            || member.current == *new_leaf
                            || member.retired.contains(new_leaf)
                    })
                {
                    return Err(
                        "certificate stage has stale owner, pending stage or reused leaf".into(),
                    );
                }
            }
            CertificateAction::Retire {
                stage_command_id,
                previous_leaf,
                new_leaf,
            }
            | CertificateAction::Abort {
                stage_command_id,
                previous_leaf,
                new_leaf,
            } => {
                let stage = self
                    .receipts
                    .iter()
                    .find(|receipt| receipt.request.command_id == *stage_command_id)
                    .ok_or("certificate retirement has no original stage")?;
                if stage.request.member != request.member
                    || stage.revision != self.revision
                    || stage.request.action
                        != (CertificateAction::Stage {
                            previous_leaf: previous_leaf.clone(),
                            new_leaf: new_leaf.clone(),
                        })
                    || entry.current != *previous_leaf
                    || entry.staged.as_deref() != Some(new_leaf.as_str())
                {
                    return Err(
                        "certificate retirement differs from original stage or member coverage"
                            .into(),
                    );
                }
                if matches!(request.action, CertificateAction::Abort { .. }) {
                    if !mutation.probes.is_empty() {
                        return Err("abort cannot assert retirement TLS confirmations".into());
                    }
                } else {
                    if mutation.probes.len() != self.members.len() {
                        return Err("retirement lacks all original member proofs".into());
                    }
                    let digest = self.digest();
                    let mut covered = BTreeSet::new();
                    for probe in &mutation.probes {
                        if !covered.insert(probe.member)
                            || !self.admits(probe.member, &probe.presented_leaf)
                            || probe.revision != self.revision
                            || probe.trust_digest != digest
                            || probe.target != request.member
                            || probe.target_leaf != *new_leaf
                            || !valid_leaf(&probe.observer_ca_bundle_digest)
                            || !valid_leaf(&probe.target_ca_bundle_digest)
                        {
                            return Err(
                                "certificate retirement lacks exact fresh all-member TLS evidence"
                                    .into(),
                            );
                        }
                    }
                    if covered != self.members.keys().copied().collect() {
                        return Err("certificate retirement member coverage differs".into());
                    }
                }
            }
        }
        let entry = self
            .members
            .get_mut(&request.member)
            .expect("validated target exists");
        match &request.action {
            CertificateAction::Stage { new_leaf, .. } => entry.staged = Some(new_leaf.clone()),
            CertificateAction::Retire {
                previous_leaf,
                new_leaf,
                ..
            } => {
                entry.retired.insert(previous_leaf.clone());
                entry.current = new_leaf.clone();
                entry.staged = None;
            }
            CertificateAction::Abort { new_leaf, .. } => {
                entry.retired.insert(new_leaf.clone());
                entry.staged = None;
            }
        }
        self.revision += 1;
        let receipt = CertificateReceipt {
            request: mutation.request,
            caller: mutation.caller,
            caller_leaf: mutation.caller_leaf,
            revision: self.revision,
            probes: mutation.probes,
        };
        self.receipts.push(receipt.clone());
        Ok(receipt)
    }
}

impl ReplicaNode {
    /// Returns current original trust only in the explicit new infrastructure profile.
    pub fn current_certificate_trust(&self) -> Result<CertificateTrust, String> {
        if !self.config.rotates_certificates() {
            return Err("legacy profile has no certificate lifecycle authority".into());
        }
        self.store
            .certificate_trust()?
            .ok_or("original certificate authority is missing".into())
    }

    /// Requires one exact original stage before answering any independent observation request.
    fn require_certificate_stage(
        &self,
        revision: u64,
        digest: &str,
    ) -> Result<CertificateTrust, String> {
        let trust = self.current_certificate_trust()?;
        if trust.revision != revision || trust.digest() != digest {
            return Err("certificate observation differs from the original committed stage".into());
        }
        Ok(trust)
    }

    /// Binds a readonly target reply to the leaf actually served on this exact TLS connection.
    pub(crate) fn confirm_certificate_target(
        &self,
        revision: u64,
        digest: String,
        served_leaf: &str,
        ca_bundle_digest: &str,
    ) -> Result<CertificateObservation, String> {
        let trust = self.require_certificate_stage(revision, &digest)?;
        if !trust.admits(self.config.replica_id, served_leaf) {
            return Err("actual served target leaf is no longer admitted".into());
        }
        Ok(CertificateObservation {
            member: self.config.replica_id,
            revision,
            trust_digest: digest,
            leaf: served_leaf.into(),
            ca_bundle_digest: ca_bundle_digest.into(),
        })
    }

    /// Independently contacts the original target using its actual staged private key over mutual TLS.
    pub(crate) async fn probe_certificate(
        &self,
        revision: u64,
        digest: String,
        target: u64,
        target_leaf: String,
        served_leaf: &str,
        observer_ca_bundle_digest: &str,
    ) -> Result<CertificateProbe, String> {
        let trust = self.require_certificate_stage(revision, &digest)?;
        if trust
            .members
            .get(&target)
            .and_then(|member| member.staged.as_deref())
            != Some(target_leaf.as_str())
            || !trust.admits(self.config.replica_id, served_leaf)
        {
            return Err(
                "TLS probe target/credential differs from the original staged owner".into(),
            );
        }
        let target_observation =
            transport::certificate_target(self, target, revision, &digest, &target_leaf).await?;
        self.require_certificate_stage(revision, &digest)?;
        Ok(CertificateProbe {
            member: self.config.replica_id,
            presented_leaf: served_leaf.into(),
            revision,
            trust_digest: digest,
            target,
            target_leaf,
            observer_ca_bundle_digest: observer_ca_bundle_digest.into(),
            target_ca_bundle_digest: target_observation.ca_bundle_digest,
        })
    }

    /// Commits the exact operation only after independently obtaining actual all-member TLS evidence.
    pub(crate) async fn rotate_certificate(
        &self,
        epoch: ControllerEpoch,
        request: CertificateRequest,
        caller: u64,
        caller_leaf: &str,
    ) -> Result<CertificateReceipt, String> {
        self.check_epoch(epoch).await?;
        let trust = self.current_certificate_trust()?;
        if !trust.admits(caller, caller_leaf) {
            return Err("original TLS caller is no longer admitted".into());
        }
        if let Some(original) = trust.original(&request, caller)? {
            return Ok(original);
        }
        if request.expected_revision != trust.revision {
            return Err("certificate expected revision is stale".into());
        }
        let probes = match &request.action {
            CertificateAction::Stage { .. } | CertificateAction::Abort { .. } => Vec::new(),
            CertificateAction::Retire {
                stage_command_id,
                previous_leaf,
                new_leaf,
            } => {
                let original_stage = trust
                    .receipts
                    .iter()
                    .find(|receipt| receipt.request.command_id == *stage_command_id)
                    .ok_or("retirement has no original stage")?;
                if original_stage.request.member != request.member
                    || original_stage.revision != trust.revision
                    || original_stage.request.action
                        != (CertificateAction::Stage {
                            previous_leaf: previous_leaf.clone(),
                            new_leaf: new_leaf.clone(),
                        })
                {
                    return Err(
                        "retirement does not reference the exact current original stage".into(),
                    );
                }
                let mut tasks = tokio::task::JoinSet::new();
                let digest = trust.digest();
                for member in trust.members.keys().copied() {
                    let node = self.clone();
                    let digest = digest.clone();
                    let new_leaf = new_leaf.clone();
                    let target = request.member;
                    let revision = trust.revision;
                    tasks.spawn(async move {
                        transport::certificate_probe(
                            &node, member, target, revision, &digest, &new_leaf,
                        )
                        .await
                    });
                }
                let mut probes = Vec::new();
                while let Some(result) = tasks.join_next().await {
                    probes.push(result.map_err(|error| error.to_string())??);
                }
                probes.sort_by_key(|probe| probe.member);
                probes
            }
        };
        let mutation = CertificateMutation {
            request,
            caller,
            caller_leaf: caller_leaf.into(),
            probes,
        };
        let mut candidate = trust;
        candidate.apply(mutation.clone())?;
        self.check_epoch(epoch).await?;
        let reply = tokio::time::timeout(
            Duration::from_secs(5),
            self.raft.client_write(ReplicaCommand::Certificate {
                certificate: mutation,
            }),
        )
        .await
        .map_err(|_| {
            "certificate quorum acceptance outcome is uncertain; observe the original command"
                .to_string()
        })?
        .map_err(|error| error.to_string())?;
        self.check_epoch(epoch).await?;
        reply.data.certificate.ok_or_else(|| {
            reply
                .data
                .rejection
                .unwrap_or_else(|| "certificate quorum command was rejected".into())
        })
    }
}
