//! Bounded trusted-loopback replica RPC and synchronous child acceptance/delivery fencing.

use crate::*;
use openraft::error::{InstallSnapshotError, RPCError, RaftError, RemoteError, Unreachable};
use openraft::network::{RPCOption, RaftNetwork, RaftNetworkFactory};
use openraft::raft::{
    AppendEntriesRequest, AppendEntriesResponse, InstallSnapshotRequest, InstallSnapshotResponse,
    VoteRequest, VoteResponse,
};
use sha2::{Digest as _, Sha256};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Maximum private frame, including escaped authority/history JSON.
const MAX_FRAME_BYTES: usize = 256 * 1024 * 1024;

/// Closed RPC envelope binds the actual target process and homogeneous cluster profile.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    /// Exact immutable cluster/application identity.
    profile: String,
    /// Infrastructure process which must receive this request.
    target: u64,
    /// Valid infrastructure sender or its owned Controller child.
    sender: u64,
    /// Optional digest authenticating the exact profile/target/sender/command envelope.
    #[serde(default)]
    auth_digest: Option<String>,
    /// Upstream consensus or local child-fencing request.
    command: Command,
}

/// Private consensus and durability operations never carry an embodied Node selector.
#[derive(Serialize, Deserialize)]
enum Command {
    /// Upstream log replication.
    Append(AppendEntriesRequest<ControllerRaftConfig>),
    /// Upstream election.
    Vote(VoteRequest<u64>),
    /// Upstream bounded snapshot chunk.
    Snapshot(InstallSnapshotRequest<ControllerRaftConfig>),
    /// Original child epoch requests current quorum confirmation.
    Fence(ControllerEpoch),
    /// Exact private application transaction requests quorum durability.
    Commit {
        /// Original issuing child epoch.
        epoch: ControllerEpoch,
        /// Complete candidate with immutable event/source evidence.
        batch: state::ApplicationCommitBatch,
    },
    /// Read the accepted application identity after an ambiguous Commit response.
    AcceptedIdentity {
        /// Original issuing epoch whose quorum must still be current.
        epoch: ControllerEpoch,
    },
}

/// Closed response preserves upstream Raft errors separately from child acceptance.
#[derive(Serialize, Deserialize)]
enum Reply {
    /// Upstream append outcome.
    Append(Result<AppendEntriesResponse<u64>, RaftError<u64>>),
    /// Upstream vote outcome.
    Vote(Result<VoteResponse<u64>, RaftError<u64>>),
    /// Upstream snapshot outcome.
    Snapshot(Result<InstallSnapshotResponse<u64>, RaftError<u64, InstallSnapshotError>>),
    /// Current epoch/quorum fence outcome.
    Fence(Result<(), String>),
    /// Exact quorum-applied application result.
    Commit(Result<CommitReceipt, String>),
    /// Small accepted application identity, never a live Control projection.
    AcceptedIdentity(Result<AcceptedApplicationIdentity, String>),
    /// Identity/framing/profile rejected before entering the consensus engine.
    Rejected(String),
}

/// Scoped private listener, stopped without touching any Local EAIOS or external process.
pub struct ReplicaServer {
    /// Owned accept-loop task.
    task: tokio::task::JoinHandle<()>,
}

impl Drop for ReplicaServer {
    /// Cancels only this service's listener on shutdown or a failed setup.
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl ReplicaServer {
    /// Binds the validated loopback member endpoint and admits only exact profile/target envelopes.
    pub(crate) async fn bind(node: ReplicaNode) -> Result<Self, String> {
        let listener = tokio::net::TcpListener::bind(node.local_address())
            .await
            .map_err(|error| error.to_string())?;
        let task = tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    return;
                };
                let node = node.clone();
                tokio::spawn(async move {
                    let _ = tokio::time::timeout(
                        Duration::from_secs(15),
                        serve_connection(stream, node),
                    )
                    .await;
                });
            }
        });
        Ok(Self { task })
    }
}

/// Dispatches one bounded request; the existing OpenRaft engine alone interprets consensus messages.
async fn serve_connection(
    mut stream: tokio::net::TcpStream,
    node: ReplicaNode,
) -> Result<(), String> {
    let length = stream.read_u64().await.map_err(|error| error.to_string())?;
    if length == 0 || length > MAX_FRAME_BYTES as u64 {
        return Err("replica request frame exceeds bounds".into());
    }
    let mut bytes = vec![0; length as usize];
    stream
        .read_exact(&mut bytes)
        .await
        .map_err(|error| error.to_string())?;
    let request: Request = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    #[cfg(test)]
    if !node.network_online.load(Ordering::SeqCst) {
        return Err("test replica is partitioned".into());
    }
    let authenticated = request_auth_digest(
        node.config.shared_secret.as_deref(),
        &request.profile,
        request.target,
        request.sender,
        &request.command,
    );
    let reply = if request.profile != node.profile_identity()
        || request.target != node.config.replica_id
        || !node.config.members.contains_key(&request.sender)
        || authenticated != request.auth_digest
    {
        Reply::Rejected("replica profile/member identity mismatch".into())
    } else {
        match request.command {
            Command::Append(rpc) => Reply::Append(node.raft.append_entries(rpc).await),
            Command::Vote(rpc) => Reply::Vote(node.raft.vote(rpc).await),
            Command::Snapshot(rpc) => {
                if rpc
                    .offset
                    .checked_add(rpc.data.len() as u64)
                    .is_none_or(|size| size > MAX_FRAME_BYTES as u64)
                {
                    Reply::Rejected("replica snapshot exceeds bounds".into())
                } else {
                    Reply::Snapshot(node.raft.install_snapshot(rpc).await)
                }
            }
            Command::Fence(epoch) => Reply::Fence(node.check_epoch(epoch).await),
            Command::Commit { epoch, batch } => Reply::Commit(node.commit(epoch, batch).await),
            Command::AcceptedIdentity { epoch } => {
                let result = node
                    .check_epoch(epoch)
                    .await
                    .and_then(|()| node.accepted_application_identity());
                Reply::AcceptedIdentity(result)
            }
        }
    };
    let encoded = serde_json::to_vec(&reply).map_err(|error| error.to_string())?;
    if encoded.len() > MAX_FRAME_BYTES {
        return Err("replica response exceeds bounds".into());
    }
    stream
        .write_u64(encoded.len() as u64)
        .await
        .map_err(|error| error.to_string())?;
    stream
        .write_all(&encoded)
        .await
        .map_err(|error| error.to_string())?;
    Ok(())
}

/// Factory establishes no connection until the upstream engine invokes an actual RPC.
pub(crate) struct ReplicaNetworkFactory {
    /// Startup cluster/profile identity.
    profile: String,
    /// Sending infrastructure replica.
    sender: u64,
    /// Test-controlled whole-network partition; production remains enabled.
    online: Arc<AtomicBool>,
    /// Optional shared secret copied only into the authenticated transport client.
    shared_secret: Option<String>,
}

impl ReplicaNetworkFactory {
    /// Freezes profile and sender; a shared switch permits deterministic partition checks.
    pub(crate) fn new(
        profile: String,
        sender: u64,
        online: Arc<AtomicBool>,
        shared_secret: Option<String>,
    ) -> Self {
        Self {
            profile,
            sender,
            online,
            shared_secret,
        }
    }
}

/// One exact remote member of the upstream consensus engine.
pub(crate) struct ReplicaNetwork {
    /// Exact remote infrastructure identity.
    target: u64,
    /// Upstream-declared member endpoint.
    node: BasicNode,
    /// Shared immutable cluster identity.
    profile: String,
    /// Local sending member identity.
    sender: u64,
    /// Shared partition control, unused by the production deployment API.
    online: Arc<AtomicBool>,
    /// Optional shared transport secret.
    shared_secret: Option<String>,
}

impl RaftNetworkFactory<ControllerRaftConfig> for ReplicaNetworkFactory {
    type Network = ReplicaNetwork;

    /// Creates an inert exact-member client without aliasing robot Nodes or opening a socket.
    async fn new_client(&mut self, target: u64, node: &BasicNode) -> Self::Network {
        ReplicaNetwork {
            target,
            node: node.clone(),
            profile: self.profile.clone(),
            sender: self.sender,
            online: self.online.clone(),
            shared_secret: self.shared_secret.clone(),
        }
    }
}

impl ReplicaNetwork {
    /// Sends one bounded frame within the upstream request budget.
    async fn exchange(&self, command: Command, option: RPCOption) -> Result<Reply, std::io::Error> {
        if !self.online.load(Ordering::Relaxed) {
            return Err(std::io::Error::other("test partition is active"));
        }
        let request = Request {
            profile: self.profile.clone(),
            target: self.target,
            sender: self.sender,
            auth_digest: request_auth_digest(
                self.shared_secret.as_deref(),
                &self.profile,
                self.target,
                self.sender,
                &command,
            ),
            command,
        };
        let encoded = serde_json::to_vec(&request).map_err(std::io::Error::other)?;
        if encoded.len() > MAX_FRAME_BYTES {
            return Err(std::io::Error::other("replica frame exceeds bounds"));
        }
        let operation = async {
            let mut stream = tokio::net::TcpStream::connect(&self.node.addr).await?;
            stream.write_u64(encoded.len() as u64).await?;
            stream.write_all(&encoded).await?;
            let length = stream.read_u64().await?;
            if length == 0 || length > MAX_FRAME_BYTES as u64 {
                return Err(std::io::Error::other("replica reply exceeds bounds"));
            }
            let mut bytes = vec![0; length as usize];
            stream.read_exact(&mut bytes).await?;
            serde_json::from_slice(&bytes).map_err(std::io::Error::other)
        };
        tokio::time::timeout(option.hard_ttl(), operation)
            .await
            .map_err(std::io::Error::other)?
    }
}

impl RaftNetwork<ControllerRaftConfig> for ReplicaNetwork {
    /// Forwards exact append RPCs; failures remain network/remote errors to OpenRaft.
    async fn append_entries(
        &mut self,
        rpc: AppendEntriesRequest<ControllerRaftConfig>,
        option: RPCOption,
    ) -> Result<AppendEntriesResponse<u64>, RPCError<u64, BasicNode, RaftError<u64>>> {
        match self
            .exchange(Command::Append(rpc), option)
            .await
            .map_err(|error| RPCError::Unreachable(Unreachable::new(&error)))?
        {
            Reply::Append(Ok(reply)) => Ok(reply),
            Reply::Append(Err(error)) => Err(RPCError::RemoteError(RemoteError::new_with_node(
                self.target,
                self.node.clone(),
                error,
            ))),
            _ => Err(RPCError::Unreachable(Unreachable::new(
                &std::io::Error::other("replica append response rejected"),
            ))),
        }
    }

    /// Forwards exact election RPCs without implementing any local election shortcut.
    async fn vote(
        &mut self,
        rpc: VoteRequest<u64>,
        option: RPCOption,
    ) -> Result<VoteResponse<u64>, RPCError<u64, BasicNode, RaftError<u64>>> {
        match self
            .exchange(Command::Vote(rpc), option)
            .await
            .map_err(|error| RPCError::Unreachable(Unreachable::new(&error)))?
        {
            Reply::Vote(Ok(reply)) => Ok(reply),
            Reply::Vote(Err(error)) => Err(RPCError::RemoteError(RemoteError::new_with_node(
                self.target,
                self.node.clone(),
                error,
            ))),
            _ => Err(RPCError::Unreachable(Unreachable::new(
                &std::io::Error::other("replica vote response rejected"),
            ))),
        }
    }

    /// Transmits upstream snapshot chunks under the same fixed profile and size bound.
    async fn install_snapshot(
        &mut self,
        rpc: InstallSnapshotRequest<ControllerRaftConfig>,
        option: RPCOption,
    ) -> Result<
        InstallSnapshotResponse<u64>,
        RPCError<u64, BasicNode, RaftError<u64, InstallSnapshotError>>,
    > {
        match self
            .exchange(Command::Snapshot(rpc), option)
            .await
            .map_err(|error| RPCError::Unreachable(Unreachable::new(&error)))?
        {
            Reply::Snapshot(Ok(reply)) => Ok(reply),
            Reply::Snapshot(Err(error)) => Err(RPCError::RemoteError(RemoteError::new_with_node(
                self.target,
                self.node.clone(),
                error,
            ))),
            _ => Err(RPCError::Unreachable(Unreachable::new(
                &std::io::Error::other("replica snapshot response rejected"),
            ))),
        }
    }
}

/// Startup-frozen synchronous port used inside a Controller's existing SQL transaction/send boundary.
#[derive(Clone)]
pub struct ReplicaFenceClient {
    /// Parent's validated trusted loopback endpoint.
    address: SocketAddr,
    /// Exact cluster/application profile.
    profile: String,
    /// Original issuing child epoch, never renewed after process creation.
    epoch: ControllerEpoch,
    /// Optional shared transport secret inherited from the supervisor.
    shared_secret: Option<String>,
}

impl ReplicaFenceClient {
    /// Constructs an exact child fence without connecting or granting leadership.
    pub fn new(
        address: SocketAddr,
        profile: String,
        epoch: ControllerEpoch,
    ) -> Result<Self, String> {
        if !address.ip().is_loopback()
            || address.port() == 0
            || profile.is_empty()
            || epoch.replica_id == 0
        {
            return Err("invalid Controller replica child fence".into());
        }
        Ok(Self {
            address,
            profile,
            epoch,
            shared_secret: None,
        })
    }

    /// Reads the complete startup triple; partial or non-Unicode configuration rejects before serving.
    pub fn from_environment() -> Result<Option<Self>, String> {
        let read = |name| match std::env::var(name) {
            Ok(value) => Ok(Some(value)),
            Err(std::env::VarError::NotPresent) => Ok(None),
            Err(_) => Err("replica child environment must be UTF-8".to_string()),
        };
        match (
            read("ROBOGUIDE_REPLICA_ENDPOINT")?,
            read("ROBOGUIDE_REPLICA_PROFILE")?,
            read("ROBOGUIDE_REPLICA_EPOCH")?,
        ) {
            (None, None, None) => Ok(None),
            (Some(address), Some(profile), Some(epoch)) => {
                let mut client = Self::new(
                    address.parse().map_err(|_| "invalid replica endpoint")?,
                    profile,
                    serde_json::from_str(&epoch).map_err(|_| "invalid replica epoch")?,
                )?;
                client.shared_secret = read("ROBOGUIDE_REPLICA_SHARED_SECRET")?;
                if let Some(secret) = client.shared_secret.as_ref()
                    && (secret.len() < 32 || secret.len() > 256)
                {
                    return Err("invalid replica shared secret length".into());
                }
                Ok(Some(client))
            }
            _ => Err("replica child fence requires endpoint/profile/epoch together".into()),
        }
    }

    /// Sends one exact child request; its independent parent runtime prevents SQL/consensus deadlock.
    fn exchange(&self, command: Command) -> Result<Reply, String> {
        let request = Request {
            profile: self.profile.clone(),
            target: self.epoch.replica_id,
            sender: self.epoch.replica_id,
            auth_digest: request_auth_digest(
                self.shared_secret.as_deref(),
                &self.profile,
                self.epoch.replica_id,
                self.epoch.replica_id,
                &command,
            ),
            command,
        };
        let bytes = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
        if bytes.len() > MAX_FRAME_BYTES {
            return Err("replica child request exceeds bounds".into());
        }
        let mut stream =
            std::net::TcpStream::connect_timeout(&self.address, Duration::from_secs(2))
                .map_err(|error| error.to_string())?;
        stream
            .set_read_timeout(Some(Duration::from_secs(12)))
            .map_err(|error| error.to_string())?;
        stream
            .set_write_timeout(Some(Duration::from_secs(12)))
            .map_err(|error| error.to_string())?;
        stream
            .write_all(&(bytes.len() as u64).to_be_bytes())
            .map_err(|error| error.to_string())?;
        stream
            .write_all(&bytes)
            .map_err(|error| error.to_string())?;
        let mut prefix = [0; 8];
        stream
            .read_exact(&mut prefix)
            .map_err(|error| error.to_string())?;
        let length = u64::from_be_bytes(prefix);
        if length == 0 || length > MAX_FRAME_BYTES as u64 {
            return Err("replica child reply exceeds bounds".into());
        }
        let mut bytes = vec![0; length as usize];
        stream
            .read_exact(&mut bytes)
            .map_err(|error| error.to_string())?;
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    /// Confirms current majority and original epoch for both write and actual command delivery.
    pub fn check(&self) -> Result<(), String> {
        match self.exchange(Command::Fence(self.epoch))? {
            Reply::Fence(result) => result,
            _ => Err("replica child epoch fence rejected".into()),
        }
    }
}

/// Computes the exact transport authentication digest without exposing the shared secret on wire.
fn request_auth_digest(
    secret: Option<&str>,
    profile: &str,
    target: u64,
    sender: u64,
    command: &Command,
) -> Option<String> {
    let secret = secret?;
    let payload = serde_json::to_vec(&(profile, target, sender, command)).ok()?;
    let mut digest = Sha256::new();
    digest.update(secret.as_bytes());
    digest.update(payload);
    Some(format!("{:x}", digest.finalize()))
}

impl state::ApplicationBatchReplicator for ReplicaFenceClient {
    /// Returns the immutable cluster/profile required by every child checkpoint.
    fn profile_identity(&self) -> &str {
        &self.profile
    }

    /// Requires original epoch/quorum before any private authority mutation begins.
    fn assert_write_authority(&self) -> Result<(), String> {
        self.check()
    }

    /// Waits for majority durability of the complete candidate before local SQL Commit.
    fn confirm_batch(&self, batch: state::ApplicationCommitBatch) -> Result<(), String> {
        let target = AcceptedApplicationIdentity {
            revision: 0,
            event_sequence: batch.checkpoint.event_sequence,
            checkpoint_digest: Some(state::checkpoint_identity(&batch.checkpoint)),
        };
        let commit = self.exchange(Command::Commit {
            epoch: self.epoch,
            batch,
        });
        match commit {
            Ok(Reply::Commit(Ok(receipt))) if receipt.accepted => Ok(()),
            Ok(Reply::Commit(Err(error))) => {
                match self.exchange(Command::AcceptedIdentity { epoch: self.epoch }) {
                    Ok(Reply::AcceptedIdentity(Ok(identity)))
                        if identity.event_sequence == target.event_sequence
                            && identity.checkpoint_digest == target.checkpoint_digest =>
                    {
                        Ok(())
                    }
                    Ok(Reply::AcceptedIdentity(Ok(identity))) => Err(format!(
                        "quorum Commit failed ({error}); accepted identity is revision {}, event {}, digest {:?}",
                        identity.revision, identity.event_sequence, identity.checkpoint_digest
                    )),
                    Ok(Reply::AcceptedIdentity(Err(identity_error))) => Err(format!(
                        "quorum Commit failed ({error}); accepted identity read failed: {identity_error}"
                    )),
                    Ok(_) => Err(format!(
                        "quorum Commit failed ({error}); accepted identity response was invalid"
                    )),
                    Err(identity_error) => Err(format!(
                        "quorum Commit failed ({error}); accepted identity read failed: {identity_error}"
                    )),
                }
            }
            Ok(_) => Err("replica application acceptance rejected".into()),
            Err(original_error) => {
                match self.exchange(Command::AcceptedIdentity { epoch: self.epoch }) {
                    Ok(Reply::AcceptedIdentity(Ok(identity)))
                        if identity.event_sequence == target.event_sequence
                            && identity.checkpoint_digest == target.checkpoint_digest =>
                    {
                        Ok(())
                    }
                    Ok(Reply::AcceptedIdentity(Ok(identity))) => Err(format!(
                        "ambiguous quorum Commit response ({original_error}); accepted identity is revision {}, event {}, digest {:?}",
                        identity.revision, identity.event_sequence, identity.checkpoint_digest
                    )),
                    Ok(Reply::AcceptedIdentity(Err(error))) => Err(format!(
                        "ambiguous quorum Commit response ({original_error}); accepted identity read failed: {error}"
                    )),
                    Ok(_) => Err(format!(
                        "ambiguous quorum Commit response ({original_error}); accepted identity response was invalid"
                    )),
                    Err(identity_error) => Err(format!(
                        "ambiguous quorum Commit response ({original_error}); accepted identity read failed: {identity_error}"
                    )),
                }
            }
        }
    }
}

impl ports::CommandDeliveryFence for ReplicaFenceClient {
    /// Rechecks the exact issuing epoch and majority immediately before each actual send.
    fn check_delivery(&self) -> Result<(), String> {
        self.check()
    }

    /// Serializes the immutable issuing epoch for Node-side monotonic command fencing.
    fn controller_epoch(&self) -> Option<String> {
        serde_json::to_string(&self.epoch).ok()
    }
}

#[cfg(test)]
mod auth_tests {
    //! Replica envelope authentication regressions.

    use super::*;

    /// Authentication binds the exact command envelope and requires an explicit secret.
    #[test]
    fn envelope_digest_binds_target_and_secret() {
        let command = Command::AcceptedIdentity {
            epoch: ControllerEpoch {
                term: 3,
                replica_id: 1,
            },
        };
        let secret = "s".repeat(32);
        let first = request_auth_digest(Some(&secret), "profile", 2, 1, &command);
        assert!(first.is_some());
        assert_ne!(
            first,
            request_auth_digest(Some(&secret), "profile", 3, 1, &command)
        );
        assert_ne!(
            first,
            request_auth_digest(Some(&"t".repeat(32)), "profile", 2, 1, &command)
        );
        assert_eq!(request_auth_digest(None, "profile", 2, 1, &command), None);
    }
}
