//! Private actual Unix/mTLS owned-child ingress, preserving first budgets and original worker scopes.

use super::*;
use crate::content_store::StreamOrigin;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Closed child envelope; the actual PID and certificate always come from the accepted connection.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ChildRequest {
    /// Explicit private original-child contract.
    pub(super) schema: String,
    /// Complete startup-frozen original application profile.
    pub(super) profile: String,
    /// Original issuing epoch, never refreshed within an original child.
    pub(super) epoch: ControllerEpoch,
    /// Original pre-spawn operation, independently checked against actual peer PID.
    pub(super) instance: String,
    /// Explicit bounded original operation duration, including the parent-writer queue.
    pub(super) budget_ms: u64,
    /// Closed requested operation; metadata parsing creates no effect permission.
    pub(super) command: ChildCommand,
}

/// External enum tagging preserves exact nested u64 member maps without compatibility normalization.
#[derive(Serialize, Deserialize)]
pub(super) enum ChildCommand {
    /// Original process/TLS/epoch and actual infrastructure quorum only, without a content-ready claim.
    ProcessFence,
    /// Current original-child authority and complete accepted content fence.
    Fence,
    /// Complete original previous view for constructing a semantic candidate under the real source writer.
    View,
    /// Exact first command, admitted before reading source bytes.
    Begin {
        /// Stable original command identity.
        command_id: String,
        /// Complete original current-writer candidate.
        candidate: Box<ContentApplicationBatch>,
    },
    /// Original immutable evidence only; no source or Commit is repeated.
    Observe {
        /// Exact original command identity.
        command_id: String,
    },
}

/// Private responses preserve the difference between first intent and complete application acceptance.
#[derive(Serialize, Deserialize)]
pub(super) enum ChildReply {
    /// Actual original process under a current complete quorum fence.
    Fence(ControllerProcessIdentity),
    /// Exact current committed original semantic/content projection.
    View(Box<ReplicaView>),
    /// Original complete intent is durable; this is not a content or application ACK.
    Ready {
        /// Exact original persisted operation digest.
        request_digest: String,
        /// Original complete target which subsequent source streams must match.
        target: Box<ContentQuorumTarget>,
        /// Actual original registered process matched with the accepted Unix peer.
        process: ControllerProcessIdentity,
    },
    /// Exact first command evidence; an absent receipt remains Unknown.
    Original(Box<ControllerIngressStatus>),
    /// Bounded readonly absence of an original command, never permission to retry effects.
    Absent,
    /// Honest refusal; no success or physical result is inferred.
    Rejected(String),
}

/// Actual owned private listener; dropping it never invents original child or worker exit evidence.
pub struct ControllerIngressServer {
    /// Owned accept task and its JoinSet of actual bounded live handlers.
    task: tokio::task::JoinHandle<()>,
    /// Exact original socket path, removed only if its inode still belongs to this listener.
    path: PathBuf,
    /// Actual original socket filesystem identity, independent of hardware identity.
    identity: (u64, u64),
}

impl Drop for ControllerIngressServer {
    /// Stops the actual accept/handler tasks while independent blocking workers keep their own leases.
    fn drop(&mut self) {
        self.task.abort();
        if std::fs::symlink_metadata(&self.path)
            .is_ok_and(|meta| (meta.dev(), meta.ino()) == self.identity)
        {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// Moves every actual SQL/proc/large-data job into bounded owned work before queuing it.
async fn blocking<T: Send + 'static>(
    node: &ReplicaNode,
    work: impl FnOnce(SqliteRaftStore) -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let root = node.store.network_content_store()?;
    let capacity = root.stream_work_permit()?;
    let store = node.store.clone();
    tokio::task::spawn_blocking(move || {
        let _root = root;
        let _capacity = capacity;
        work(store)
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Writes only one bounded framed value while retaining its actual original encoded-byte capacity.
pub(super) async fn write<S: tokio::io::AsyncWrite + Unpin>(
    stream: &mut S,
    value: &impl Serialize,
    node: &ReplicaNode,
) -> Result<(), String> {
    let encoded = crate::transport_budget::encode(value, &node.transport_budget)
        .map_err(|error| error.to_string())?;
    stream
        .write_u64(encoded.bytes.len() as u64)
        .await
        .map_err(|error| error.to_string())?;
    stream
        .write_all(&encoded.bytes)
        .await
        .map_err(|error| error.to_string())
}

/// Checks the actual current original process/TLS witness on an owned worker before returning it.
async fn peer_process(
    node: &ReplicaNode,
    peer: ControllerPeer,
) -> Result<ControllerProcessIdentity, String> {
    let original = node.clone();
    blocking(node, move |_| original.require_controller_peer(&peer)).await
}

/// Holds actual captured original source checks, original budget and optional complete target through I/O.
fn permission(
    node: ReplicaNode,
    peer: ControllerPeer,
    clock: ContentBudget,
    target: Option<ContentQuorumTarget>,
) -> StreamPermission {
    Arc::new(move || {
        clock.check()?;
        node.require_controller_peer(&peer)?;
        if let Some(target) = &target {
            node.network_target_authority(target)?;
        }
        clock.check()?;
        Ok(())
    })
}

/// Requires current consensus and the same actual accepted metadata/full-byte majority before authority.
async fn fence(node: &ReplicaNode, peer: &ControllerPeer) -> Result<(), String> {
    node.check_epoch(peer.epoch).await?;
    peer_process(node, peer.clone()).await?;
    let view = node.store.network_view().await?;
    if view.checkpoint.is_some() {
        let target = node.accepted_content_target().await?;
        node.content_quorum(target).await?;
    }
    peer_process(node, peer.clone()).await?;
    node.check_epoch(peer.epoch).await
}

/// Executes one exact original candidate; duplicate original IDs cannot reach Ready or source opening.
async fn begin<S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin>(
    stream: &mut S,
    node: &ReplicaNode,
    peer: ControllerPeer,
    command: String,
    candidate: Box<ContentApplicationBatch>,
    clock: ContentBudget,
) -> Result<ControllerIngressStatus, String> {
    let _writer = node.member_writer.lock().await;
    let process = peer_process(node, peer.clone()).await?;
    let peer_leaf = peer
        .connection
        .peer_digest()
        .ok_or("actual original child leaf absent")?;
    let original_command = command.clone();
    let previous = blocking(node, move |store| {
        let connection = store.connection.lock().map_err(|error| error.to_string())?;
        require_marker(&connection)?;
        journal::record(&connection, &original_command)
    })
    .await?;
    if let Some(previous) = previous {
        if previous.intent.process != process
            || previous.intent.peer_leaf != peer_leaf
            || previous.intent.candidate != candidate
            || previous.intent.budget.duration_ms != clock.duration_ms
        {
            return Err(
                "original child command differs from its immutable first input/permission".into(),
            );
        }
        let observed = status(&previous)?;
        write(
            stream,
            &ChildReply::Original(Box::new(observed.clone())),
            node,
        )
        .await?;
        return Ok(observed);
    }
    clock.check()?;
    node.check_epoch(peer.epoch).await?;
    let target = node.content_target_for(&candidate).await?;
    let check = permission(
        node.clone(),
        peer.clone(),
        clock.clone(),
        Some(target.clone()),
    );
    let intent = IngressIntent {
        schema: JOURNAL_SCHEMA.into(),
        command_id: command.clone(),
        process: process.clone(),
        peer_leaf: peer_leaf.clone(),
        candidate,
        target: target.clone(),
        budget: clock.clone(),
    };
    let guard = check.clone();
    let admission = blocking(node, move |store| journal::admit(&store, intent, guard)).await?;
    let original = match admission {
        IngressAdmission::Existing(observed) => {
            write(stream, &ChildReply::Original(observed.clone()), node).await?;
            return Ok(*observed);
        }
        IngressAdmission::New(original) => original,
    };
    let ready = ChildReply::Ready {
        request_digest: original.request_digest.clone(),
        target: Box::new(target.clone()),
        process: process.clone(),
    };
    peer_process(node, peer.clone()).await?;
    write(stream, &ready, node).await?;
    for input in &target.inputs {
        if input.source_owner != original.intent.candidate.source_writer.owner {
            continue;
        }
        let receive_id = format!(
            "controller:{:x}",
            Sha256::digest(
                serde_json::to_vec(&(&command, &target, input))
                    .map_err(|error| error.to_string())?
            )
        );
        let origin = StreamOrigin::Controller {
            process: process.clone(),
            writer: original.intent.candidate.source_writer.clone(),
            peer_leaf: peer_leaf.clone(),
        };
        let received = crate::transport::receive_owned_stream(
            stream,
            node,
            crate::transport::StreamReceive {
                target: target.clone(),
                content: input.clone(),
                command_id: receive_id,
                budget: clock.clone(),
                origin,
                check: check.clone(),
            },
        )
        .await?;
        if received.receipt.is_none() {
            return Err(
                "original child input remains Unknown; an original command cannot repeat it".into(),
            );
        }
    }
    let held = node.clone();
    let witness = check.clone();
    blocking(node, move |_| {
        witness()?;
        Ok(())
    })
    .await?;
    let (receipt, complete_members) = held
        .commit_content_checked(
            *original.intent.candidate.clone(),
            clock.clone(),
            check.clone(),
        )
        .await?;
    let completed_ms = clock.check()?;
    let receipt = IngressReceipt {
        commit: receipt,
        complete_members,
        completed_ms,
    };
    let observed = blocking(node, move |store| {
        journal::complete(&store, *original, receipt, check)
    })
    .await?;
    write(
        stream,
        &ChildReply::Original(Box::new(observed.clone())),
        node,
    )
    .await?;
    Ok(observed)
}

/// Authenticates one actual accepted Unix peer and keeps captured chain identity through all phases.
async fn handle(stream: tokio::net::UnixStream, node: ReplicaNode, pid: u32) -> Result<(), String> {
    let tls = node
        .tls
        .as_ref()
        .ok_or("original child ingress requires actual mTLS")?
        .refreshed()?;
    let mut stream = tokio::time::timeout(
        Duration::from_secs(15),
        tokio_rustls::TlsAcceptor::from(tls.server.clone()).accept(stream),
    )
    .await
    .map_err(|_| "original child TLS handshake deadline expired")?
    .map_err(|error| error.to_string())?;
    let peer_chain = stream
        .get_ref()
        .1
        .peer_certificates()
        .ok_or("actual original child chain absent")?
        .iter()
        .map(|certificate| certificate.clone().into_owned())
        .collect();
    let connection = crate::tls::VerifiedTlsConnection {
        local: tls,
        peer_chain,
    };
    let (bytes, _frame) = tokio::time::timeout(Duration::from_secs(15), async {
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
        Ok::<_, String>((bytes, frame))
    })
    .await
    .map_err(|_| "original child first frame deadline expired")??;
    let request: ChildRequest =
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    drop(bytes);
    let result = tokio::time::timeout(
        Duration::from_millis(request.budget_ms.min(300000)),
        async {
            if request.schema != JOURNAL_SCHEMA || request.profile != node.profile_identity() {
                return Err("original child profile/contract differs".into());
            }
            let peer = ControllerPeer {
                pid,
                instance: request.instance,
                epoch: request.epoch,
                connection,
            };
            let clock = ContentBudget::issue(request.budget_ms)?;
            peer_process(&node, peer.clone()).await?;
            match request.command {
                ChildCommand::ProcessFence => {
                    node.check_epoch(peer.epoch).await?;
                    let process = peer_process(&node, peer).await?;
                    write(&mut stream, &ChildReply::Fence(process), &node).await
                }
                ChildCommand::Fence => {
                    fence(&node, &peer).await?;
                    let process = peer_process(&node, peer).await?;
                    write(&mut stream, &ChildReply::Fence(process), &node).await
                }
                ChildCommand::View => {
                    fence(&node, &peer).await?;
                    let view = node.store.network_view().await?;
                    peer_process(&node, peer).await?;
                    write(&mut stream, &ChildReply::View(Box::new(view)), &node).await
                }
                ChildCommand::Observe { command_id } => {
                    let held = node.clone();
                    let observed =
                        blocking(&node, move |_| held.controller_ingress_status(&command_id))
                            .await?;
                    peer_process(&node, peer).await?;
                    let reply = observed.map_or(ChildReply::Absent, |value| {
                        ChildReply::Original(Box::new(value))
                    });
                    write(&mut stream, &reply, &node).await
                }
                ChildCommand::Begin {
                    command_id,
                    candidate,
                    ..
                } => begin(&mut stream, &node, peer, command_id, candidate, clock)
                    .await
                    .map(|_| ()),
            }
        },
    )
    .await
    .unwrap_or_else(|_| Err("original child complete operation deadline expired".into()));
    if let Err(error) = &result {
        let bounded = error.chars().take(1024).collect();
        eprintln!("original Controller ingress refused for actual peer {pid}: {bounded}");
        let _ = write(&mut stream, &ChildReply::Rejected(bounded), &node).await;
    }
    result
}

impl ReplicaNode {
    /// Binds only a private owned-directory Unix endpoint after explicit parent8 and original supervision.
    pub async fn serve_controller_ingress(
        &self,
        path: &Path,
    ) -> Result<ControllerIngressServer, String> {
        if !self.store.controller_content || !path.is_absolute() || path.as_os_str().len() > 103 {
            return Err(
                "original child ingress requires parent8 and a bounded absolute Unix path".into(),
            );
        }
        let parent = path
            .parent()
            .ok_or("original Unix endpoint parent absent")?;
        let directory = std::fs::symlink_metadata(parent).map_err(|error| error.to_string())?;
        let uid = std::fs::metadata("/proc/self")
            .map_err(|error| error.to_string())?
            .uid();
        if !directory.is_dir()
            || directory.uid() != uid
            || directory.mode() & 0o077 != 0
            || parent.canonicalize().map_err(|error| error.to_string())? != parent
        {
            return Err(
                "original child endpoint requires an actual same-user canonical private directory"
                    .into(),
            );
        }
        match std::fs::symlink_metadata(path) {
            Ok(previous) => {
                if !previous.file_type().is_socket()
                    || previous.uid() != uid
                    || previous.mode() & 0o777 != 0o600
                {
                    return Err(
                        "original child endpoint path is not an owned private socket".into(),
                    );
                }
                let held = self.clone();
                blocking(self, move |store| {
                    let connection = store.connection.lock().map_err(|error| error.to_string())?;
                    validate_storage(&connection, true)?;
                    drop(connection);
                    held.store.require_original_endpoint_reopen()
                })
                .await?;
                match tokio::time::timeout(
                    Duration::from_secs(2),
                    tokio::net::UnixStream::connect(path),
                )
                .await
                {
                    Ok(Err(error)) if error.kind() == std::io::ErrorKind::ConnectionRefused => {}
                    _ => {
                        return Err(
                            "original child endpoint still has a listener or uncertain connection"
                                .into(),
                        );
                    }
                }
                let actual = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
                if !actual.file_type().is_socket()
                    || (actual.dev(), actual.ino()) != (previous.dev(), previous.ino())
                {
                    return Err(
                        "original child endpoint inode changed during reopen observation".into(),
                    );
                }
                std::fs::remove_file(path).map_err(|error| error.to_string())?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.to_string()),
        }
        let listener = tokio::net::UnixListener::bind(path).map_err(|error| error.to_string())?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
            .map_err(|error| error.to_string())?;
        let actual = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
        let identity = (actual.dev(), actual.ino());
        let node = self.clone();
        let task = tokio::spawn(async move {
            let mut handlers = tokio::task::JoinSet::new();
            loop {
                let accepted = tokio::select! {
                    completed=handlers.join_next(),if !handlers.is_empty()=>{let _=completed;continue;}
                    accepted=listener.accept()=>accepted,
                };
                let Ok((stream, _)) = accepted else {
                    return;
                };
                let Ok(credentials) = stream.peer_cred() else {
                    continue;
                };
                let Some(pid) = credentials.pid().and_then(|pid| u32::try_from(pid).ok()) else {
                    continue;
                };
                if credentials.uid() != uid {
                    continue;
                }
                let Ok(permit) = node.transport_budget.inbound() else {
                    continue;
                };
                let held = node.clone();
                handlers.spawn(async move {
                    let _permit = permit;
                    let budget = held.transport_budget.clone();
                    if tokio::time::timeout(Duration::from_secs(300), handle(stream, held, pid))
                        .await
                        .is_err()
                    {
                        budget.timed_out();
                    }
                });
            }
        });
        Ok(ControllerIngressServer {
            task,
            path: path.into(),
            identity,
        })
    }
}
