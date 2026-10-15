#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Quorum durability for one Controller authority; replicas never match, bind or execute work.

mod store;
#[cfg(test)]
mod tests;
mod tls;
mod transport;

use openraft::{BasicNode, Raft};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Cursor;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

pub use store::{AcceptedApplicationIdentity, HistoryAnchor, ReplicaView, SqliteRaftStore};
pub use transport::{ReplicaFenceClient, ReplicaServer};

openraft::declare_raft_types!(
    /// Consensus types use infrastructure replica identities independently of embodied Node identities.
    pub ControllerRaftConfig:
        D = state::ApplicationCommitBatch,
        R = CommitReceipt,
        NodeId = u64,
        Node = BasicNode,
        SnapshotData = Cursor<Vec<u8>>
);

/// Existing OpenRaft election, log ordering, quorum and snapshot engine.
pub type ControllerRaft = Raft<ControllerRaftConfig>;

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
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CommitReceipt {
    /// Exact target passed every base/tail/codec requirement and is durable.
    pub accepted: bool,
    /// Latest accepted application revision.
    pub revision: u64,
    /// Exact accepted complete checkpoint digest.
    pub checkpoint_digest: Option<String>,
    /// Bounded rejection description, never interpreted as lifecycle policy.
    pub rejection: Option<String>,
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

/// Authenticated member-side administration client; it opens no consensus storage or child.
pub struct ReplicaAdminClient {
    /// Complete startup-frozen cluster profile and member credential material.
    config: ReplicaConfig,
    /// Current leader endpoint selected by an operator or deployment supervisor.
    leader: u64,
}

impl ReplicaAdminClient {
    /// Validates a pre-enrolled member profile before constructing a private admin client.
    pub fn new(config: ReplicaConfig, leader: u64) -> Result<Self, String> {
        config.validate()?;
        if !config.members.contains_key(&leader) {
            return Err("administration leader is not pre-enrolled".into());
        }
        Ok(Self { config, leader })
    }

    /// Reads the current leader epoch, rejecting a follower or unavailable endpoint.
    pub async fn epoch(&self) -> Result<ControllerEpoch, String> {
        transport::admin_epoch(&self.config, self.leader)
            .await?
            .ok_or_else(|| "administration endpoint is not the current leader".into())
    }

    /// Reads the current committed voter set for an operator audit.
    pub async fn membership(&self) -> Result<BTreeSet<u64>, String> {
        transport::admin_membership(&self.config, self.leader).await
    }

    /// Adds one configured member as a learner and waits for its log catch-up.
    pub async fn add_learner(&self, member_id: u64) -> Result<(), String> {
        let epoch = self.epoch().await?;
        transport::admin_add_learner(&self.config, self.leader, epoch, member_id).await
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
            epoch,
            target,
            transfer_id.into(),
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
            || self.schema == "roboguide.controller-replica/v0.2")
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
            || (self.schema == "roboguide.controller-replica/v0.2" && self.initial_voters.is_none())
            || (self
                .members
                .values()
                .any(|address| !address.ip().is_loopback())
                && self.tls.is_none())
            || self.application_schema.trim().is_empty()
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
}

impl ReplicaNode {
    /// Opens durable storage, binds private RPC and initializes only the lowest member's empty log.
    pub async fn start(
        config: ReplicaConfig,
        path: &Path,
    ) -> Result<(Self, ReplicaServer), String> {
        config.validate()?;
        let tls = tls::load(config.tls.as_ref())?.map(Arc::new);
        if let Some(tls) = &tls {
            tls.authenticate_local(config.replica_id)?;
        }
        let store =
            SqliteRaftStore::open(path, &config.profile_identity(), &config.application_schema)?;
        let settings = Arc::new(
            openraft::Config {
                cluster_name: config.cluster_id.clone(),
                heartbeat_interval: 500,
                election_timeout_min: 1_500,
                election_timeout_max: 3_000,
                max_payload_entries: 1,
                snapshot_max_chunk_size: 1024 * 1024,
                snapshot_policy: openraft::SnapshotPolicy::LogsSinceLast(128),
                max_in_snapshot_log_to_keep: 64,
                ..Default::default()
            }
            .validate()
            .map_err(|error| error.to_string())?,
        );
        let network_online = Arc::new(std::sync::atomic::AtomicBool::new(true));
        let network = transport::ReplicaNetworkFactory::new(
            config.profile_identity(),
            config.replica_id,
            network_online.clone(),
            config.shared_secret.clone(),
            tls.clone(),
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
            network_online,
            config: Arc::new(config),
            raft,
            store,
            tls,
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
                .map(|id| (*id, BasicNode::new(node.config.members[id].to_string())))
                .collect::<BTreeMap<_, _>>();
            node.raft
                .initialize(members)
                .await
                .map_err(|error| error.to_string())?;
        }
        Ok((node, server))
    }

    /// Requires current leadership and majority confirmation under the exact original epoch.
    pub async fn check_epoch(&self, epoch: ControllerEpoch) -> Result<(), String> {
        if self.current_epoch() != Some(epoch) {
            return Err("Controller epoch is no longer current".into());
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

    /// Applies one exact application candidate through majority durability before returning acceptance.
    pub async fn commit(
        &self,
        epoch: ControllerEpoch,
        batch: state::ApplicationCommitBatch,
    ) -> Result<CommitReceipt, String> {
        self.check_epoch(epoch).await?;
        batch.validate().map_err(|error| error.to_string())?;
        let reply = tokio::time::timeout(Duration::from_secs(5), self.raft.client_write(batch))
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
        let node = BasicNode::new(self.config.members[&member_id].to_string());
        tokio::time::timeout(
            Duration::from_secs(10),
            self.raft.add_learner(member_id, node, true),
        )
        .await
        .map_err(|_| "learner catch-up timed out".to_string())?
        .map_err(|error| error.to_string())?;
        self.check_epoch(epoch).await
    }

    /// Changes voters through OpenRaft joint consensus after every new voter is caught up.
    pub async fn change_membership(
        &self,
        voters: BTreeSet<u64>,
        retain_removed_as_learners: bool,
    ) -> Result<(), String> {
        if voters.is_empty()
            || voters.len() > self.config.members.len()
            || voters
                .iter()
                .any(|member_id| !self.config.members.contains_key(member_id))
        {
            return Err("membership change contains an unknown or empty voter set".into());
        }
        let epoch = self
            .current_epoch()
            .ok_or_else(|| "membership change requires the current leader".to_string())?;
        self.check_epoch(epoch).await?;
        let current_voters = self
            .raft
            .metrics()
            .borrow()
            .membership_config
            .voter_ids()
            .collect::<BTreeSet<_>>();
        for member_id in voters.iter().filter(|id| !current_voters.contains(id)) {
            self.add_learner(*member_id).await?;
        }
        tokio::time::timeout(
            Duration::from_secs(10),
            self.raft
                .change_membership(voters, retain_removed_as_learners),
        )
        .await
        .map_err(|_| "membership joint consensus timed out".to_string())?
        .map_err(|error| error.to_string())?;
        self.check_epoch(epoch).await
    }

    /// Requests a target voter to trigger a controlled election under the current source epoch.
    pub async fn request_leader_transfer(
        &self,
        member_id: u64,
        transfer_id: impl Into<String>,
    ) -> Result<LeaderTransferReceipt, String> {
        let transfer_id = transfer_id.into();
        if transfer_id.trim().is_empty() || transfer_id.len() > 128 {
            return Err("leader transfer identity is empty or unbounded".into());
        }
        let epoch = self
            .current_epoch()
            .ok_or_else(|| "leader transfer requires the current leader".to_string())?;
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
        self.raft.runtime_config().heartbeat(false);
        self.raft.runtime_config().elect(false);
        let receipt = transport::request_leader_transfer(
            self,
            member_id,
            transfer_id,
            epoch,
            required_last_log_index,
        )
        .await;
        let receipt = match receipt {
            Ok(receipt) => receipt,
            Err(error) => {
                self.raft.runtime_config().heartbeat(true);
                self.raft.runtime_config().elect(true);
                return Err(error);
            }
        };
        let deadline = tokio::time::Instant::now() + Duration::from_secs(8);
        loop {
            if let Ok(Some(target_epoch)) = transport::member_epoch(self, member_id).await
                && target_epoch.replica_id == member_id
                && target_epoch.term > epoch.term
            {
                return Ok(receipt);
            }
            if tokio::time::Instant::now() >= deadline {
                if self.current_epoch() == Some(epoch) {
                    self.raft.runtime_config().heartbeat(true);
                    self.raft.runtime_config().elect(true);
                }
                return Err(
                    "leader transfer target did not become leader before its deadline".into(),
                );
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
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
    ) -> Result<LeaderTransferReceipt, String> {
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
