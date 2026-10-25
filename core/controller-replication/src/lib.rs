#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Quorum durability for one Controller authority; replicas never match, bind or execute work.

mod application_client;
mod archive_parent;
mod certificates;
mod content;
mod content_application;
#[cfg(test)]
mod content_application_tests;
mod content_archive;
#[cfg(test)]
mod content_archive_tests;
mod content_store;
mod content_uploads;
mod controller_ingress;
mod member;
mod network_content;
mod network_initialization;
mod network_membership;
mod snapshot_data;
mod source_initialization;
mod store;
mod supervision;
#[cfg(test)]
mod tests;
mod tls;
mod transport;
mod transport_budget;
mod upgrade_network;
mod upgrades;

use openraft::Raft;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

pub use application_client::ApplicationReplicaClient;
pub use certificates::{
    CertificateAction, CertificateMutation, CertificateObservation, CertificateProbe,
    CertificateReceipt, CertificateRequest, CertificateTrust, MemberCertificates, ReplicaCommand,
};
pub use content::{ArtifactContentKey, ContentQuorumTarget, MemberContentObservation};
pub use content_application::{
    AcceptedContentState, ApplicationArtifactWriter, CONTENT_APPLICATION_SCHEMA,
    CONTENT_ARCHIVE_APPLICATION_SCHEMA, CONTENT_INITIALIZATION_SCHEMA, ContentApplicationBatch,
    ContentInitializationAnchor, ContentInitializationPolicy,
};
pub use content_store::{
    ContentProcessEndObservation, REPLICA_CONTENT_GC_LAYOUT_SCHEMA, REPLICA_CONTENT_LAYOUT_SCHEMA,
    REPLICA_CONTENT_PRODUCER_LAYOUT_SCHEMA, REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA,
    REPLICA_CONTENT_STAGING_GC_LAYOUT_SCHEMA, REPLICA_CONTENT_STORAGE_SCHEMA,
    REPLICA_CONTENT_STREAM_LAYOUT_SCHEMA, ReplicaContentCopyStatus, ReplicaContentGcStatus,
    ReplicaContentIdentity, ReplicaContentProducerKind, ReplicaContentProducerSource,
    ReplicaContentProducerStatus, ReplicaContentReadLease, ReplicaContentReader,
    ReplicaContentReaderReceipt, ReplicaContentReaderStatus, ReplicaContentReceipt,
    ReplicaContentReference, ReplicaContentReferenceKind, ReplicaContentStore,
    ReplicaContentStreamStatus, ReplicaSnapshotCloseStatus, ReplicaStagingGcStatus,
    ReplicaStagingTarget, SnapshotContentHeader, SnapshotContentReady, SnapshotContentStatus,
};
pub use controller_ingress::{
    ControllerContentClient, ControllerContentClientConfig, ControllerContentRead,
    ControllerIngressServer, ControllerIngressStatus,
};
pub use member::ReplicaMember;
pub use network_initialization::inspect_archived_source;
pub use snapshot_data::ReplicaSnapshotData;
pub use source_initialization::{
    ContentInitializationInput, ContentInitializationSourceObservation, ContentInitializationStatus,
};
pub use store::ReplicaContentParentAdoptionStatus;
pub use store::{
    AcceptedApplicationIdentity, HistoryAnchor, ReplicaView, SqliteRaftStore,
    UnavailableReplayInput,
};
pub use supervision::{ControllerDrainProof, ControllerProcessExit, ControllerProcessIdentity};
pub use transport::{ReplicaFenceClient, ReplicaServer};
pub use transport_budget::{ReplicaTransportLimits, ReplicaTransportSnapshot};
pub use upgrades::{
    ApplicationUpgradeAbort, ApplicationUpgradeAuthority, ApplicationUpgradeDrain,
    ApplicationUpgradeIntent, ApplicationUpgradeReceipt, ApplicationUpgradeRequest,
    ApplicationUpgradeSupport, UpgradeBinary, supports_application_generation,
};

openraft::declare_raft_types!(
    /// Consensus types use infrastructure replica identities independently of embodied Node identities.
    pub ControllerRaftConfig:
        D = ReplicaCommand,
        R = CommitReceipt,
        NodeId = u64,
        Node = ReplicaMember,
        SnapshotData = ReplicaSnapshotData
);

/// Existing OpenRaft election, log ordering, quorum and snapshot engine.
pub type ControllerRaft = Raft<ControllerRaftConfig>;

/// Readonly infrastructure readiness; process liveness and role labels never grant content authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaReadiness {
    /// Closed observation interpretation.
    pub schema: String,
    /// Actual configured infrastructure member.
    pub member: u64,
    /// Actual OpenRaft role at the observation sample.
    pub role: String,
    /// Actual current term, independent of application revision.
    pub term: u64,
    /// Actual current leader, if this process has observed one.
    pub leader: Option<u64>,
    /// Actual local last log position.
    pub last_log_index: Option<u64>,
    /// Actual local applied position.
    pub last_applied_index: Option<u64>,
    /// Number of actual current/joint voting configurations.
    pub joint_groups: usize,
    /// Actual committed voter identities.
    pub voters: BTreeSet<u64>,
    /// Metadata/quorum observation is separate from content readiness.
    pub metadata_ready: bool,
    /// Same-voter complete content majority, never process liveness.
    pub content_ready: bool,
    /// Members that independently supplied complete current content evidence.
    pub complete_content_members: BTreeSet<u64>,
    /// Bounded reason when a readiness dimension is unavailable or refused.
    pub reason: Option<String>,
}

/// Original issuing Controller epoch; no replica identifier is a physical Node selector.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerEpoch {
    /// Raft election term, persisted by the consensus log/vote authority.
    pub term: u64,
    /// Infrastructure replica that issued the candidate.
    pub replica_id: u64,
}

/// Mutual-TLS material required when replica RPC crosses host/container trust boundaries.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaTlsConfig {
    /// CA certificate bundle trusted for peer server and client certificates.
    pub ca_cert_path: std::path::PathBuf,
    /// This member's certificate chain in PEM format.
    pub cert_path: std::path::PathBuf,
    /// This member's private key in PEM format.
    pub key_path: std::path::PathBuf,
    /// DNS name asserted by the peer certificates.
    pub server_name: String,
    /// Exact leaf certificate digest expected for each infrastructure member.
    pub peer_certificate_digests: BTreeMap<u64, String>,
}

/// Quorum-applied application outcome; rejection advances no accepted checkpoint/evidence history.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommitReceipt {
    /// Exact target passed every base/tail/codec requirement and is durable.
    pub accepted: bool,
    /// Latest accepted application revision.
    pub revision: u64,
    /// Exact accepted complete checkpoint digest.
    pub checkpoint_digest: Option<String>,
    /// Bounded rejection description, never interpreted as lifecycle policy.
    pub rejection: Option<String>,
    /// Separate infrastructure outcome; absent on ordinary application acceptance.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub certificate: Option<CertificateReceipt>,
}

/// Software-only leader handoff receipt; it never asserts physical continuity or device safety.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LeaderTransferReceipt {
    /// Stable caller-supplied operation identity for retries and audit correlation.
    pub transfer_id: String,
    /// Epoch that was required to remain current while the request was admitted.
    pub source_epoch: ControllerEpoch,
    /// Pre-enrolled infrastructure voter selected as the handoff target.
    pub target: u64,
    /// `requested` means target election was triggered; caller must observe a new target epoch.
    pub state: String,
}

/// Member-local handoff evidence retained across process restart, never Raft snapshot authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LeaderTransferRecord {
    /// Stable caller-supplied operation identity.
    pub transfer_id: String,
    /// Source leader epoch that authorized the handoff.
    pub source_epoch: ControllerEpoch,
    /// Pre-enrolled voter selected as the target.
    pub target: u64,
    /// Last log index the target had to contain before the request was sent.
    pub required_last_log_index: Option<u64>,
    /// Monotonic durable state: prepared, accepted, target-elected, completed, rejected or unknown.
    pub state: String,
    /// Exact target epoch independently confirmed by quorum; absence never proves completion.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confirmed_epoch: Option<ControllerEpoch>,
    /// Exact original source child frozen before fencing; absent only with a verified empty set.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_process: Option<ControllerProcessIdentity>,
    /// Original supervisor wait or scoped actual empty-process proof, mandatory before election.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_drain: Option<ControllerDrainProof>,
}

/// Authenticated member-side administration client; it opens no consensus storage or child.
pub struct ReplicaAdminClient {
    /// Complete startup-frozen cluster profile and member credential material.
    config: ReplicaConfig,
    /// Current leader endpoint selected by an operator or deployment supervisor.
    leader: u64,
    /// Actual current member credentials and optional original committed trust reader.
    tls: Option<Arc<tls::TlsMaterial>>,
}

impl ReplicaAdminClient {
    /// Observes one exact member-local original operation without requiring or creating leadership.
    pub async fn leader_transfer_record(
        &self,
        transfer_id: &str,
    ) -> Result<Option<LeaderTransferRecord>, String> {
        transport::admin_observe_leader_transfer(
            &self.config,
            self.leader,
            self.tls.clone(),
            transfer_id,
        )
        .await
    }

    /// Validates a pre-enrolled member profile before constructing a private admin client.
    pub fn new(config: ReplicaConfig, leader: u64) -> Result<Self, String> {
        config.validate()?;
        if !config.members.contains_key(&leader) {
            return Err("administration leader is not pre-enrolled".into());
        }
        if config.rotates_certificates() {
            return Err("replica v0.3 administration requires its original trust database".into());
        }
        let tls = tls::load(config.tls.as_ref())?.map(Arc::new);
        Ok(Self {
            config,
            leader,
            tls,
        })
    }

    /// Uses the original local replica authority for exact current target pins during rotation.
    pub fn with_trust_database(
        config: ReplicaConfig,
        leader: u64,
        path: &Path,
    ) -> Result<Self, String> {
        config.validate()?;
        if !config.rotates_certificates() || !config.members.contains_key(&leader) {
            return Err(
                "current trust administration requires replica v0.3 and an enrolled leader".into(),
            );
        }
        let tls = tls::with_readonly_trust(
            config.tls.as_ref().ok_or("rotation requires TLS")?,
            path,
            &config.profile_identity(),
            config.replica_id,
        )?;
        Ok(Self {
            config,
            leader,
            tls: Some(Arc::new(tls)),
        })
    }

    /// Reads the current leader epoch, rejecting a follower or unavailable endpoint.
    pub async fn epoch(&self) -> Result<ControllerEpoch, String> {
        transport::admin_epoch(&self.config, self.leader, self.tls.clone())
            .await?
            .ok_or_else(|| "administration endpoint is not the current leader".into())
    }

    /// Reads the current committed voter set for an operator audit.
    pub async fn membership(&self) -> Result<BTreeSet<u64>, String> {
        transport::admin_membership(&self.config, self.leader, self.tls.clone()).await
    }

    /// Adds one configured member as a learner and waits for its log catch-up.
    pub async fn add_learner(&self, member_id: u64) -> Result<(), String> {
        let epoch = self.epoch().await?;
        transport::admin_add_learner(
            &self.config,
            self.leader,
            self.tls.clone(),
            epoch,
            member_id,
        )
        .await
    }

    /// Applies one voter set through OpenRaft joint consensus.
    pub async fn change_membership(
        &self,
        voters: BTreeSet<u64>,
        retain_removed_as_learners: bool,
    ) -> Result<(), String> {
        let epoch = self.epoch().await?;
        transport::admin_change_membership(
            &self.config,
            self.leader,
            self.tls.clone(),
            epoch,
            voters,
            retain_removed_as_learners,
        )
        .await
    }

    /// Requests controlled target-side election under the observed current leader epoch.
    pub async fn request_leader_transfer(
        &self,
        target: u64,
        transfer_id: impl Into<String>,
    ) -> Result<LeaderTransferReceipt, String> {
        let epoch = self.epoch().await?;
        transport::admin_request_transfer(
            &self.config,
            self.leader,
            self.tls.clone(),
            epoch,
            target,
            transfer_id.into(),
        )
        .await
    }

    /// Reads quorum-current trust without creating or repeating a rotation operation.
    pub async fn certificate_trust(&self) -> Result<CertificateTrust, String> {
        transport::admin_certificate_trust(&self.config, self.leader, self.tls.clone()).await
    }

    /// Observes the selected endpoint's actual leaf/root material without claiming quorum.
    pub async fn certificate_observation(&self) -> Result<CertificateObservation, String> {
        transport::admin_certificate_observation(&self.config, self.leader, self.tls.clone()).await
    }

    /// Performs one original authenticated generation operation; caller observes uncertainty separately.
    pub async fn upgrade_application_generation(
        &self,
        request: ApplicationUpgradeRequest,
    ) -> Result<ApplicationUpgradeReceipt, String> {
        let epoch = self.epoch().await?;
        let budget = request.budget_ms;
        match transport::admin_upgrade(
            &self.config,
            self.leader,
            self.tls.clone(),
            upgrade_network::UpgradeWire::Activate { epoch, request },
            budget,
        )
        .await?
        {
            upgrade_network::UpgradeReply::Activated(receipt) => Ok(receipt),
            _ => Err("application upgrade did not return an original activation receipt".into()),
        }
    }

    /// Reads actual retained authority/pending IDs without sending another stop or activation.
    pub async fn observe_application_upgrade(
        &self,
    ) -> Result<(Option<ApplicationUpgradeAuthority>, Vec<String>), String> {
        match transport::admin_upgrade(
            &self.config,
            self.leader,
            self.tls.clone(),
            upgrade_network::UpgradeWire::Observe,
            5000,
        )
        .await?
        {
            upgrade_network::UpgradeReply::Observation { authority, pending } => {
                Ok((authority, pending))
            }
            _ => Err("application upgrade observation reply differs".into()),
        }
    }

    /// Abandons only a genuinely retained uncertain operation; active generations cannot roll back.
    pub async fn abort_application_generation(
        &self,
        original_command_id: String,
        command_id: String,
    ) -> Result<ApplicationUpgradeAbort, String> {
        let epoch = self.epoch().await?;
        match transport::admin_upgrade(
            &self.config,
            self.leader,
            self.tls.clone(),
            upgrade_network::UpgradeWire::Abort {
                epoch,
                original_command_id,
                command_id,
            },
            15000,
        )
        .await?
        {
            upgrade_network::UpgradeReply::Aborted(receipt) => Ok(receipt),
            _ => Err("application upgrade abandonment did not return its original receipt".into()),
        }
    }

    /// Submits only the closed request; the leader independently collects actual TLS evidence.
    pub async fn rotate_certificate(
        &self,
        request: CertificateRequest,
    ) -> Result<CertificateReceipt, String> {
        let epoch = self.epoch().await?;
        transport::admin_rotate_certificate(
            &self.config,
            self.leader,
            self.tls.clone(),
            epoch,
            request,
        )
        .await
    }
}

/// Startup-frozen, bounded three-member Controller replica profile.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaConfig {
    /// Current declarative configuration version.
    pub schema: String,
    /// Stable cluster identity independent of user Missions and physical entities.
    pub cluster_id: String,
    /// This process's infrastructure member identity.
    pub replica_id: u64,
    /// Three immutable RPC endpoints keyed by infrastructure member identity.
    pub members: BTreeMap<u64, SocketAddr>,
    /// Initial voters; additional pre-enrolled members start as learners and require an explicit change.
    #[serde(default)]
    pub initial_voters: Option<BTreeSet<u64>>,
    /// Exact application checkpoint schema admitted by this cluster.
    pub application_schema: String,
    /// Startup-owned application policy identity, identical on all members.
    pub application_policy_digest: String,
    /// Optional shared transport secret for non-loopback-capable deployments.
    #[serde(default)]
    pub shared_secret: Option<String>,
    /// Optional mandatory mTLS profile; required whenever a member is non-loopback.
    #[serde(default)]
    pub tls: Option<ReplicaTlsConfig>,
}

impl ReplicaConfig {
    /// Names only the explicit network content generation; legacy configuration retains its old meaning.
    pub fn streams_content(&self) -> bool {
        self.schema == "roboguide.controller-replica/v0.4"
    }

    /// New trust authority is never hidden inside legacy single-pin deployment profiles.
    pub fn rotates_certificates(&self) -> bool {
        self.schema == "roboguide.controller-replica/v0.3" || self.streams_content()
    }
    /// Returns the startup voter set while preserving the v0.1 three-member compatibility profile.
    pub fn initial_voters(&self) -> BTreeSet<u64> {
        self.initial_voters
            .clone()
            .unwrap_or_else(|| self.members.keys().copied().collect())
    }

    /// Rejects unsupported membership/network scope before creating any consensus authority.
    pub fn validate(&self) -> Result<(), String> {
        let initial_voters = self.initial_voters();
        if !(self.schema == "roboguide.controller-replica/v0.1"
            || self.schema == "roboguide.controller-replica/v0.2"
            || self.rotates_certificates())
            || self.cluster_id.trim().is_empty()
            || self.cluster_id.len() > 128
            || self.members.len() < 3
            || self.members.len() > 9
            || !self.members.contains_key(&self.replica_id)
            || self.members.contains_key(&0)
            || self.members.values().any(|address| address.port() == 0)
            || initial_voters.is_empty()
            || initial_voters.len() > self.members.len()
            || !initial_voters
                .iter()
                .all(|id| self.members.contains_key(id))
            || (self.schema == "roboguide.controller-replica/v0.1"
                && (self.members.len() != 3 || self.initial_voters.is_some()))
            || ((self.schema == "roboguide.controller-replica/v0.2" || self.rotates_certificates())
                && self.initial_voters.is_none())
            || (self.rotates_certificates()
                && self
                    .tls
                    .as_ref()
                    .is_none_or(|tls| tls.cert_path != tls.key_path))
            || (self
                .members
                .values()
                .any(|address| !address.ip().is_loopback())
                && self.tls.is_none())
            || self.application_schema.trim().is_empty()
            || (self.streams_content()
                && !supports_application_generation(&self.application_schema))
            || self.application_policy_digest.trim().is_empty()
            || self
                .shared_secret
                .as_ref()
                .is_some_and(|secret| secret.len() < 32 || secret.len() > 256)
        {
            return Err("unsupported or incomplete controller replica profile".into());
        }
        if let Some(tls) = &self.tls
            && (tls.server_name.trim().is_empty()
                || tls.server_name.len() > 253
                || !tls.ca_cert_path.is_absolute()
                || !tls.cert_path.is_absolute()
                || !tls.key_path.is_absolute()
                || tls.peer_certificate_digests.len() != self.members.len()
                || self.members.keys().any(|member| {
                    tls.peer_certificate_digests
                        .get(member)
                        .is_none_or(|digest| {
                            !digest.strip_prefix("sha256:").is_some_and(|value| {
                                value.len() == 64
                                    && value.bytes().all(|byte| {
                                        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                                    })
                            })
                        })
                })
                || tls
                    .peer_certificate_digests
                    .values()
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    != self.members.len())
        {
            return Err("replica TLS paths/name must be absolute and bounded".into());
        }
        let unique = self
            .members
            .values()
            .collect::<std::collections::BTreeSet<_>>();
        if unique.len() != self.members.len() {
            return Err("controller replica endpoints must be distinct".into());
        }
        Ok(())
    }

    /// Hashes immutable membership, application schema and policy without this process's local identity.
    pub fn profile_identity(&self) -> String {
        use sha2::{Digest as _, Sha256};
        let bytes = serde_json::to_vec(&serde_json::json!({
            "schema":self.schema,"cluster_id":self.cluster_id,"members":self.members,
            "initial_voters":self.initial_voters(),
            "application_schema":self.application_schema,"application_policy_digest":self.application_policy_digest,
            "transport_auth":self.shared_secret.as_ref().map(|secret| {
                format!("{:x}", sha2::Sha256::digest(secret.as_bytes()))
            }),
            "transport_tls": self.tls.as_ref().map(|tls| {
                serde_json::json!({"server_name": &tls.server_name, "peer_certificate_digests": &tls.peer_certificate_digests})
            }),
        })).expect("validated scalar replica configuration encodes");
        format!("{:x}", Sha256::digest(bytes))
    }
}

/// Live consensus service and durable accepted history, independent of any executing Controller child.
#[derive(Clone)]
pub struct ReplicaNode {
    /// Weak running-server context for real Snapshot prerequisites; it cannot retain a Raft ownership cycle.
    network_source: Arc<std::sync::OnceLock<std::sync::Weak<ReplicaNode>>>,
    /// Explicit initial/pre-enrolled root declarations; existing committed member data stays authoritative.
    content_bootstrap: Option<Arc<BTreeMap<u64, String>>>,
    /// Actual local network lifetimes and capacity; never replicated authority.
    transport_budget: Arc<transport_budget::TransportBudget>,
    /// Actual kernel-generated identity of this infrastructure process incarnation.
    process_instance: String,
    /// Test-only response loss after actual target-side acceptance/election; never a product mode.
    #[cfg(test)]
    discard_transfer_reply: Arc<std::sync::atomic::AtomicBool>,
    /// Prevents overlapping temporary heartbeat/election suspension for one original source.
    leader_transfer_writer: Arc<tokio::sync::Mutex<()>>,
    /// Owned complete-content transitions retain admission until their actual Raft result.
    content_transition: Arc<tokio::sync::Semaphore>,
    /// Test-only actual framed-ingress ordinal, never an authority or success receipt.
    #[cfg(test)]
    framed_member_mutations: Arc<std::sync::atomic::AtomicU64>,
    /// Serializes authenticated member mutations; consensus traffic never waits on this gate.
    pub(crate) member_writer: Arc<tokio::sync::Mutex<()>>,
    /// Private network fault switch for deterministic partition verification only.
    network_online: Arc<std::sync::atomic::AtomicBool>,
    /// Infrastructure identity and exact cluster profile.
    config: Arc<ReplicaConfig>,
    /// Mature consensus protocol implementation.
    raft: ControllerRaft,
    /// Durable accepted history and consensus storage.
    store: SqliteRaftStore,
    /// Optional loaded mutual-TLS material shared by the listener and Raft clients.
    pub(crate) tls: Option<Arc<tls::TlsMaterial>>,
    /// Explicit owned-profile observation window; legacy constructors retain one original query.
    quorum_observation_ms: Option<u64>,
    /// Actual locally configured next executable and original infrastructure executable digest.
    upgrade_binary:
        Arc<std::sync::RwLock<BTreeMap<String, (upgrades::LocalUpgradeBinary, String)>>>,
}

impl ReplicaNode {
    /// Opens durable storage, binds private RPC and initializes only the lowest member's empty log.
    pub async fn start(
        config: ReplicaConfig,
        path: &Path,
    ) -> Result<(Self, ReplicaServer), String> {
        Self::start_with_transport_limits(config, path, ReplicaTransportLimits::default()).await
    }

    /// Applies explicit local resource capacities before opening storage or binding any RPC socket.
    pub async fn start_with_transport_limits(
        config: ReplicaConfig,
        path: &Path,
        limits: ReplicaTransportLimits,
    ) -> Result<(Self, ReplicaServer), String> {
        Self::start_inner(config, path, limits, None, false, None, None, false).await
    }

    /// Starts only explicit v0.4 with actual held Root4 and declared original bootstrap roots.
    /// This core constructor does not establish the runnable supervisor's child/source contract.
    pub async fn start_with_content(
        config: ReplicaConfig,
        path: &Path,
        root: ReplicaContentStore,
        bootstrap_owners: BTreeMap<u64, String>,
        limits: ReplicaTransportLimits,
    ) -> Result<(Self, ReplicaServer), String> {
        Self::start_inner(
            config,
            path,
            limits,
            Some((root, bootstrap_owners)),
            false,
            None,
            None,
            false,
        )
        .await
    }

    /// Starts explicit parent8 original-child authority beside unchanged core v0.4 network semantics.
    pub async fn start_with_controller_content(
        config: ReplicaConfig,
        path: &Path,
        root: ReplicaContentStore,
        bootstrap_owners: BTreeMap<u64, String>,
        limits: ReplicaTransportLimits,
    ) -> Result<(Self, ReplicaServer), String> {
        Self::start_inner(
            config,
            path,
            limits,
            Some((root, bootstrap_owners)),
            true,
            None,
            None,
            false,
        )
        .await
    }

    /// Starts an explicit owned profile with its immutable bounded quorum observation declaration.
    pub async fn start_with_controller_content_window(
        config: ReplicaConfig,
        path: &Path,
        root: ReplicaContentStore,
        bootstrap_owners: BTreeMap<u64, String>,
        limits: ReplicaTransportLimits,
        quorum_observation_ms: u64,
    ) -> Result<(Self, ReplicaServer), String> {
        if !(100..=30000).contains(&quorum_observation_ms) {
            return Err("owned quorum observation window must be 100..30000 ms".into());
        }
        Self::start_inner(
            config,
            path,
            limits,
            Some((root, bootstrap_owners)),
            true,
            Some(quorum_observation_ms),
            None,
            false,
        )
        .await
    }

    /// Starts only explicit parent9/Root8, preserving original owned-child and source declarations.
    pub async fn start_with_content_initialization(
        config: ReplicaConfig,
        path: &Path,
        root: ReplicaContentStore,
        bootstrap_owners: BTreeMap<u64, String>,
        limits: ReplicaTransportLimits,
        policy: Option<ContentInitializationPolicy>,
        quorum_observation_ms: Option<u64>,
    ) -> Result<(Self, ReplicaServer), String> {
        if quorum_observation_ms.is_some_and(|value| !(100..=30_000).contains(&value)) {
            return Err("owned quorum observation window must be 100..30000 ms".into());
        }
        if let Some(policy) = &policy {
            policy.validate()?;
            if !config.members.contains_key(&policy.source_member)
                || config
                    .initial_voters
                    .as_ref()
                    .is_some_and(|voters| !voters.contains(&policy.source_member))
            {
                return Err("initialization source must be an original configured voter".into());
            }
        }
        Self::start_inner(
            config,
            path,
            limits,
            Some((root, bootstrap_owners)),
            true,
            quorum_observation_ms,
            Some(policy),
            false,
        )
        .await
    }

    /// Starts only explicit parent11 with the original owned-child/base initialization declaration.
    pub async fn start_with_coordinated_archive(
        config: ReplicaConfig,
        path: &Path,
        root: ReplicaContentStore,
        owners: BTreeMap<u64, String>,
        limits: ReplicaTransportLimits,
        policy: Option<Option<ContentInitializationPolicy>>,
        observation_ms: Option<u64>,
    ) -> Result<(Self, ReplicaServer), String> {
        if observation_ms.is_some_and(|v| !(100..=30_000).contains(&v)) {
            return Err("owned quorum observation window must be 100..30000 ms".into());
        }
        if let Some(Some(policy)) = &policy {
            policy.validate()?;
            if !config.members.contains_key(&policy.source_member) {
                return Err("original initialization source is not a configured member".into());
            }
        }
        Self::start_inner(
            config,
            path,
            limits,
            Some((root, owners)),
            true,
            observation_ms,
            policy,
            true,
        )
        .await
    }

    /// Retains old startup while admitting network content only through the explicit owned constructor.
    #[allow(clippy::too_many_arguments)]
    async fn start_inner(
        config: ReplicaConfig,
        path: &Path,
        limits: ReplicaTransportLimits,
        content: Option<(ReplicaContentStore, BTreeMap<u64, String>)>,
        controller_content: bool,
        quorum_observation_ms: Option<u64>,
        initialization: Option<Option<ContentInitializationPolicy>>,
        coordinated_archive: bool,
    ) -> Result<(Self, ReplicaServer), String> {
        config.validate()?;
        if config.streams_content() != content.is_some() {
            return Err(
                "network content startup requires its explicit original Root4 constructor".into(),
            );
        }
        let transport_budget = transport_budget::TransportBudget::new(limits)?;
        let (store, content_bootstrap) = if let Some((root, owners)) = content {
            if owners.keys().copied().collect::<BTreeSet<_>>()
                != config.members.keys().copied().collect()
                || owners.values().collect::<BTreeSet<_>>().len() != owners.len()
                || owners.get(&config.replica_id) != Some(&root.identity().store_owner)
                || root.identity().member != config.replica_id
            {
                return Err("network bootstrap lacks exact distinct actual member roots".into());
            }
            for (id, owner) in &owners {
                ReplicaMember::with_content_store(config.members[id], owner.clone())?;
            }
            let store = if coordinated_archive {
                SqliteRaftStore::open_with_coordinated_archive(
                    path,
                    &config.profile_identity(),
                    &config.application_schema,
                    root,
                    initialization,
                )
            } else if let Some(policy) = initialization {
                SqliteRaftStore::open_with_content_initialization(
                    path,
                    &config.profile_identity(),
                    &config.application_schema,
                    root,
                    policy,
                )
            } else if controller_content {
                SqliteRaftStore::open_with_controller_content(
                    path,
                    &config.profile_identity(),
                    &config.application_schema,
                    root,
                )
            } else {
                SqliteRaftStore::open_with_network_content(
                    path,
                    &config.profile_identity(),
                    &config.application_schema,
                    root,
                )
            }?;
            (store, Some(Arc::new(owners)))
        } else {
            (
                SqliteRaftStore::open(
                    path,
                    &config.profile_identity(),
                    &config.application_schema,
                )?,
                None,
            )
        };
        if config.streams_content() {
            if crate::content::hex_digest(&config.application_policy_digest) {
                store.bind_original_application_policy(&config.application_policy_digest)?;
            }
            let original = store.clone();
            tokio::task::spawn_blocking(move || original.reconcile_snapshot_receives())
                .await
                .map_err(|e| e.to_string())??;
            let original = store.clone();
            let unavailable =
                tokio::task::spawn_blocking(move || original.unavailable_replay_inputs())
                    .await
                    .map_err(|error| error.to_string())??;
            if let Some(first) = unavailable.first() {
                return Err(format!(
                    "committed replay content requires explicit repair: {} unavailable inputs; first Log {} entry {}: {}",
                    unavailable.len(),
                    first.log_index,
                    first.entry_digest,
                    first.content.content_digest,
                ));
            }
        }
        let tls = if config.rotates_certificates() {
            let configuration = config.tls.as_ref().ok_or("rotation requires TLS")?;
            store.initialize_certificate_trust(&configuration.peer_certificate_digests)?;
            Some(Arc::new(tls::with_store_trust(
                configuration,
                store.clone(),
                config.replica_id,
            )?))
        } else {
            if store.certificate_trust()?.is_some() {
                return Err("legacy replica cannot consume current certificate authority".into());
            }
            tls::load(config.tls.as_ref())?.map(Arc::new)
        };
        if let Some(tls) = &tls {
            tls.authenticate_local(config.replica_id)?;
        }
        let settings = Arc::new(
            openraft::Config {
                cluster_name: config.cluster_id.clone(),
                heartbeat_interval: 500,
                election_timeout_min: 1_500,
                election_timeout_max: 3_000,
                max_payload_entries: 1,
                snapshot_max_chunk_size: 1024 * 1024,
                install_snapshot_timeout: if config.streams_content()
                    && store.network_content_store()?.snapshot_receives_enabled()
                {
                    10_000
                } else {
                    200
                },
                snapshot_policy: openraft::SnapshotPolicy::LogsSinceLast(128),
                max_in_snapshot_log_to_keep: 64,
                ..Default::default()
            }
            .validate()
            .map_err(|error| error.to_string())?,
        );
        let network_online = Arc::new(std::sync::atomic::AtomicBool::new(true));
        let network_source = Arc::new(std::sync::OnceLock::new());
        let network = transport::ReplicaNetworkFactory::new(
            config.profile_identity(),
            config.replica_id,
            network_online.clone(),
            config.shared_secret.clone(),
            tls.clone(),
            transport_budget.clone(),
            network_source.clone(),
        );
        let raft = Raft::new(
            config.replica_id,
            settings,
            network,
            store.clone(),
            store.clone(),
        )
        .await
        .map_err(|error| error.to_string())?;
        let node = Self {
            network_source,
            content_bootstrap,
            transport_budget,
            process_instance: supervision::new_process_instance()?,
            #[cfg(test)]
            discard_transfer_reply: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            leader_transfer_writer: Arc::new(tokio::sync::Mutex::new(())),
            content_transition: Arc::new(tokio::sync::Semaphore::new(1)),
            #[cfg(test)]
            framed_member_mutations: Arc::new(std::sync::atomic::AtomicU64::new(0)),
            member_writer: Arc::new(tokio::sync::Mutex::new(())),
            network_online,
            config: Arc::new(config),
            upgrade_binary: Arc::new(std::sync::RwLock::new(BTreeMap::new())),
            raft,
            store,
            tls,
            quorum_observation_ms,
        };
        let server = ReplicaServer::bind(node.clone()).await?;
        if node.config.replica_id
            == *node
                .config
                .initial_voters()
                .iter()
                .next()
                .expect("at least one initial voter")
            && !node
                .raft
                .is_initialized()
                .await
                .map_err(|error| error.to_string())?
        {
            let members = node
                .config
                .initial_voters()
                .iter()
                .map(|id| node.configured_member(*id).map(|member| (*id, member)))
                .collect::<Result<BTreeMap<_, _>, _>>()?;
            node.raft
                .initialize(members)
                .await
                .map_err(|error| error.to_string())?;
        }
        Ok((node, server))
    }

    /// Supplies declared original member data for first bootstrap/new learner, never rewriting existing data.
    fn configured_member(&self, member: u64) -> Result<ReplicaMember, String> {
        let address = self
            .config
            .members
            .get(&member)
            .ok_or("member is not pre-enrolled")?;
        match &self.content_bootstrap {
            None => Ok(ReplicaMember::new(address)),
            Some(owners) => ReplicaMember::with_content_store(
                address,
                owners
                    .get(&member)
                    .ok_or("original member root declaration missing")?
                    .clone(),
            ),
        }
    }

    /// Observes actual local transport permits without changing epoch, ownership or permissions.
    pub fn transport_snapshot(&self) -> ReplicaTransportSnapshot {
        self.transport_budget.snapshot()
    }

    /// Observes one bounded actual Raft fatal error; this cannot grant readiness or recovery authority.
    pub fn infrastructure_error(&self) -> Option<String> {
        self.raft
            .metrics()
            .borrow()
            .running_state
            .as_ref()
            .err()
            .map(|error| error.to_string().chars().take(2048).collect())
    }

    /// Confirms infrastructure epoch/quorum and the original transfer fence; content child ingress is separate.
    pub async fn check_epoch(&self, epoch: ControllerEpoch) -> Result<(), String> {
        if self.store.application_upgrade_blocks()? {
            return Err("application authority is fenced by original upgrade intent".into());
        }
        self.check_infrastructure_epoch(epoch).await?;
        if self.store.application_upgrade_blocks()? {
            return Err("application upgrade was admitted during the original quorum check".into());
        }
        Ok(())
    }

    /// Confirms only original infrastructure epoch/quorum; this grants no fenced application effect.
    pub(crate) async fn check_infrastructure_epoch(
        &self,
        epoch: ControllerEpoch,
    ) -> Result<(), String> {
        self.check_source_transfer_fence()?;
        self.check_consensus_epoch(epoch).await?;
        self.check_source_transfer_fence()
    }

    /// Refuses source application authority as soon as its durable original handoff is pending.
    fn check_source_transfer_fence(&self) -> Result<(), String> {
        self.store.connection_source_fence(self.config.replica_id)
    }

    /// Confirms actual infrastructure quorum without granting application or source-child authority.
    async fn check_consensus_epoch(&self, epoch: ControllerEpoch) -> Result<(), String> {
        if let Some(tls) = &self.tls {
            tls.refreshed()?
                .authenticate_local(self.config.replica_id)?;
        }
        if self.current_epoch() != Some(epoch) {
            return Err("Controller epoch is no longer current".into());
        }
        if let Some(window) = self.quorum_observation_ms {
            let deadline = tokio::time::Instant::now() + Duration::from_millis(window);
            loop {
                if self.current_epoch() != Some(epoch) {
                    return Err("Controller epoch changed during quorum observation".into());
                }
                let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
                if remaining.is_zero() {
                    return Err("original owned quorum observation window expired".into());
                }
                match tokio::time::timeout(remaining, self.raft.ensure_linearizable()).await {
                    Ok(Ok(_)) => {
                        if self.current_epoch() == Some(epoch) {
                            return Ok(());
                        }
                        return Err("Controller epoch changed during actual quorum proof".into());
                    }
                    Err(_) => return Err("original owned quorum observation window expired".into()),
                    Ok(Err(_)) => {
                        if self.current_epoch() != Some(epoch) {
                            return Err("Controller epoch lost during quorum observation".into());
                        }
                        tokio::time::sleep(
                            Duration::from_millis(20).min(
                                deadline.saturating_duration_since(tokio::time::Instant::now()),
                            ),
                        )
                        .await;
                    }
                }
            }
        }
        tokio::time::timeout(Duration::from_secs(2), self.raft.ensure_linearizable())
            .await
            .map_err(|_| "Controller quorum confirmation timed out".to_string())?
            .map_err(|error| error.to_string())?;
        if self.current_epoch() != Some(epoch) {
            return Err("Controller epoch changed during quorum confirmation".into());
        }
        Ok(())
    }

    /// Reads local consensus role without interpreting embodied Node status or physical stop evidence.
    pub fn current_epoch(&self) -> Option<ControllerEpoch> {
        let metrics = self.raft.metrics().borrow().clone();
        (metrics.state == openraft::ServerState::Leader
            && metrics.current_leader == Some(self.config.replica_id))
        .then_some(ControllerEpoch {
            term: metrics.current_term,
            replica_id: self.config.replica_id,
        })
    }

    /// Reads actual role/quorum/position and, for the leader, the complete same-voter content majority.
    /// This never creates a producer, renews a budget, changes membership or retires a pin.
    pub async fn readiness(&self) -> ReplicaReadiness {
        let metrics = self.raft.metrics().borrow().clone();
        let role = format!("{:?}", metrics.state).to_ascii_lowercase();
        let voters = metrics
            .membership_config
            .voter_ids()
            .collect::<BTreeSet<_>>();
        let metadata_ready = metrics.running_state.is_ok()
            && metrics.current_leader.is_some()
            && metrics.millis_since_quorum_ack.is_some();
        let mut complete_content_members = BTreeSet::new();
        let mut content_ready = false;
        let mut reason = if metadata_ready {
            None
        } else {
            Some("actual metadata quorum/leader observation is unavailable".to_string())
        };
        if self.config.streams_content() && metrics.state == openraft::ServerState::Leader {
            match self.accepted_content_target().await {
                Ok(target) => match self.content_quorum(target).await {
                    Ok(members) => {
                        complete_content_members = members;
                        content_ready = true;
                    }
                    Err(error) => reason = Some(error.chars().take(512).collect()),
                },
                Err(error) => reason = Some(error.chars().take(512).collect()),
            }
        } else if self.config.streams_content() {
            reason = Some("content readiness requires the actual current leader".into());
        }
        ReplicaReadiness {
            schema: "roboguide.replica-readiness/v0.1".into(),
            member: self.config.replica_id,
            role,
            term: metrics.current_term,
            leader: metrics.current_leader,
            last_log_index: metrics.last_log_index,
            last_applied_index: metrics.last_applied.map(|id| id.index),
            joint_groups: metrics
                .membership_config
                .membership()
                .get_joint_config()
                .len(),
            voters,
            metadata_ready,
            content_ready,
            complete_content_members,
            reason,
        }
    }

    /// Applies one exact application candidate through majority durability before returning acceptance.
    pub async fn commit(
        &self,
        epoch: ControllerEpoch,
        batch: state::ApplicationCommitBatch,
    ) -> Result<CommitReceipt, String> {
        if self.config.streams_content() {
            return Err(
                "network content profile requires its complete original content candidate".into(),
            );
        }
        self.check_epoch(epoch).await?;
        batch.validate().map_err(|error| error.to_string())?;
        let reply =
            tokio::time::timeout(Duration::from_secs(5), self.raft.client_write(batch.into()))
                .await
                .map_err(|_| "Controller quorum acceptance timed out".to_string())?
                .map_err(|error| error.to_string())?;
        self.check_epoch(epoch).await?;
        if !reply.data.accepted {
            return Err(reply
                .data
                .rejection
                .unwrap_or_else(|| "replica application rejected the base identity".into()));
        }
        Ok(reply.data)
    }

    /// Adds one pre-enrolled infrastructure member as a learner after current quorum confirmation.
    pub async fn add_learner(&self, member_id: u64) -> Result<(), String> {
        let epoch = self
            .current_epoch()
            .ok_or_else(|| "learner admission requires the current leader".to_string())?;
        self.add_learner_at_epoch(epoch, member_id, Arc::new(|| Ok(())))
            .await
    }

    /// Retains the actual original caller epoch before learner effects, including queued administration.
    pub(crate) async fn add_learner_at_epoch(
        &self,
        epoch: ControllerEpoch,
        member_id: u64,
        permission: crate::content_store::StreamPermission,
    ) -> Result<(), String> {
        permission()?;
        self.check_epoch(epoch).await?;
        if member_id == self.config.replica_id
            || !self.config.members.contains_key(&member_id)
            || self
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .any(|id| id == member_id)
        {
            return Err("learner admission target is not a pre-enrolled non-voter".into());
        }
        let node = self.configured_member(member_id)?;
        if self.config.streams_content()
            && self
                .raft
                .metrics()
                .borrow()
                .membership_config
                .nodes()
                .any(|(id, original)| *id == member_id && original != &node)
        {
            return Err(
                "existing member binding requires explicit original reconfiguration".into(),
            );
        }
        permission()?;
        tokio::time::timeout(
            Duration::from_secs(10),
            self.raft.add_learner(member_id, node, true),
        )
        .await
        .map_err(|_| "learner catch-up timed out".to_string())?
        .map_err(|error| error.to_string())?;
        permission()?;
        self.check_epoch(epoch).await
    }

    /// Changes voters through OpenRaft joint consensus after every new voter is caught up.
    pub async fn change_membership(
        &self,
        voters: BTreeSet<u64>,
        retain_removed_as_learners: bool,
    ) -> Result<(), String> {
        let epoch = self
            .current_epoch()
            .ok_or_else(|| "membership change requires the current leader".to_string())?;
        self.change_membership_at_epoch(
            epoch,
            voters,
            retain_removed_as_learners,
            Arc::new(|| Ok(())),
        )
        .await
    }

    /// Carries one original caller epoch through actual admission without adopting a newer queued term.
    pub(crate) async fn change_membership_at_epoch(
        &self,
        epoch: ControllerEpoch,
        voters: BTreeSet<u64>,
        retain_removed_as_learners: bool,
        permission: crate::content_store::StreamPermission,
    ) -> Result<(), String> {
        if voters.is_empty()
            || voters.len() > self.config.members.len()
            || voters
                .iter()
                .any(|member_id| !self.config.members.contains_key(member_id))
        {
            return Err("membership change contains an unknown or empty voter set".into());
        }
        if self.config.streams_content() {
            return self
                .change_content_membership(epoch, voters, retain_removed_as_learners, permission)
                .await;
        }
        permission()?;
        self.check_epoch(epoch).await?;
        let current_voters = self
            .raft
            .metrics()
            .borrow()
            .membership_config
            .voter_ids()
            .collect::<BTreeSet<_>>();
        for member_id in voters.iter().filter(|id| !current_voters.contains(id)) {
            self.add_learner_at_epoch(epoch, *member_id, permission.clone())
                .await?;
        }
        permission()?;
        tokio::time::timeout(
            Duration::from_secs(10),
            self.raft
                .change_membership(voters, retain_removed_as_learners),
        )
        .await
        .map_err(|_| "membership joint consensus timed out".to_string())?
        .map_err(|error| error.to_string())?;
        permission()?;
        self.check_epoch(epoch).await
    }

    /// Requests a target voter to trigger a controlled election under the current source epoch.
    pub async fn request_leader_transfer(
        &self,
        member_id: u64,
        transfer_id: impl Into<String>,
    ) -> Result<LeaderTransferReceipt, String> {
        let _transfer_writer = self.leader_transfer_writer.lock().await;
        let transfer_id = transfer_id.into();
        if transfer_id.trim().is_empty() || transfer_id.len() > 128 {
            return Err("leader transfer identity is empty or unbounded".into());
        }
        if let Some(original) = self.store.leader_transfer_by_id(&transfer_id)? {
            if original.target != member_id
                || original.source_epoch.replica_id != self.config.replica_id
            {
                return Err("original leader transfer owner/target differs".into());
            }
            return Ok(LeaderTransferReceipt {
                transfer_id: original.transfer_id,
                source_epoch: original.source_epoch,
                target: original.target,
                state: original.state,
            });
        }
        self.reconcile_leader_transfer().await?;
        let epoch = self
            .current_epoch()
            .ok_or("leader transfer requires the current leader")?;
        self.check_epoch(epoch).await?;
        if member_id == self.config.replica_id
            || !self.config.members.contains_key(&member_id)
            || !self
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .any(|id| id == member_id)
        {
            return Err("leader transfer target is not a current voter".into());
        }
        let metrics = self.raft.metrics().borrow().clone();
        let required_last_log_index = metrics.last_log_index;
        if !metrics
            .replication
            .as_ref()
            .and_then(|replication| replication.get(&member_id))
            .and_then(|log| log.as_ref())
            .map(|log| Some(log.index) >= required_last_log_index)
            .unwrap_or(required_last_log_index.is_none())
        {
            return Err("leader transfer target has not caught up to the current log".into());
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(8);
        let mut prepared = LeaderTransferRecord {
            transfer_id: transfer_id.clone(),
            source_epoch: epoch,
            target: member_id,
            required_last_log_index,
            state: "prepared".into(),
            confirmed_epoch: None,
            source_process: self.store.transfer_source_process(epoch)?,
            source_drain: None,
        };
        self.store.persist_leader_transfer(&prepared)?;
        loop {
            if let Some(proof) = self.store.controller_drain_proof(
                epoch,
                prepared.source_process.as_ref(),
                &self.process_instance,
            )? {
                proof.validate(epoch)?;
                prepared.source_drain = Some(proof);
                self.store.persist_leader_transfer(&prepared)?;
                break;
            }
            if tokio::time::Instant::now() >= deadline {
                self.store.persist_leader_transfer(&LeaderTransferRecord {
                    state: "unknown".into(),
                    ..prepared
                })?;
                return Err(
                    "original Controller child was not reaped before transfer deadline".into(),
                );
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        if let Err(error) = self.check_consensus_epoch(epoch).await {
            self.store.persist_leader_transfer(&LeaderTransferRecord {
                state: "unknown".into(),
                ..prepared
            })?;
            return Err(error);
        }
        let remaining_ms = u64::try_from(
            deadline
                .saturating_duration_since(tokio::time::Instant::now())
                .as_millis(),
        )
        .unwrap_or(0);
        if remaining_ms == 0 {
            self.store.persist_leader_transfer(&LeaderTransferRecord {
                state: "unknown".into(),
                ..prepared
            })?;
            return Err("original source drain consumed the transfer effect budget".into());
        }
        self.raft.runtime_config().heartbeat(false);
        self.raft.runtime_config().elect(false);
        let _resume_ticker = LeaderTransferTickerGuard { raft: &self.raft };
        let result = tokio::time::timeout_at(
            deadline,
            transport::request_leader_transfer(
                self,
                member_id,
                transfer_id,
                epoch,
                required_last_log_index,
                prepared
                    .source_drain
                    .clone()
                    .ok_or("original source drain proof is absent")?,
                remaining_ms,
            ),
        )
        .await;
        let receipt = match result {
            Ok(Ok(receipt)) => {
                if receipt.transfer_id != prepared.transfer_id
                    || receipt.source_epoch != epoch
                    || receipt.target != member_id
                    || receipt.state != "requested"
                {
                    self.store.persist_leader_transfer(&LeaderTransferRecord {
                        state: "unknown".into(),
                        ..prepared
                    })?;
                    return Err("leader transfer original target receipt differs".into());
                }
                self.store.persist_leader_transfer(&LeaderTransferRecord {
                    state: "accepted".into(),
                    ..prepared.clone()
                })?;
                receipt
            }
            result => {
                self.store.persist_leader_transfer(&LeaderTransferRecord {
                    state: "unknown".into(),
                    ..prepared
                })?;
                return Err(match result {
                    Ok(Err(error)) => error,
                    _ => {
                        "leader transfer original effect deadline expired; outcome remains unknown"
                            .into()
                    }
                });
            }
        };
        loop {
            if let Ok(Ok(Some(target_epoch))) =
                tokio::time::timeout_at(deadline, transport::member_epoch(self, member_id)).await
                && target_epoch.replica_id == member_id
                && target_epoch.term > epoch.term
            {
                self.store.persist_leader_transfer(&LeaderTransferRecord {
                    state: "target-elected".into(),
                    ..prepared.clone()
                })?;
                return Ok(receipt);
            }
            if tokio::time::Instant::now() >= deadline {
                self.store.persist_leader_transfer(&LeaderTransferRecord {
                    state: "unknown".into(),
                    ..prepared
                })?;
                return Err(
                    "leader transfer target was not confirmed before original deadline".into(),
                );
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    }

    /// Reads the durable handoff record for operator reconciliation and restart fencing.
    pub fn leader_transfer_record(&self) -> Result<Option<LeaderTransferRecord>, String> {
        self.store.leader_transfer_record()
    }

    /// Observes an original completion without triggering election or renewing effect permission.
    pub async fn reconcile_leader_transfer(&self) -> Result<bool, String> {
        let Some(record) = self.store.leader_transfer_record_on_worker().await? else {
            return Ok(true);
        };
        if matches!(record.state.as_str(), "completed" | "rejected") {
            return Ok(true);
        }
        if record.source_drain.is_none() {
            return Ok(false);
        }
        if record.target == self.config.replica_id {
            let Some(epoch) = self.current_epoch() else {
                return Ok(false);
            };
            if epoch.term <= record.source_epoch.term {
                return Ok(false);
            }
            if self.check_consensus_epoch(epoch).await.is_err() {
                return Ok(false);
            }
            self.store
                .persist_leader_transfer_on_worker(LeaderTransferRecord {
                    state: "completed".into(),
                    confirmed_epoch: Some(epoch),
                    ..record
                })
                .await?;
            return Ok(true);
        }
        if record.source_epoch.replica_id != self.config.replica_id {
            return Ok(false);
        }
        let incoming = match transport::observe_leader_transfer(
            self,
            record.target,
            &record.transfer_id,
        )
        .await
        {
            Ok(Some(incoming)) => incoming,
            _ => return Ok(false),
        };
        if incoming.transfer_id != record.transfer_id
            || incoming.source_epoch != record.source_epoch
            || incoming.target != record.target
            || incoming.required_last_log_index != record.required_last_log_index
            || incoming.state != "completed"
            || !incoming.confirmed_epoch.is_some_and(|epoch| {
                epoch.replica_id == record.target && epoch.term > record.source_epoch.term
            })
        {
            return Ok(false);
        }
        self.store
            .persist_leader_transfer_on_worker(incoming)
            .await?;
        Ok(true)
    }

    /// Holds owned application processes until an original operation has independent completion.
    pub fn leader_transfer_blocks_controller(&self) -> Result<bool, String> {
        Ok(self.store.application_upgrade_blocks()?
            || self
                .store
                .leader_transfer_record()?
                .is_some_and(|record| !matches!(record.state.as_str(), "completed" | "rejected")))
    }

    /// Returns whether an envelope sender is a current member or a configured bootstrap voter.
    pub(crate) fn member_allowed(&self, member_id: u64) -> bool {
        let metrics = self.raft.metrics().borrow().clone();
        if metrics
            .membership_config
            .nodes()
            .any(|(id, _)| *id == member_id)
        {
            return true;
        }
        metrics
            .membership_config
            .membership()
            .get_joint_config()
            .is_empty()
            && self.initial_voters().contains(&member_id)
    }

    /// Returns the initial voter identities for bootstrap authorization.
    fn initial_voters(&self) -> BTreeSet<u64> {
        self.config.initial_voters()
    }

    /// Validates and triggers a target-side election without claiming that it already won.
    pub(crate) async fn accept_leader_transfer(
        &self,
        source_epoch: ControllerEpoch,
        target: u64,
        transfer_id: String,
        required_last_log_index: Option<u64>,
        source_drain: ControllerDrainProof,
        received_deadline: tokio::time::Instant,
    ) -> Result<LeaderTransferReceipt, String> {
        source_drain.validate(source_epoch)?;
        if tokio::time::Instant::now() >= received_deadline {
            return Err("original target receive budget expired before admission".into());
        }
        if let Some(original) = self.store.leader_transfer_by_id(&transfer_id)? {
            if original.source_epoch != source_epoch
                || original.target != target
                || original.required_last_log_index != required_last_log_index
                || original.source_drain.as_ref() != Some(&source_drain)
            {
                return Err("original target transfer identity differs".into());
            }
            return if matches!(
                original.state.as_str(),
                "accepted" | "completed" | "target-elected"
            ) {
                Ok(LeaderTransferReceipt {
                    transfer_id,
                    source_epoch,
                    target,
                    state: "requested".into(),
                })
            } else {
                Err("original target transfer remains uncertain; election cannot repeat".into())
            };
        }
        self.reconcile_leader_transfer().await?;
        let metrics = self.raft.metrics().borrow().clone();
        if target != self.config.replica_id
            || source_epoch.replica_id == self.config.replica_id
            || source_epoch.term != metrics.current_term
            || metrics.current_leader != Some(source_epoch.replica_id)
            || !metrics
                .membership_config
                .voter_ids()
                .any(|id| id == self.config.replica_id)
            || metrics.last_log_index < required_last_log_index
        {
            return Err("leader transfer source epoch, target membership or log is stale".into());
        }
        self.store.persist_leader_transfer(&LeaderTransferRecord {
            transfer_id: transfer_id.clone(),
            source_epoch,
            target,
            required_last_log_index,
            state: "accepted".into(),
            confirmed_epoch: None,
            source_process: match &source_drain {
                ControllerDrainProof::ProcessExit { outcome } => Some(outcome.process.clone()),
                _ => None,
            },
            source_drain: Some(source_drain),
        })?;
        if tokio::time::Instant::now() >= received_deadline {
            return Err("original target receive budget expired before election".into());
        }
        self.raft
            .trigger()
            .elect()
            .await
            .map_err(|error| error.to_string())?;
        Ok(LeaderTransferReceipt {
            transfer_id,
            source_epoch,
            target,
            state: "requested".into(),
        })
    }

    /// Returns the complete quorum-applied view for inactive child materialization only.
    pub fn accepted_view(&self) -> Result<ReplicaView, String> {
        self.store.accepted_view()
    }

    /// Returns the applied ordinal for local diagnostics without copying authority/history snapshots.
    pub fn accepted_revision(&self) -> Result<u64, String> {
        self.store.accepted_revision()
    }

    /// Returns the small accepted application identity used to resolve an ambiguous Commit reply.
    pub fn accepted_application_identity(&self) -> Result<AcceptedApplicationIdentity, String> {
        self.store.accepted_application_identity()
    }

    /// Returns the bound infrastructure RPC endpoint, never an embodied executor address.
    pub fn local_address(&self) -> SocketAddr {
        self.config.members[&self.config.replica_id]
    }

    /// Returns startup-frozen cluster/profile identity for a child acceptance/delivery fence.
    pub fn profile_identity(&self) -> String {
        self.config.profile_identity()
    }

    /// Stops only the owned consensus engine; a caller separately fences its Controller child.
    pub async fn shutdown(&self) -> Result<(), String> {
        self.raft
            .shutdown()
            .await
            .map_err(|error| error.to_string())
    }
}

/// Temporary process-local suppression must never survive success, failure or future cancellation.
struct LeaderTransferTickerGuard<'a> {
    /// Original source engine whose automatic timers resume when this exact request stops waiting.
    raft: &'a ControllerRaft,
}

impl Drop for LeaderTransferTickerGuard<'_> {
    /// Restores ordinary Raft timers even after this member has become a follower in a newer term.
    fn drop(&mut self) {
        self.raft.runtime_config().heartbeat(true);
        self.raft.runtime_config().elect(true);
    }
}
