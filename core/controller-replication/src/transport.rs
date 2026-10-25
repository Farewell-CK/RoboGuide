//! Bounded trusted-loopback replica RPC and synchronous child acceptance/delivery fencing.

use crate::transport_budget::{MAX_FRAME_BYTES, TransportBudget};
use crate::*;
use openraft::StoredMembership;
use openraft::error::{InstallSnapshotError, RPCError, RaftError, RemoteError, Unreachable};
use openraft::network::{RPCOption, RaftNetwork, RaftNetworkFactory};
use openraft::raft::{
    AppendEntriesRequest, AppendEntriesResponse, InstallSnapshotRequest, InstallSnapshotResponse,
    VoteRequest, VoteResponse,
};
use sha2::{Digest as _, Sha256};
use std::io::{Read, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_rustls::{TlsAcceptor, TlsConnector};

#[path = "content_transport.rs"]
mod content_streams;
#[path = "log_content_transport.rs"]
mod log_content;
#[path = "snapshot_content_transport.rs"]
mod snapshot_content;
pub(crate) use content_streams::{
    StreamNotice, StreamReceive, StreamRoute, receive_owned_stream, send_committed_content_stream,
    send_content_stream, send_content_stream_checked, send_historical_log_stream,
};
pub(crate) use log_content::on_worker as content_worker;

#[cfg(test)]
#[path = "transport_tls_tests.rs"]
mod tls_tests;

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
    /// Explicit actual application-generation negotiation; older members refuse the new envelope.
    ApplicationUpgradeV1(Box<crate::upgrade_network::UpgradeWire>),
    /// Exact readonly parent9 policy negotiation under the original leader and committed membership.
    ContentInitializationContractV1 {
        /// Original current leader, never inferred from a supported-format boolean.
        epoch: ControllerEpoch,
        /// Exact original committed voters/nodes, including joint configuration and Root bindings.
        membership: StoredMembership<u64, ReplicaMember>,
    },
    /// Readonly contract negotiation; a supported Root generation is not byte or application readiness.
    SnapshotContentContractV1,
    /// Original durable prerequisite issue before any new content source payload read.
    PrepareSnapshotContentV1 {
        /// Complete original authenticated source and exact Snapshot/input identity.
        header: Box<SnapshotContentHeader>,
        /// Original receive-relative budget sampled before queue admission.
        budget_ms: u64,
    },
    /// Independent actual complete file verification under the first original receiving budget.
    CompleteSnapshotContentV1 {
        /// Complete original immutable receive scope, never a replacement target.
        header: Box<SnapshotContentHeader>,
    },
    /// Explicit byte route scoped by an already durable original Snapshot receive.
    PushSnapshotContentV1 {
        /// Complete original immutable receive scope.
        header: Box<SnapshotContentHeader>,
        /// Exact qualified original input.
        content: ArtifactContentKey,
        /// Deterministic first original stream operation, independent of retry.
        command_id: String,
        /// Original receive-relative stream bound, additionally fenced by the Snapshot issue.
        budget_ms: u64,
    },
    /// Describes an actual source-committed historical Log without payload reads or effects.
    HistoricalLogTargetV1 {
        /// Exact original retained position, independently checked by both members.
        log_id: openraft::LogId<u64>,
    },
    /// Requests one immutable historical transfer to the authenticated requesting member.
    RouteHistoricalLogV1 {
        /// Exact original retained position.
        log_id: openraft::LogId<u64>,
        /// Frozen original source observation and historical candidate inputs.
        target: Box<ContentQuorumTarget>,
        /// Complete original qualified input.
        content: ArtifactContentKey,
        /// Deterministic original source/receiver/epoch/Log/input identity.
        command_id: String,
        /// Original request-relative budget, frozen before any queue.
        budget_ms: u64,
    },
    /// Separate exact retained-Log route; old peers refuse rather than widening accepted-view routing.
    PushHistoricalLogContentV1 {
        /// Original durable Log shared independently by source and receiver.
        log_id: openraft::LogId<u64>,
        /// Exact current membership and original historical candidate context.
        target: Box<ContentQuorumTarget>,
        /// Exact original qualified bytes.
        content: ArtifactContentKey,
        /// One retained immutable receiver operation.
        command_id: String,
        /// Original receive-relative budget sampled before its queue.
        budget_ms: u64,
    },
    /// Independently observes actual complete committed metadata and bytes; never executes an application.
    ObserveContent {
        /// Exact original epoch/membership/application/qualified-input observation target.
        target: Box<ContentQuorumTarget>,
    },
    /// Exact original network byte receive; actual TLS/current membership supplies permission.
    PushContent {
        /// Exact original current metadata/membership/qualified input target.
        target: Box<ContentQuorumTarget>,
        /// One complete original input from the target.
        content: ArtifactContentKey,
        /// Original immutable receive ID, never an automatic repair ID.
        command_id: String,
        /// Original bounded receiver duration, frozen before its writer wait/queue.
        budget_ms: u64,
    },
    /// Explicit committed-only route; older peers refuse instead of widening proposal staging.
    PushCommittedContentV1 {
        /// Exact complete already-applied target on both original members.
        target: Box<ContentQuorumTarget>,
        /// Original qualified content, never another Artifact owner alias.
        content: ArtifactContentKey,
        /// One original retained route operation.
        command_id: String,
        /// Original receiver duration, issued before queue admission.
        budget_ms: u64,
    },
    /// Observes actual loaded TLS credentials and local committed trust without claiming quorum.
    CertificateObserve,
    /// Reads quorum-current committed certificate authority, without modifying lifecycle.
    AdminCertificateTrust,
    /// Closed administration request; the real connection supplies caller identity.
    AdminRotateCertificate {
        /// Original leader epoch obtained by the administration client.
        epoch: ControllerEpoch,
        /// Exact public request; no caller identity or confirmation booleans are accepted.
        request: CertificateRequest,
    },
    /// Requests one member's independent TLS contact with the original staged target.
    CertificateProbe {
        /// Original complete committed stage to observe.
        revision: u64,
        /// Exact original trust projection identity.
        trust_digest: String,
        /// Stable member being rotated.
        target: u64,
        /// Exact staged new leaf required before target application bytes.
        target_leaf: String,
    },
    /// Confirms the target's actual served TLS leaf and exact original stage.
    CertificateTarget {
        /// Original stage revision; unavailable or stale stages fail.
        revision: u64,
        /// Original trust identity; no caller-supplied success flags.
        trust_digest: String,
    },
    /// Upstream log replication.
    Append(AppendEntriesRequest<ControllerRaftConfig>),
    /// Upstream election.
    Vote(VoteRequest<u64>),
    /// Upstream bounded snapshot chunk.
    Snapshot(InstallSnapshotRequest<ControllerRaftConfig>),
    /// Explicit content-ready original Snapshot metadata, separate from the old metadata-only envelope.
    SnapshotPreparedV1 {
        /// Complete first issue, rechecked against actual TLS on every metadata chunk.
        header: Box<SnapshotContentHeader>,
        /// Original upstream metadata chunk, still interpreted only by OpenRaft.
        rpc: InstallSnapshotRequest<ControllerRaftConfig>,
    },
    /// Original child epoch requests current quorum confirmation.
    Fence(ControllerEpoch),
    /// Exact private application transaction requests quorum durability.
    Commit {
        /// Original issuing child epoch.
        epoch: ControllerEpoch,
        /// Complete candidate with immutable event/source evidence.
        batch: state::ApplicationCommitBatch,
    },
    /// Read one original member-local handoff without election or application authority.
    ObserveLeaderTransfer {
        /// Original bounded operation ID, never a permission to repeat an effect.
        transfer_id: String,
    },
    /// Read the accepted application identity after an ambiguous Commit response.
    AcceptedIdentity {
        /// Original issuing epoch whose quorum must still be current.
        epoch: ControllerEpoch,
    },
    /// Target-side controlled election used by the explicit leader transfer profile.
    #[serde(rename = "TransferLeadershipV3")]
    TransferLeadership {
        /// Epoch of the current leader that must still be the target's observed leader.
        epoch: ControllerEpoch,
        /// Target member identity; it must equal the receiving process.
        target: u64,
        /// Stable operator operation identity.
        transfer_id: String,
        /// Source last-log index required before triggering the election.
        required_last_log_index: Option<u64>,
        /// Actual original source child wait or scoped empty-process proof.
        source_drain: ControllerDrainProof,
        /// Remaining original effect budget; receiver freezes its own ingress-relative deadline.
        effect_budget_ms: u64,
    },
    /// Read the leader epoch for an authenticated member-side administration client.
    AdminEpoch,
    /// Read the currently committed voter set for an administration audit.
    AdminMembership,
    /// Add one configured member as a learner through the current leader.
    AdminAddLearner {
        /// Current leader epoch captured before the mutation.
        epoch: ControllerEpoch,
        /// Pre-enrolled member identity to catch up.
        member_id: u64,
    },
    /// Apply a voter set through OpenRaft joint consensus.
    AdminChangeMembership {
        /// Current leader epoch captured before the mutation.
        epoch: ControllerEpoch,
        /// Desired voter identities.
        voters: std::collections::BTreeSet<u64>,
        /// Whether removed voters remain as learners.
        retain_removed_as_learners: bool,
    },
    /// Ask the current leader to perform the complete target-side transfer request.
    AdminRequestTransfer {
        /// Current leader epoch captured before the mutation.
        epoch: ControllerEpoch,
        /// Target voter identity.
        target: u64,
        /// Stable operator operation identity.
        transfer_id: String,
    },
}

impl Command {
    /// Keeps actual member credentials current while queued administration waits for its writer.
    fn requires_member_writer(&self) -> bool {
        matches!(self, Self::ApplicationUpgradeV1(wire) if matches!(wire.as_ref(), crate::upgrade_network::UpgradeWire::Drain { .. } | crate::upgrade_network::UpgradeWire::ObserveDrain { .. }))
            || matches!(
                self,
                Self::Commit { .. }
                    | Self::PushContent { .. }
                    | Self::PushCommittedContentV1 { .. }
                    | Self::PushHistoricalLogContentV1 { .. }
                    | Self::PrepareSnapshotContentV1 { .. }
                    | Self::CompleteSnapshotContentV1 { .. }
                    | Self::PushSnapshotContentV1 { .. }
                    | Self::AdminRotateCertificate { .. }
                    | Self::AdminAddLearner { .. }
                    | Self::AdminChangeMembership { .. }
                    | Self::AdminRequestTransfer { .. }
                    | Self::TransferLeadership { .. }
            )
    }
}

/// Closed response preserves upstream Raft errors separately from child acceptance.
#[derive(Serialize, Deserialize)]
pub(crate) enum Reply {
    /// Original authenticated support, drain, activation or readonly reconciliation evidence.
    ApplicationUpgradeV1(Result<crate::upgrade_network::UpgradeReply, String>),
    /// Actual immutable local parent9 declaration, without claiming source or byte availability.
    ContentInitializationContract(Result<Option<ContentInitializationPolicy>, String>),
    /// Actual explicit local Root generation, with no claim of content readiness.
    SnapshotContentContract(Result<bool, String>),
    /// Exact immutable original prerequisite evidence, never parent application success.
    SnapshotContent(Result<SnapshotContentStatus, String>),
    /// Exact readonly source description; a serialized target alone is never content permission.
    HistoricalLogTarget(Result<ContentQuorumTarget, String>),
    /// Actual original historical stream outcome, independently checked in the receiver's Root.
    HistoricalLogRouted(Result<ReplicaContentStreamStatus, String>),
    /// Actual authenticated member observation; its standalone serialized data never grants a quorum.
    ContentObservation(Result<MemberContentObservation, String>),
    /// Original receive readiness or immutable existing outcome; never an application ACK.
    ContentStream(Result<content_streams::StreamNotice, String>),
    /// Actual full-byte stream receipt, distinct from complete metadata/content majority.
    ContentStreamFinished(Result<ReplicaContentStreamStatus, String>),
    /// Exact original committed trust, separate from application state.
    CertificateTrust(Result<CertificateTrust, String>),
    /// Immutable accepted rotation result, including original actual confirmations.
    CertificateReceipt(Result<CertificateReceipt, String>),
    /// Actual endpoint TLS proof, bound to both current member and original stage.
    CertificateProbe(Result<CertificateProbe, String>),
    /// Actual target endpoint and served leaf from this very TLS connection.
    CertificateTarget(Result<CertificateObservation, String>),
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
    /// Target-side transfer admission; it does not mean the target has won an election.
    Transfer(Result<LeaderTransferReceipt, String>),
    /// Readonly exact member-local operation evidence.
    LeaderTransferRecord(Result<Option<LeaderTransferRecord>, String>),
    /// Current leader epoch observed by an administration client.
    Epoch(Option<ControllerEpoch>),
    /// Current committed voter identities.
    Membership(BTreeSet<u64>),
    /// Membership administration result.
    Admin(Result<(), String>),
    /// Identity/framing/profile rejected before entering the consensus engine.
    Rejected(String),
}

/// Scoped private listener, stopped without touching any Local EAIOS or external process.
pub struct ReplicaServer {
    /// Owned accept-loop task.
    task: tokio::task::JoinHandle<()>,
}

impl Drop for ReplicaServer {
    /// Cancels the accept loop and its owned network handlers, without denying committed effects.
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl ReplicaServer {
    /// Binds the validated member endpoint and admits only exact profile/target envelopes.
    pub(crate) async fn bind(node: ReplicaNode) -> Result<Self, String> {
        let listener = tokio::net::TcpListener::bind(node.local_address())
            .await
            .map_err(|error| error.to_string())?;
        let node = Arc::new(node);
        node.network_source
            .set(Arc::downgrade(&node))
            .map_err(|_| "original running Snapshot network context was already registered")?;
        let task = tokio::spawn(async move {
            let mut handlers = tokio::task::JoinSet::new();
            loop {
                let accepted = tokio::select! {
                    completed = handlers.join_next(), if !handlers.is_empty() => {
                        let _ = completed;
                        continue;
                    }
                    accepted = listener.accept() => accepted,
                };
                let Ok((stream, _)) = accepted else {
                    return;
                };
                let Ok(permit) = node.transport_budget.inbound() else {
                    continue;
                };
                let node = node.as_ref().clone();
                handlers.spawn(async move {
                    let _permit = permit;
                    let budget = node.transport_budget.clone();
                    match tokio::time::timeout(
                        Duration::from_secs(15),
                        serve_connection(stream, node),
                    )
                    .await
                    {
                        Err(_) => budget.timed_out(),
                        Ok(Err(error)) => tracing::warn!(
                            error = %error.chars().take(512).collect::<String>(),
                            "Replica transport refused its original request"
                        ),
                        Ok(Ok(())) => {}
                    }
                });
            }
        });
        Ok(Self { task })
    }
}

/// Retains the actual member writer and original connection through owned administration effects.
fn member_command_permission(
    node: ReplicaNode,
    sender: u64,
    connection: Option<crate::tls::VerifiedTlsConnection>,
    writer: Option<Arc<tokio::sync::OwnedMutexGuard<()>>>,
) -> crate::content_store::StreamPermission {
    Arc::new(move || {
        let _writer = writer
            .as_ref()
            .ok_or("original member command writer is absent")?;
        let network = if node.config.streams_content() {
            Some(node.store.try_network_authority()?)
        } else {
            None
        };
        let admitted_member = network.as_ref().map_or_else(
            || node.member_allowed(sender),
            |(_, membership)| membership.nodes().any(|(id, _)| *id == sender),
        );
        if !admitted_member {
            return Err("original administration caller is no longer a current member".into());
        }
        if node.tls.is_some() {
            let connection = connection
                .as_ref()
                .ok_or("original administration TLS witness is absent")?;
            if let Some((trust, _)) = network {
                connection.authorize_with_trust(sender, node.config.replica_id, &trust)?;
            } else if node.config.rotates_certificates() {
                connection.authorize_with_trust(
                    sender,
                    node.config.replica_id,
                    &node.store.connection_certificate_trust()?,
                )?;
            } else if !connection.authorize(sender, node.config.replica_id)? {
                return Err(
                    "original legacy administration credentials are no longer admitted".into(),
                );
            }
        }
        Ok(())
    })
}

/// Dispatches one bounded request; the existing OpenRaft engine alone interprets consensus messages.
async fn serve_connection(stream: tokio::net::TcpStream, node: ReplicaNode) -> Result<(), String> {
    if let Some(tls) = &node.tls {
        let tls = tls.refreshed()?;
        let acceptor = TlsAcceptor::from(tls.server.clone());
        let stream = acceptor
            .accept(stream)
            .await
            .map_err(|error| format!("replica TLS handshake failed: {error}"))?;
        let peer_chain = stream
            .get_ref()
            .1
            .peer_certificates()
            .ok_or("mutual TLS peer chain is absent")?
            .iter()
            .map(|certificate| certificate.clone().into_owned())
            .collect();
        return serve_framed_connection(
            stream,
            node,
            Some(crate::tls::VerifiedTlsConnection {
                local: tls,
                peer_chain,
            }),
        )
        .await;
    }
    serve_framed_connection(stream, node, None).await
}

/// Reads and dispatches one framed request over either raw loopback or an authenticated TLS stream.
async fn serve_framed_connection<S>(
    mut stream: S,
    node: ReplicaNode,
    certificate_connection: Option<crate::tls::VerifiedTlsConnection>,
) -> Result<(), String>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let length = stream.read_u64().await.map_err(|error| error.to_string())?;
    let frame = node
        .transport_budget
        .frame(length)
        .map_err(|error| error.to_string())?;
    let mut bytes = vec![0; length as usize];
    stream
        .read_exact(&mut bytes)
        .await
        .map_err(|error| error.to_string())?;
    let request: Request = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    drop(bytes);
    let received_at = tokio::time::Instant::now();
    let upgrade_budget = match &request.command {
        Command::ApplicationUpgradeV1(wire) => match wire.as_ref() {
            crate::upgrade_network::UpgradeWire::Drain { budget_ms, .. } => {
                Some(crate::content_store::ContentBudget::issue(*budget_ms)?)
            }
            crate::upgrade_network::UpgradeWire::Activate { request, .. } => Some(
                crate::content_store::ContentBudget::issue(request.budget_ms)?,
            ),
            _ => None,
        },
        _ => None,
    };
    let catchup_budget =
        if node.config.streams_content() && matches!(request.command, Command::Append(_)) {
            Some(crate::content_store::ContentBudget::issue(15000)?)
        } else {
            None
        };
    let stream_budget = if let Command::PushContent { budget_ms, .. }
    | Command::PushCommittedContentV1 { budget_ms, .. }
    | Command::RouteHistoricalLogV1 { budget_ms, .. }
    | Command::PushHistoricalLogContentV1 { budget_ms, .. }
    | Command::PrepareSnapshotContentV1 { budget_ms, .. }
    | Command::PushSnapshotContentV1 { budget_ms, .. } = &request.command
    {
        Some(crate::content_store::ContentBudget::issue(*budget_ms)?)
    } else {
        None
    };
    #[cfg(test)]
    if request.command.requires_member_writer() {
        node.framed_member_mutations.fetch_add(1, Ordering::SeqCst);
    }
    let _member_writer = if request.command.requires_member_writer() {
        Some(Arc::new(node.member_writer.clone().lock_owned().await))
    } else {
        None
    };
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
    let peer_certificate_digest = certificate_connection
        .as_ref()
        .and_then(|connection| connection.peer_digest());
    let local_certificate_digest = certificate_connection
        .as_ref()
        .map(|connection| connection.local.local_certificate_digest.clone());
    let ca_bundle_digest = certificate_connection
        .as_ref()
        .map(|connection| connection.local.ca_bundle_digest.clone());
    let certificate_authenticated = match &node.tls {
        None => true,
        Some(_) => match &certificate_connection {
            Some(connection) => connection.authorize(request.sender, node.config.replica_id)?,
            None => false,
        },
    };
    let reply = if request.profile != node.profile_identity()
        || request.target != node.config.replica_id
        || !node.member_allowed(request.sender)
        || !certificate_authenticated
        || authenticated != request.auth_digest
    {
        Reply::Rejected("replica profile/member identity mismatch".into())
    } else {
        match request.command {
            Command::ApplicationUpgradeV1(wire) => {
                Reply::ApplicationUpgradeV1(match &certificate_connection {
                    Some(connection) => {
                        crate::upgrade_network::handle(
                            &node,
                            request.sender,
                            connection,
                            *wire,
                            upgrade_budget,
                        )
                        .await
                    }
                    None => Err("application upgrade requires actual mutual TLS".into()),
                })
            }
            Command::ContentInitializationContractV1 { epoch, membership } => {
                let sender = request.sender;
                let connection = certificate_connection.clone();
                Reply::ContentInitializationContract(
                    log_content::on_worker(&node, move |current| {
                        if !current.store.supports_content_initialization()
                            || epoch.replica_id != sender
                        {
                            return Err(
                                "original member lacks parent9 initialization contract".into()
                            );
                        }
                        let (trust, actual) = current.store.try_network_authority()?;
                        let metrics = current.raft.metrics().borrow().clone();
                        if actual != membership
                            || metrics.membership_config.as_ref() != &membership
                            || metrics.current_term != epoch.term
                            || metrics.current_leader != Some(epoch.replica_id)
                        {
                            return Err(
                                "original initialization contract epoch/membership changed".into(),
                            );
                        }
                        current
                            .store
                            .validate_content_membership(membership.membership())?;
                        connection
                            .as_ref()
                            .ok_or("initialization contract requires actual mutual TLS")?
                            .authorize_with_trust(sender, current.config.replica_id, &trust)?;
                        Ok(current.store.content_initialization_policy().cloned())
                    })
                    .await,
                )
            }
            Command::SnapshotContentContractV1 => Reply::SnapshotContentContract(
                node.store
                    .network_content_store()
                    .map(|root| root.snapshot_receives_enabled()),
            ),
            Command::PrepareSnapshotContentV1 {
                header,
                budget_ms: _,
            } => Reply::SnapshotContent(
                snapshot_content::prepare(
                    &node,
                    request.sender,
                    *header,
                    stream_budget.ok_or("original Snapshot prepare budget is absent")?,
                    certificate_connection
                        .clone()
                        .ok_or("Snapshot prepare requires actual mutual TLS")?,
                )
                .await,
            ),
            Command::CompleteSnapshotContentV1 { header } => Reply::SnapshotContent(
                snapshot_content::complete(
                    &node,
                    request.sender,
                    *header,
                    certificate_connection
                        .clone()
                        .ok_or("Snapshot completion requires actual mutual TLS")?,
                )
                .await,
            ),
            Command::PushSnapshotContentV1 {
                header,
                content,
                command_id,
                budget_ms: _,
            } => {
                snapshot_content::receive(
                    &mut stream,
                    &node,
                    request.sender,
                    *header,
                    content,
                    command_id,
                    stream_budget.ok_or("original Snapshot stream budget is absent")?,
                    certificate_connection
                        .clone()
                        .ok_or("Snapshot stream requires actual mutual TLS")?,
                )
                .await?;
                return Ok(());
            }
            Command::HistoricalLogTargetV1 { log_id } => Reply::HistoricalLogTarget(
                log_content::on_worker(&node, move |current| current.historical_log_target(log_id))
                    .await,
            ),
            Command::RouteHistoricalLogV1 {
                log_id,
                target,
                content,
                command_id,
                budget_ms: _,
            } => Reply::HistoricalLogRouted(
                node.route_historical_log_to(
                    request.sender,
                    log_id,
                    *target,
                    content,
                    command_id,
                    stream_budget.ok_or("original historical request clock is absent")?,
                )
                .await,
            ),
            Command::PushHistoricalLogContentV1 {
                log_id,
                target,
                content,
                command_id,
                budget_ms: _,
            } => {
                if !target.membership.voter_ids().any(|id| id == request.sender) {
                    return Err("historical source is not a current voter".into());
                }
                if command_id
                    != log_content::operation(
                        &node.profile_identity(),
                        request.sender,
                        node.config.replica_id,
                        log_id,
                        &content,
                    )?
                {
                    return Err(
                        "historical receive differs from its deterministic original operation"
                            .into(),
                    );
                }
                let original = target.clone();
                log_content::on_worker(&node, move |current| {
                    current.store.retained_log_candidate(log_id, false)?;
                    current
                        .store
                        .require_historical_log_target(log_id, &original, false)
                })
                .await?;
                drop(frame);
                return content_streams::receive_content_stream(
                    &mut stream,
                    node.clone(),
                    *target,
                    content,
                    command_id,
                    (request.sender, StreamRoute::HistoricalLog(log_id)),
                    stream_budget.ok_or("original historical receive clock is absent")?,
                    certificate_connection.ok_or("historical route requires actual mTLS")?,
                )
                .await;
            }
            Command::ObserveContent { target } => {
                if request.sender != target.epoch.replica_id {
                    return Err("content observation requires its actual original leader".into());
                }
                let check = content_streams::permission(
                    node.clone(),
                    *target.clone(),
                    request.sender,
                    certificate_connection.ok_or("content observation requires actual mTLS")?,
                );
                Reply::ContentObservation(node.observe_content_checked(*target, check).await)
            }
            Command::PushCommittedContentV1 {
                target,
                content,
                command_id,
                budget_ms: _,
            } => {
                if !target.membership.voter_ids().any(|id| id == request.sender) {
                    return Err("committed route source is not an original voter".into());
                }
                node.store.require_committed_content_target(&target)?;
                drop(frame);
                return content_streams::receive_content_stream(
                    &mut stream,
                    node.clone(),
                    *target,
                    content,
                    command_id,
                    (request.sender, StreamRoute::Committed),
                    stream_budget.ok_or("original route receive clock missing")?,
                    certificate_connection.ok_or("committed route requires actual mTLS")?,
                )
                .await;
            }
            Command::PushContent {
                target,
                content,
                command_id,
                budget_ms: _,
            } => {
                if request.sender != target.epoch.replica_id {
                    return Err(
                        "actual content sender differs from the original target leader".into(),
                    );
                }
                drop(frame);
                return content_streams::receive_content_stream(
                    &mut stream,
                    node.clone(),
                    *target,
                    content,
                    command_id,
                    (request.sender, StreamRoute::Proposal),
                    stream_budget.ok_or("original receive clock is missing")?,
                    certificate_connection.ok_or("content stream requires actual mTLS")?,
                )
                .await;
            }
            Command::CertificateObserve => {
                Reply::CertificateTarget(node.current_certificate_trust().and_then(|trust| {
                    node.confirm_certificate_target(
                        trust.revision,
                        trust.digest(),
                        local_certificate_digest.as_deref().unwrap_or(""),
                        ca_bundle_digest.as_deref().unwrap_or(""),
                    )
                }))
            }
            Command::AdminCertificateTrust => {
                let result = match node.current_epoch() {
                    Some(epoch) => node
                        .check_epoch(epoch)
                        .await
                        .and_then(|()| node.current_certificate_trust()),
                    None => Err("certificate authority requires the current quorum leader".into()),
                };
                Reply::CertificateTrust(result)
            }
            Command::AdminRotateCertificate {
                epoch,
                request: operation,
            } => Reply::CertificateReceipt(
                node.rotate_certificate(
                    epoch,
                    operation,
                    request.sender,
                    peer_certificate_digest.as_deref().unwrap_or(""),
                )
                .await,
            ),
            Command::CertificateProbe {
                revision,
                trust_digest,
                target,
                target_leaf,
            } => Reply::CertificateProbe(
                node.probe_certificate(
                    revision,
                    trust_digest,
                    target,
                    target_leaf,
                    local_certificate_digest.as_deref().unwrap_or(""),
                    ca_bundle_digest.as_deref().unwrap_or(""),
                )
                .await,
            ),
            Command::CertificateTarget {
                revision,
                trust_digest,
            } => Reply::CertificateTarget(node.confirm_certificate_target(
                revision,
                trust_digest,
                local_certificate_digest.as_deref().unwrap_or(""),
                ca_bundle_digest.as_deref().unwrap_or(""),
            )),
            Command::Append(rpc) => {
                let through = rpc.leader_commit;
                let appended = node.raft.append_entries(rpc).await;
                if matches!(appended, Ok(AppendEntriesResponse::Success))
                    && let Some(clock) = catchup_budget
                    && let Err(error) =
                        node.queue_retained_log_catchup(request.sender, through, clock)
                {
                    eprintln!(
                        "original retained Log content remains unavailable: {}",
                        error.chars().take(512).collect::<String>()
                    );
                }
                Reply::Append(appended)
            }
            Command::Vote(rpc) => Reply::Vote(node.raft.vote(rpc).await),
            Command::Snapshot(rpc) => {
                let root6 = node.config.streams_content()
                    && node
                        .store
                        .network_content_store()?
                        .snapshot_receives_enabled();
                if root6 && rpc.meta.snapshot_id.split('-').nth(1) != Some("0") {
                    Reply::Rejected(
                        "Root6 content Snapshot requires its original prepared envelope".into(),
                    )
                } else if rpc
                    .offset
                    .checked_add(rpc.data.len() as u64)
                    .is_none_or(|size| size > MAX_FRAME_BYTES as u64)
                {
                    Reply::Rejected("replica snapshot exceeds bounds".into())
                } else {
                    Reply::Snapshot(node.raft.install_snapshot(rpc).await)
                }
            }
            Command::SnapshotPreparedV1 { header, rpc } => {
                let already_applied = snapshot_content::check_delivery(
                    &node,
                    request.sender,
                    *header,
                    &rpc,
                    certificate_connection
                        .clone()
                        .ok_or("Snapshot metadata requires actual mutual TLS")?,
                )
                .await?;
                if already_applied {
                    Reply::Snapshot(Ok(InstallSnapshotResponse {
                        vote: node.raft.metrics().borrow().vote,
                    }))
                } else if rpc
                    .offset
                    .checked_add(rpc.data.len() as u64)
                    .is_none_or(|size| size > MAX_FRAME_BYTES as u64)
                {
                    Reply::Rejected("replica Snapshot exceeds bounds".into())
                } else {
                    Reply::Snapshot(node.raft.install_snapshot(rpc).await)
                }
            }
            Command::Fence(epoch) => Reply::Fence(if node.config.streams_content() {
                Err("network content application authority requires its original child ingress contract".into())
            } else {
                node.check_epoch(epoch).await
            }),
            Command::Commit { epoch, batch } => Reply::Commit(node.commit(epoch, batch).await),
            Command::AcceptedIdentity { epoch } => {
                let result = if node.config.streams_content() {
                    Err("network content acceptance observation requires its complete original child contract".into())
                } else {
                    node.check_epoch(epoch)
                        .await
                        .and_then(|()| node.accepted_application_identity())
                };
                Reply::AcceptedIdentity(result)
            }
            Command::TransferLeadership {
                epoch,
                target,
                transfer_id,
                required_last_log_index,
                source_drain,
                effect_budget_ms,
            } => Reply::Transfer(if effect_budget_ms == 0 || effect_budget_ms > 8_000 {
                Err("original target effect budget is invalid".into())
            } else if request.sender != epoch.replica_id {
                Err("leader transfer source differs from actual authenticated member".into())
            } else {
                node.accept_leader_transfer(
                    epoch,
                    target,
                    transfer_id,
                    required_last_log_index,
                    source_drain,
                    received_at + Duration::from_millis(effect_budget_ms),
                )
                .await
            }),
            Command::ObserveLeaderTransfer { transfer_id } => {
                Reply::LeaderTransferRecord(if transfer_id.is_empty() || transfer_id.len() > 128 {
                    Err("original transfer observation ID is unbounded".into())
                } else if node.config.streams_content() {
                    content_worker(&node, move |current| {
                        current.store.leader_transfer_by_id(&transfer_id)
                    })
                    .await
                } else {
                    node.store.leader_transfer_by_id(&transfer_id)
                })
            }
            Command::AdminEpoch => Reply::Epoch(node.current_epoch()),
            Command::AdminMembership => Reply::Membership(
                node.raft
                    .metrics()
                    .borrow()
                    .membership_config
                    .voter_ids()
                    .collect(),
            ),
            Command::AdminAddLearner { epoch, member_id } => Reply::Admin(
                node.add_learner_at_epoch(
                    epoch,
                    member_id,
                    member_command_permission(
                        node.clone(),
                        request.sender,
                        certificate_connection.clone(),
                        _member_writer.clone(),
                    ),
                )
                .await
                .and_then(|()| {
                    if node.current_epoch() == Some(epoch) {
                        Ok(())
                    } else {
                        Err("leader epoch changed during learner admission".into())
                    }
                }),
            ),
            Command::AdminChangeMembership {
                epoch,
                voters,
                retain_removed_as_learners,
            } => Reply::Admin(
                node.change_membership_at_epoch(
                    epoch,
                    voters,
                    retain_removed_as_learners,
                    member_command_permission(
                        node.clone(),
                        request.sender,
                        certificate_connection.clone(),
                        _member_writer.clone(),
                    ),
                )
                .await
                .and_then(|()| {
                    if node.current_epoch() == Some(epoch) {
                        Ok(())
                    } else {
                        Err("leader epoch changed during membership change".into())
                    }
                }),
            ),
            Command::AdminRequestTransfer {
                epoch,
                target,
                transfer_id,
            } => Reply::Transfer(if node.current_epoch() == Some(epoch) {
                node.request_leader_transfer(target, transfer_id).await
            } else {
                Err("leader epoch changed before transfer request".into())
            }),
        }
    };
    #[cfg(test)]
    if matches!(reply, Reply::Transfer(Ok(_))) && node.discard_transfer_reply.load(Ordering::SeqCst)
    {
        tokio::time::timeout(Duration::from_secs(4), async {
            loop {
                if node.reconcile_leader_transfer().await? {
                    return Ok::<(), String>(());
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .map_err(|_| "test target quorum did not precede response loss".to_string())??;
        return Err("test discarded original transfer reply after actual target quorum".into());
    }
    drop(frame);
    let encoded = crate::transport_budget::encode(&reply, &node.transport_budget)
        .map_err(|error| error.to_string())?;
    stream
        .write_u64(encoded.bytes.len() as u64)
        .await
        .map_err(|error| error.to_string())?;
    stream
        .write_all(&encoded.bytes)
        .await
        .map_err(|error| error.to_string())?;
    stream.flush().await.map_err(|error| error.to_string())
}

/// Sends one authenticated transfer request to a current voter and returns its admission receipt.
pub(crate) async fn request_leader_transfer(
    node: &ReplicaNode,
    target: u64,
    transfer_id: String,
    epoch: ControllerEpoch,
    required_last_log_index: Option<u64>,
    source_drain: ControllerDrainProof,
    effect_budget_ms: u64,
) -> Result<LeaderTransferReceipt, String> {
    let address = *node
        .config
        .members
        .get(&target)
        .ok_or_else(|| "leader transfer target is not pre-enrolled".to_string())?;
    let network = ReplicaNetwork {
        source: None,
        snapshot_header: None,
        target,
        node: ReplicaMember::new(address.to_string()),
        profile: node.profile_identity(),
        sender: node.config.replica_id,
        online: node.network_online.clone(),
        shared_secret: node.config.shared_secret.clone(),
        tls: node.tls.clone(),
        budget: node.transport_budget.clone(),
    };
    match network
        .exchange(
            Command::TransferLeadership {
                epoch,
                target,
                transfer_id,
                required_last_log_index,
                source_drain,
                effect_budget_ms,
            },
            RPCOption::new(Duration::from_secs(5)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Transfer(result) => result,
        _ => Err("leader transfer response was not a transfer receipt".into()),
    }
}

/// Builds a member-authenticated administration connection without opening replica storage.
fn admin_network(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
) -> Result<ReplicaNetwork, String> {
    network_with_budget(
        config,
        leader,
        tls,
        TransportBudget::new(ReplicaTransportLimits::default())?,
    )
}

/// Shares the original owning member's actual capacities across certificate and handoff RPCs too.
fn node_network(node: &ReplicaNode, target: u64) -> Result<ReplicaNetwork, String> {
    network_with_budget(
        &node.config,
        target,
        node.tls.clone(),
        node.transport_budget.clone(),
    )
}

/// Exchanges one explicit upgrade command and binds reported support/wait to the actual served leaf.
pub(crate) async fn upgrade_exchange(
    node: &ReplicaNode,
    target: u64,
    wire: crate::upgrade_network::UpgradeWire,
    budget_ms: u64,
) -> Result<crate::upgrade_network::UpgradeReply, String> {
    let (reply, leaf) = node_network(node, target)?
        .exchange_observed(
            Command::ApplicationUpgradeV1(Box::new(wire)),
            RPCOption::new(Duration::from_millis(budget_ms)),
            None,
        )
        .await
        .map_err(|error| error.to_string())?;
    match reply {
        Reply::ApplicationUpgradeV1(Ok(response)) => {
            let observed = match &response {
                crate::upgrade_network::UpgradeReply::Support(proof) => {
                    Some((proof.member, proof.leaf.as_str()))
                }
                crate::upgrade_network::UpgradeReply::Drain(Some(proof))
                    if proof.member != target =>
                {
                    return Err("original drain came from another member".into());
                }
                _ => None,
            };
            if observed
                .is_some_and(|(member, served)| member != target || leaf.as_deref() != Some(served))
            {
                return Err(
                    "upgrade evidence differs from actual authenticated target leaf".into(),
                );
            }
            Ok(response)
        }
        Reply::ApplicationUpgradeV1(Err(error)) | Reply::Rejected(error) => Err(error),
        _ => Err("member does not support the explicit application upgrade contract".into()),
    }
}

/// Sends one original administrative operation without ever retrying an uncertain activation.
pub(crate) async fn admin_upgrade(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    wire: crate::upgrade_network::UpgradeWire,
    budget_ms: u64,
) -> Result<crate::upgrade_network::UpgradeReply, String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::ApplicationUpgradeV1(Box::new(wire)),
            RPCOption::new(Duration::from_millis(budget_ms)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::ApplicationUpgradeV1(result) => result,
        Reply::Rejected(error) => Err(error),
        _ => Err("administration target does not support explicit application upgrade".into()),
    }
}

/// Negotiates the actual peer generation/policy over its original current authenticated connection.
pub(crate) async fn initialization_contract(
    node: &ReplicaNode,
    target: u64,
    epoch: ControllerEpoch,
    membership: StoredMembership<u64, ReplicaMember>,
    remaining_ms: u64,
) -> Result<Option<ContentInitializationPolicy>, String> {
    match node_network(node, target)?
        .exchange(
            Command::ContentInitializationContractV1 { epoch, membership },
            RPCOption::new(Duration::from_millis(remaining_ms)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::ContentInitializationContract(result) => result,
        _ => Err("actual peer refused the initialization contract".into()),
    }
}

/// Constructs an inert exact-member client with an actual original capacity owner, never a new grant.
fn network_with_budget(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    budget: Arc<TransportBudget>,
) -> Result<ReplicaNetwork, String> {
    let address = *config
        .members
        .get(&leader)
        .ok_or_else(|| "administration leader is not pre-enrolled".to_string())?;
    Ok(ReplicaNetwork {
        source: None,
        snapshot_header: None,
        target: leader,
        node: ReplicaMember::new(address.to_string()),
        profile: config.profile_identity(),
        sender: config.replica_id,
        online: Arc::new(AtomicBool::new(true)),
        shared_secret: config.shared_secret.clone(),
        tls,
        budget,
    })
}

/// Reads quorum-current infrastructure trust using the actual current member credential source.
pub(crate) async fn admin_certificate_trust(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
) -> Result<CertificateTrust, String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminCertificateTrust,
            RPCOption::new(Duration::from_secs(5)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::CertificateTrust(result) => result,
        Reply::Rejected(error) => Err(error),
        _ => Err("certificate trust response was invalid".into()),
    }
}

/// Binds an endpoint's actual presented leaf to its readonly local trust and CA material observation.
pub(crate) async fn admin_certificate_observation(
    config: &ReplicaConfig,
    member: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
) -> Result<CertificateObservation, String> {
    let (reply, actual_leaf) = admin_network(config, member, tls)?
        .exchange_observed(
            Command::CertificateObserve,
            RPCOption::new(Duration::from_secs(5)),
            None,
        )
        .await
        .map_err(|error| error.to_string())?;
    match reply {
        Reply::CertificateTarget(Ok(observation))
            if observation.member == member
                && actual_leaf.as_deref() == Some(observation.leaf.as_str()) =>
        {
            Ok(observation)
        }
        Reply::CertificateTarget(Err(error)) | Reply::Rejected(error) => Err(error),
        _ => Err("certificate observation differs from actual authenticated endpoint".into()),
    }
}

/// Sends no asserted proof; the leader derives actual caller identity and all retirement observations.
pub(crate) async fn admin_rotate_certificate(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    epoch: ControllerEpoch,
    request: CertificateRequest,
) -> Result<CertificateReceipt, String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminRotateCertificate { epoch, request },
            RPCOption::new(Duration::from_secs(15)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::CertificateReceipt(result) => result,
        Reply::Rejected(error) => Err(error),
        _ => Err("certificate rotation response was invalid".into()),
    }
}

/// Captures the actual member leaf and rejects replies not bound to this real TLS connection.
pub(crate) async fn certificate_probe(
    node: &ReplicaNode,
    member: u64,
    target: u64,
    revision: u64,
    digest: &str,
    target_leaf: &str,
) -> Result<CertificateProbe, String> {
    let (reply, actual_leaf) = node_network(node, member)?
        .exchange_observed(
            Command::CertificateProbe {
                revision,
                trust_digest: digest.into(),
                target,
                target_leaf: target_leaf.into(),
            },
            RPCOption::new(Duration::from_secs(4)),
            None,
        )
        .await
        .map_err(|error| error.to_string())?;
    match reply {
        Reply::CertificateProbe(Ok(probe))
            if probe.member == member
                && actual_leaf.as_deref() == Some(probe.presented_leaf.as_str()) =>
        {
            Ok(probe)
        }
        Reply::CertificateProbe(Err(error)) | Reply::Rejected(error) => Err(error),
        _ => Err("certificate member proof differs from actual TLS identity".into()),
    }
}

/// Requires the exact staged new leaf before sending any original-stage query to the target.
pub(crate) async fn certificate_target(
    node: &ReplicaNode,
    target: u64,
    revision: u64,
    digest: &str,
    target_leaf: &str,
) -> Result<CertificateObservation, String> {
    let (reply, actual_leaf) = node_network(node, target)?
        .exchange_observed(
            Command::CertificateTarget {
                revision,
                trust_digest: digest.into(),
            },
            RPCOption::new(Duration::from_secs(2)),
            Some(target_leaf),
        )
        .await
        .map_err(|error| error.to_string())?;
    match reply {
        Reply::CertificateTarget(Ok(observation))
            if observation.member == target
                && observation.revision == revision
                && observation.trust_digest == digest
                && observation.leaf == target_leaf
                && actual_leaf.as_deref() == Some(target_leaf) =>
        {
            Ok(observation)
        }
        Reply::CertificateTarget(Err(error)) | Reply::Rejected(error) => Err(error),
        _ => Err("certificate target proof differs from original stage and actual TLS".into()),
    }
}

/// Reads the current leader epoch through the private authenticated member envelope.
pub(crate) async fn admin_epoch(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
) -> Result<Option<ControllerEpoch>, String> {
    match admin_network(config, leader, tls)?
        .exchange(Command::AdminEpoch, RPCOption::new(Duration::from_secs(5)))
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Epoch(epoch) => Ok(epoch),
        _ => Err("leader administration epoch response was invalid".into()),
    }
}

/// Reads one member's current epoch using the source node's authenticated profile.
pub(crate) async fn member_epoch(
    node: &ReplicaNode,
    member: u64,
) -> Result<Option<ControllerEpoch>, String> {
    match node_network(node, member)?
        .exchange(Command::AdminEpoch, RPCOption::new(Duration::from_secs(2)))
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Epoch(epoch) => Ok(epoch),
        _ => Err("member epoch response was invalid".into()),
    }
}

/// Observes only an original target's local handoff proof through actual member authentication.
pub(crate) async fn observe_leader_transfer(
    node: &ReplicaNode,
    member: u64,
    transfer_id: &str,
) -> Result<Option<LeaderTransferRecord>, String> {
    observe_transfer(node_network(node, member)?, transfer_id).await
}

/// Reads an original infrastructure operation using an explicitly configured actual member peer.
pub(crate) async fn admin_observe_leader_transfer(
    config: &ReplicaConfig,
    member: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    transfer_id: &str,
) -> Result<Option<LeaderTransferRecord>, String> {
    observe_transfer(admin_network(config, member, tls)?, transfer_id).await
}

/// Reads one immutable operation through the caller's original actual client capacity.
async fn observe_transfer(
    network: ReplicaNetwork,
    transfer_id: &str,
) -> Result<Option<LeaderTransferRecord>, String> {
    if transfer_id.trim().is_empty() || transfer_id.len() > 128 {
        return Err("original transfer observation identity is invalid".into());
    }
    match network
        .exchange(
            Command::ObserveLeaderTransfer {
                transfer_id: transfer_id.into(),
            },
            RPCOption::new(Duration::from_secs(2)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::LeaderTransferRecord(record) => record,
        _ => Err("original target handoff observation response differs".into()),
    }
}

/// Reads the current voter set through the authenticated member envelope.
pub(crate) async fn admin_membership(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
) -> Result<std::collections::BTreeSet<u64>, String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminMembership,
            RPCOption::new(Duration::from_secs(5)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Membership(membership) => Ok(membership),
        _ => Err("leader administration membership response was invalid".into()),
    }
}

/// Obtains one actual exact-member mTLS observation and rechecks current original leaf permission.
pub(crate) async fn observe_content_target(
    source: &ReplicaNode,
    member: u64,
    target: ContentQuorumTarget,
) -> Result<MemberContentObservation, String> {
    let data = target
        .membership
        .nodes()
        .find_map(|(id, data)| (*id == member).then_some(data.clone()))
        .ok_or("content observation member lacks its exact committed data")?;
    let network = ReplicaNetwork {
        source: None,
        snapshot_header: None,
        target: member,
        node: data,
        profile: source.profile_identity(),
        sender: source.config.replica_id,
        online: source.network_online.clone(),
        shared_secret: source.config.shared_secret.clone(),
        tls: source.tls.clone(),
        budget: source.transport_budget.clone(),
    };
    let (response, leaf) = network
        .exchange_observed(
            Command::ObserveContent {
                target: Box::new(target.clone()),
            },
            RPCOption::new(Duration::from_secs(15)),
            None,
        )
        .await
        .map_err(|error| error.to_string())?;
    let trust = source.network_target_authority(&target)?;
    if !leaf
        .as_ref()
        .is_some_and(|actual| trust.admits(member, actual))
    {
        return Err("actual observation peer lost its current exact leaf permission".into());
    }
    match response {
        Reply::ContentObservation(result) => result,
        Reply::Rejected(error) => Err(error),
        _ => Err("actual content observation response has another interpretation".into()),
    }
}

/// Sends one authenticated learner admission command to the current leader.
pub(crate) async fn admin_add_learner(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    epoch: ControllerEpoch,
    member_id: u64,
) -> Result<(), String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminAddLearner { epoch, member_id },
            RPCOption::new(Duration::from_secs(15)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Admin(result) => result,
        _ => Err("learner administration response was invalid".into()),
    }
}

/// Sends one authenticated joint-consensus membership mutation to the current leader.
pub(crate) async fn admin_change_membership(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    epoch: ControllerEpoch,
    voters: std::collections::BTreeSet<u64>,
    retain_removed_as_learners: bool,
) -> Result<(), String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminChangeMembership {
                epoch,
                voters,
                retain_removed_as_learners,
            },
            RPCOption::new(Duration::from_secs(15)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Admin(result) => result,
        _ => Err("membership administration response was invalid".into()),
    }
}

/// Sends one authenticated transfer request to the current leader.
pub(crate) async fn admin_request_transfer(
    config: &ReplicaConfig,
    leader: u64,
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    epoch: ControllerEpoch,
    target: u64,
    transfer_id: String,
) -> Result<LeaderTransferReceipt, String> {
    match admin_network(config, leader, tls)?
        .exchange(
            Command::AdminRequestTransfer {
                epoch,
                target,
                transfer_id,
            },
            RPCOption::new(Duration::from_secs(15)),
        )
        .await
        .map_err(|error| error.to_string())?
    {
        Reply::Transfer(result) => result,
        _ => Err("leader transfer administration response was invalid".into()),
    }
}

/// Factory establishes no connection until the upstream engine invokes an actual RPC.
pub(crate) struct ReplicaNetworkFactory {
    /// Weak actual running source, initialized once the original server owns it.
    source: Arc<std::sync::OnceLock<std::sync::Weak<ReplicaNode>>>,
    /// Startup cluster/profile identity.
    profile: String,
    /// Sending infrastructure replica.
    sender: u64,
    /// Test-controlled whole-network partition; production remains enabled.
    online: Arc<AtomicBool>,
    /// Optional shared secret copied only into the authenticated transport client.
    shared_secret: Option<String>,
    /// Optional loaded mTLS client profile for cross-host members.
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    /// Actual shared process-local network and frame capacity.
    budget: Arc<TransportBudget>,
}

impl ReplicaNetworkFactory {
    /// Freezes profile and sender; a shared switch permits deterministic partition checks.
    pub(crate) fn new(
        profile: String,
        sender: u64,
        online: Arc<AtomicBool>,
        shared_secret: Option<String>,
        tls: Option<Arc<crate::tls::TlsMaterial>>,
        budget: Arc<TransportBudget>,
        source: Arc<std::sync::OnceLock<std::sync::Weak<ReplicaNode>>>,
    ) -> Self {
        Self {
            source,
            profile,
            sender,
            online,
            shared_secret,
            tls,
            budget,
        }
    }
}

/// One exact remote member of the upstream consensus engine.
#[derive(Clone)]
pub(crate) struct ReplicaNetwork {
    /// Exact header admitted before the first chunk on this actual upstream Snapshot transfer.
    snapshot_header: Option<SnapshotContentHeader>,
    /// Weak original running source; readonly administration clients do not acquire a source scope.
    source: Option<Arc<std::sync::OnceLock<std::sync::Weak<ReplicaNode>>>>,
    /// Exact remote infrastructure identity.
    target: u64,
    /// Upstream-declared member endpoint.
    node: ReplicaMember,
    /// Shared immutable cluster identity.
    profile: String,
    /// Local sending member identity.
    sender: u64,
    /// Shared partition control, unused by the production deployment API.
    online: Arc<AtomicBool>,
    /// Optional shared transport secret.
    shared_secret: Option<String>,
    /// Optional loaded mTLS client profile for the remote member.
    tls: Option<Arc<crate::tls::TlsMaterial>>,
    /// Original node-local asynchronous exchange and frame capacity.
    budget: Arc<TransportBudget>,
}

impl RaftNetworkFactory<ControllerRaftConfig> for ReplicaNetworkFactory {
    type Network = ReplicaNetwork;

    /// Creates an inert exact-member client without aliasing robot Nodes or opening a socket.
    async fn new_client(&mut self, target: u64, node: &ReplicaMember) -> Self::Network {
        ReplicaNetwork {
            snapshot_header: None,
            source: Some(self.source.clone()),
            target,
            node: node.clone(),
            profile: self.profile.clone(),
            sender: self.sender,
            online: self.online.clone(),
            shared_secret: self.shared_secret.clone(),
            tls: self.tls.clone(),
            budget: self.budget.clone(),
        }
    }
}

impl ReplicaNetwork {
    /// Sends one bounded frame within the upstream request budget.
    async fn exchange(&self, command: Command, option: RPCOption) -> Result<Reply, std::io::Error> {
        self.exchange_observed(command, option, None)
            .await
            .map(|(reply, _)| reply)
    }

    /// Captures the actual target leaf and optionally requires the original staged replacement.
    async fn exchange_observed(
        &self,
        command: Command,
        option: RPCOption,
        required_leaf: Option<&str>,
    ) -> Result<(Reply, Option<String>), std::io::Error> {
        if !self.online.load(Ordering::Relaxed) {
            return Err(std::io::Error::other("test partition is active"));
        }
        let _exchange = self.budget.outbound()?;
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
        let encoded = crate::transport_budget::encode(&request, &self.budget)?;
        let operation = async {
            let stream = tokio::net::TcpStream::connect(&self.node.addr).await?;
            if let Some(tls) = &self.tls {
                let tls = tls.refreshed().map_err(std::io::Error::other)?;
                let connector = TlsConnector::from(tls.client.clone());
                let name = rustls::pki_types::ServerName::try_from(tls.server_name.clone())
                    .map_err(std::io::Error::other)?;
                let mut stream = connector
                    .connect(name, stream)
                    .await
                    .map_err(std::io::Error::other)?;
                tls.authenticate_peer(self.target, stream.get_ref().1.peer_certificates())
                    .map_err(std::io::Error::other)?;
                let actual_leaf = stream
                    .get_ref()
                    .1
                    .peer_certificates()
                    .and_then(|chain| chain.first())
                    .map(|leaf| format!("sha256:{:x}", Sha256::digest(leaf.as_ref())));
                if required_leaf.is_some_and(|required| actual_leaf.as_deref() != Some(required)) {
                    return Err(std::io::Error::other(
                        "target has not presented the original staged new certificate",
                    ));
                }
                stream.write_u64(encoded.bytes.len() as u64).await?;
                stream.write_all(&encoded.bytes).await?;
                stream.flush().await?;
                let reply = read_reply(&mut stream, &self.budget).await?;
                if self.node.content_store_owner.is_some() {
                    tls.authenticate_peer(self.target, stream.get_ref().1.peer_certificates())
                        .map_err(std::io::Error::other)?;
                }
                Ok((reply, actual_leaf))
            } else {
                let mut stream = stream;
                stream.write_u64(encoded.bytes.len() as u64).await?;
                stream.write_all(&encoded.bytes).await?;
                stream.flush().await?;
                if required_leaf.is_some() {
                    return Err(std::io::Error::other(
                        "certificate evidence requires actual mutual TLS",
                    ));
                }
                read_reply(&mut stream, &self.budget)
                    .await
                    .map(|reply| (reply, None))
            }
        };
        tokio::time::timeout(option.hard_ttl(), operation)
            .await
            .map_err(std::io::Error::other)?
    }
}

/// Reads one bounded framed reply from an async replica stream.
async fn read_reply<S>(stream: &mut S, budget: &TransportBudget) -> Result<Reply, std::io::Error>
where
    S: tokio::io::AsyncRead + Unpin,
{
    let length = stream.read_u64().await?;
    let _frame = budget.frame(length)?;
    let mut bytes = vec![0; length as usize];
    stream.read_exact(&mut bytes).await?;
    serde_json::from_slice(&bytes).map_err(std::io::Error::other)
}

impl RaftNetwork<ControllerRaftConfig> for ReplicaNetwork {
    /// Freezes one separate original content-operation budget before the unchanged upstream chunk transport.
    async fn full_snapshot(
        &mut self,
        vote: openraft::Vote<u64>,
        snapshot: openraft::Snapshot<ControllerRaftConfig>,
        cancel: impl std::future::Future<Output = openraft::error::ReplicationClosed>
        + openraft::OptionalSend
        + 'static,
        option: RPCOption,
    ) -> Result<
        openraft::raft::SnapshotResponse<u64>,
        openraft::error::StreamingError<ControllerRaftConfig, openraft::error::Fatal<u64>>,
    > {
        use openraft::network::snapshot_transport::{Chunked, SnapshotTransport};
        let mut cancel = Box::pin(cancel);
        let preparation = tokio::select! {
            closed=&mut cancel=>return Err(closed.into()),
            prepared=snapshot_content::prepare_with_heartbeats(self,vote,&snapshot.meta)=>prepared
                .map_err(|error|Unreachable::new(&std::io::Error::other(error)))?,
        };
        let (header, clock) = match preparation {
            snapshot_content::Preparation::Ready { header, clock } => (header, clock),
            snapshot_content::Preparation::Superseded(actual) => {
                return Ok(openraft::raft::SnapshotResponse::new(actual));
            }
        };
        self.snapshot_header = header.map(|header| *header);
        let Some(clock) = clock else {
            return Chunked::send_snapshot(self, vote, snapshot, cancel, option).await;
        };
        let heartbeat = self.clone();
        let meta = snapshot.meta.clone();
        let original_meta = meta.clone();
        let transferred = tokio::time::timeout(
            Duration::from_millis(
                clock
                    .remaining_ms()
                    .map_err(|error| Unreachable::new(&std::io::Error::other(error)))?,
            ),
            async {
                tokio::select! {
                    result=Chunked::send_snapshot(self,vote,snapshot,cancel,option)=>result,
                    superseded=snapshot_content::keep_alive(&heartbeat,vote,&meta,&clock)=>{
                        superseded.map(openraft::raft::SnapshotResponse::new)
                            .map_err(|error| Unreachable::new(&std::io::Error::other(error)).into())
                    }
                }
            },
        )
        .await;
        let outcome = match transferred {
            Ok(outcome) => outcome,
            Err(_) => Err(Unreachable::new(&std::io::Error::other(
                "original Snapshot transfer budget expired; outcome remains uncertain",
            ))
            .into()),
        };
        if outcome.is_err()
            && let Some(source) = heartbeat
                .source
                .as_ref()
                .and_then(|source| source.get())
                .and_then(std::sync::Weak::upgrade)
        {
            // Parent11 creates a new actual journaled publication; legacy profiles need a newer cut.
            // Original receive, byte readiness, Unknown and first budgets remain immutable.
            let _ = snapshot_content::refresh_snapshot(&source, &original_meta).await;
        }
        outcome
    }
    /// Forwards exact append RPCs; failures remain network/remote errors to OpenRaft.
    async fn append_entries(
        &mut self,
        rpc: AppendEntriesRequest<ControllerRaftConfig>,
        option: RPCOption,
    ) -> Result<AppendEntriesResponse<u64>, RPCError<u64, ReplicaMember, RaftError<u64>>> {
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
    ) -> Result<VoteResponse<u64>, RPCError<u64, ReplicaMember, RaftError<u64>>> {
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
        RPCError<u64, ReplicaMember, RaftError<u64, InstallSnapshotError>>,
    > {
        let command = if let Some(header) = &self.snapshot_header {
            if header.meta != rpc.meta {
                return Err(RPCError::Unreachable(Unreachable::new(
                    &std::io::Error::other("Snapshot sender lost its original prerequisite header"),
                )));
            }
            Command::SnapshotPreparedV1 {
                header: Box::new(header.clone()),
                rpc,
            }
        } else {
            Command::Snapshot(rpc)
        };
        match self
            .exchange(command, option)
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
    /// Optional loaded mTLS client profile for a cross-host replica endpoint.
    tls: Option<Arc<crate::tls::TlsMaterial>>,
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
            tls: None,
        })
    }

    /// Constructs a child fence for either loopback or a validated mTLS cross-host endpoint.
    pub fn new_with_tls(
        address: SocketAddr,
        profile: String,
        epoch: ControllerEpoch,
        tls_config: ReplicaTlsConfig,
    ) -> Result<Self, String> {
        if address.port() == 0 || profile.is_empty() || epoch.replica_id == 0 {
            return Err("invalid Controller replica child fence".into());
        }
        let tls = crate::tls::load(Some(&tls_config))?.map(Arc::new);
        if let Some(tls) = &tls {
            tls.authenticate_local(epoch.replica_id)?;
        }
        Ok(Self {
            address,
            profile,
            epoch,
            shared_secret: None,
            tls,
        })
    }

    /// Uses the original WAL-aware trust authority and atomic PEM bundle for a v0.3 owned child.
    pub fn new_with_current_trust(
        address: SocketAddr,
        profile: String,
        epoch: ControllerEpoch,
        tls_config: ReplicaTlsConfig,
        path: &Path,
    ) -> Result<Self, String> {
        if address.port() == 0 || profile.is_empty() || epoch.replica_id == 0 {
            return Err("invalid current-trust Controller child fence".into());
        }
        let tls = crate::tls::with_readonly_trust(&tls_config, path, &profile, epoch.replica_id)?;
        Ok(Self {
            address,
            profile,
            epoch,
            shared_secret: None,
            tls: Some(Arc::new(tls)),
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
            (None, None, None) => {
                if read("ROBOGUIDE_REPLICA_TRUST_DATABASE")?.is_some() {
                    return Err(
                        "current trust requires the complete child replica environment".into(),
                    );
                }
                Ok(None)
            }
            (Some(address), Some(profile), Some(epoch)) => {
                let address = address.parse().map_err(|_| "invalid replica endpoint")?;
                let epoch = serde_json::from_str(&epoch).map_err(|_| "invalid replica epoch")?;
                let tls_values = (
                    read("ROBOGUIDE_REPLICA_TLS_CA")?,
                    read("ROBOGUIDE_REPLICA_TLS_CERT")?,
                    read("ROBOGUIDE_REPLICA_TLS_KEY")?,
                    read("ROBOGUIDE_REPLICA_TLS_SERVER_NAME")?,
                    read("ROBOGUIDE_REPLICA_TLS_PEER_DIGESTS")?,
                );
                let mut client = if tls_values.0.is_some()
                    || tls_values.1.is_some()
                    || tls_values.2.is_some()
                    || tls_values.3.is_some()
                    || tls_values.4.is_some()
                {
                    let tls_config = ReplicaTlsConfig {
                        ca_cert_path: tls_values
                            .0
                            .ok_or("replica TLS child fence misses CA path")?
                            .into(),
                        cert_path: tls_values
                            .1
                            .ok_or("replica TLS child fence misses certificate path")?
                            .into(),
                        key_path: tls_values
                            .2
                            .ok_or("replica TLS child fence misses key path")?
                            .into(),
                        server_name: tls_values
                            .3
                            .ok_or("replica TLS child fence misses server name")?,
                        peer_certificate_digests: serde_json::from_str(
                            &tls_values
                                .4
                                .ok_or("replica TLS child fence misses peer digest map")?,
                        )
                        .map_err(|_| "replica TLS child fence has invalid peer digest map")?,
                    };
                    match read("ROBOGUIDE_REPLICA_TRUST_DATABASE")? {
                        Some(path) => Self::new_with_current_trust(
                            address,
                            profile,
                            epoch,
                            tls_config,
                            Path::new(&path),
                        )?,
                        None => Self::new_with_tls(address, profile, epoch, tls_config)?,
                    }
                } else {
                    if read("ROBOGUIDE_REPLICA_TRUST_DATABASE")?.is_some() {
                        return Err("current trust child requires mandatory mutual TLS".into());
                    }
                    Self::new(address, profile, epoch)?
                };
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
        let stream = std::net::TcpStream::connect_timeout(&self.address, Duration::from_secs(2))
            .map_err(|error| error.to_string())?;
        stream
            .set_read_timeout(Some(Duration::from_secs(12)))
            .map_err(|error| error.to_string())?;
        stream
            .set_write_timeout(Some(Duration::from_secs(12)))
            .map_err(|error| error.to_string())?;
        let mut stream = if let Some(tls) = &self.tls {
            let tls = tls.refreshed()?;
            let name = rustls::pki_types::ServerName::try_from(tls.server_name.clone())
                .map_err(|error| error.to_string())?;
            let mut connection = rustls::ClientConnection::new(tls.client.clone(), name)
                .map_err(|error| error.to_string())?;
            let mut stream = stream;
            while connection.is_handshaking() {
                connection
                    .complete_io(&mut stream)
                    .map_err(|error| error.to_string())?;
            }
            tls.authenticate_peer(self.epoch.replica_id, connection.peer_certificates())?;
            rustls::StreamOwned::new(connection, stream)
        } else {
            return self.exchange_raw(request, stream);
        };
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

    /// Sends one raw loopback child request while keeping the common framing logic bounded.
    fn exchange_raw(
        &self,
        request: Request,
        mut stream: std::net::TcpStream,
    ) -> Result<Reply, String> {
        let bytes = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
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
    let mut digest = Sha256::new();
    digest.update(secret.as_bytes());
    serde_json::to_writer(
        &mut AuthHashWriter(&mut digest),
        &(profile, target, sender, command),
    )
    .ok()?;
    Some(format!("{:x}", digest.finalize()))
}

/// Streams original JSON into its exact original SHA-256 without a second whole-frame allocation.
struct AuthHashWriter<'a>(&'a mut Sha256);

impl Write for AuthHashWriter<'_> {
    /// Hashes the same complete serialization bytes formerly retained in an extra Vec.
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.update(bytes);
        Ok(bytes.len())
    }

    /// Hashing retains no external buffer or I/O to flush.
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl state::ApplicationBatchReplicator for ReplicaFenceClient {
    /// Binds durable command evidence to the exact supervisor-issued leader epoch.
    fn controller_epoch(&self) -> Option<String> {
        serde_json::to_string(&self.epoch).ok()
    }
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

    /// Streaming preserves exact legacy digest bytes, including escaped and non-ASCII identities.
    #[test]
    fn streaming_authentication_matches_the_original_wire_digest() {
        let command = Command::ObserveLeaderTransfer {
            transfer_id: "原始\n\"transfer\\".into(),
        };
        let secret = "original-secret-".repeat(3);
        let original_payload = serde_json::to_vec(&("profile", 2_u64, 1_u64, &command)).unwrap();
        let mut original = Sha256::new();
        original.update(secret.as_bytes());
        original.update(original_payload);
        assert_eq!(
            request_auth_digest(Some(&secret), "profile", 2, 1, &command),
            Some(format!("{:x}", original.finalize()))
        );
    }

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
