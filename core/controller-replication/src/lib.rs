#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Quorum durability for one Controller authority; replicas never match, bind or execute work.

mod store;
#[cfg(test)]
mod tests;
mod transport;

use openraft::{BasicNode, Raft};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
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

/// Startup-frozen, bounded three-member trusted loopback profile.
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
    /// Exact application checkpoint schema admitted by this cluster.
    pub application_schema: String,
    /// Startup-owned application policy identity, identical on all members.
    pub application_policy_digest: String,
    /// Optional shared transport secret for non-loopback-capable deployments.
    #[serde(default)]
    pub shared_secret: Option<String>,
}

impl ReplicaConfig {
    /// Rejects unsupported membership/network scope before creating any consensus authority.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.controller-replica/v0.1"
            || self.cluster_id.trim().is_empty()
            || self.cluster_id.len() > 128
            || self.members.len() != 3
            || !self.members.contains_key(&self.replica_id)
            || self.members.contains_key(&0)
            || self
                .members
                .values()
                .any(|address| !address.ip().is_loopback() || address.port() == 0)
            || self.application_schema.trim().is_empty()
            || self.application_policy_digest.trim().is_empty()
            || self
                .shared_secret
                .as_ref()
                .is_some_and(|secret| secret.len() < 32 || secret.len() > 256)
        {
            return Err("unsupported or incomplete controller replica profile".into());
        }
        let unique = self
            .members
            .values()
            .collect::<std::collections::BTreeSet<_>>();
        if unique.len() != 3 {
            return Err("controller replica endpoints must be distinct".into());
        }
        Ok(())
    }

    /// Hashes immutable membership, application schema and policy without this process's local identity.
    pub fn profile_identity(&self) -> String {
        use sha2::{Digest as _, Sha256};
        let bytes = serde_json::to_vec(&serde_json::json!({
            "schema":self.schema,"cluster_id":self.cluster_id,"members":self.members,
            "application_schema":self.application_schema,"application_policy_digest":self.application_policy_digest,
            "transport_auth":self.shared_secret.as_ref().map(|secret| {
                format!("{:x}", sha2::Sha256::digest(secret.as_bytes()))
            }),
        })).expect("validated scalar replica configuration encodes");
        format!("{:x}", Sha256::digest(bytes))
    }
}

/// Live consensus service and durable accepted history, independent of any executing Controller child.
#[derive(Clone)]
pub struct ReplicaNode {
    /// Private network fault switch for deterministic partition verification only.
    #[cfg(test)]
    network_online: Arc<std::sync::atomic::AtomicBool>,
    /// Infrastructure identity and exact cluster profile.
    config: Arc<ReplicaConfig>,
    /// Mature consensus protocol implementation.
    raft: ControllerRaft,
    /// Durable accepted history and consensus storage.
    store: SqliteRaftStore,
}

impl ReplicaNode {
    /// Opens durable storage, binds private RPC and initializes only the lowest member's empty log.
    pub async fn start(
        config: ReplicaConfig,
        path: &Path,
    ) -> Result<(Self, ReplicaServer), String> {
        config.validate()?;
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
            config: Arc::new(config),
            raft,
            store,
            #[cfg(test)]
            network_online,
        };
        let server = ReplicaServer::bind(node.clone()).await?;
        if node.config.replica_id == *node.config.members.keys().next().expect("three members")
            && !node
                .raft
                .is_initialized()
                .await
                .map_err(|error| error.to_string())?
        {
            let members = node
                .config
                .members
                .iter()
                .map(|(id, address)| (*id, BasicNode::new(address.to_string())))
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
