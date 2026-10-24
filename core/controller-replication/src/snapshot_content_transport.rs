//! Durable Snapshot prerequisites on actual Raft/mTLS transports; no serialized flag grants application authority.

use super::*;
use crate::content_store::{
    ContentBudget, StreamOrigin, StreamPermission, snapshot_receive_operation,
};

/// Actual upstream preparation outcome; a higher vote is not a completed Snapshot receipt.
pub(super) enum Preparation {
    /// Original content prerequisites, or the unchanged metadata-only transport.
    Ready {
        /// Exact original prepared metadata prerequisite, absent for legacy profiles.
        header: Option<Box<SnapshotContentHeader>>,
        /// One original source clock, retained through all metadata chunks without renewal.
        clock: Option<Box<ContentBudget>>,
    },
    /// Actual authenticated Append reply for OpenRaft's original vote handling.
    Superseded(openraft::Vote<u64>),
}

/// Keeps the original upstream vote live through normal Append while bounded content work waits.
pub(super) async fn prepare_with_heartbeats(
    network: &ReplicaNetwork,
    vote: openraft::Vote<u64>,
    meta: &openraft::SnapshotMeta<u64, ReplicaMember>,
) -> Result<Preparation, String> {
    let Some(source) = network
        .source
        .as_ref()
        .and_then(|source| source.get())
        .and_then(std::sync::Weak::upgrade)
    else {
        return Ok(Preparation::Ready {
            header: None,
            clock: None,
        });
    };
    if !source.config.streams_content()
        || !source
            .store
            .network_content_store()?
            .snapshot_receives_enabled()
    {
        return Ok(Preparation::Ready {
            header: None,
            clock: None,
        });
    }
    let clock = ContentBudget::issue(15000)?;
    if let Some(higher) = heartbeat(network, &source, vote, meta, &clock).await? {
        return Ok(Preparation::Superseded(higher));
    }
    let prepared = before_send(network, meta, clock.clone());
    tokio::pin!(prepared);
    loop {
        tokio::select! {
            ready = &mut prepared => return ready.map(|header| Preparation::Ready {
                header: header.map(Box::new), clock: Some(Box::new(clock)),
            }),
            _ = tokio::time::sleep(Duration::from_millis(500)) => {
                if let Some(higher) = heartbeat(network, &source, vote, meta, &clock).await? {
                    return Ok(Preparation::Superseded(higher));
                }
            }
        }
    }
}

/// Continues ordinary vote heartbeats until metadata completion, supersession or original expiry.
pub(super) async fn keep_alive(
    network: &ReplicaNetwork,
    vote: openraft::Vote<u64>,
    meta: &openraft::SnapshotMeta<u64, ReplicaMember>,
    clock: &ContentBudget,
) -> Result<openraft::Vote<u64>, String> {
    let source = network
        .source
        .as_ref()
        .and_then(|source| source.get())
        .and_then(std::sync::Weak::upgrade)
        .ok_or("original Snapshot source process is unavailable")?;
    loop {
        clock.check()?;
        tokio::time::sleep(Duration::from_millis(500)).await;
        if let Some(higher) = heartbeat(network, &source, vote, meta, clock).await? {
            return Ok(higher);
        }
    }
}

/// Sends an exact empty Append; prefix conflict never becomes log, byte or quorum acceptance.
async fn heartbeat(
    network: &ReplicaNetwork,
    source: &ReplicaNode,
    vote: openraft::Vote<u64>,
    meta: &openraft::SnapshotMeta<u64, ReplicaMember>,
    clock: &ContentBudget,
) -> Result<Option<openraft::Vote<u64>>, String> {
    clock.check()?;
    if !vote.committed
        || source.current_epoch()
            != Some(ControllerEpoch {
                term: vote.leader_id.term,
                replica_id: vote.leader_id.node_id,
            })
    {
        return Err("original Snapshot heartbeat source vote changed".into());
    }
    source.store.require_network_snapshot_source(meta)?;
    clock.check()?;
    let response = network
        .exchange(
            Command::Append(AppendEntriesRequest {
                vote,
                prev_log_id: meta.last_log_id,
                entries: Vec::new(),
                leader_commit: meta.last_log_id,
            }),
            RPCOption::new(Duration::from_millis(clock.remaining_ms()?.min(500))),
        )
        .await
        .map_err(|error| error.to_string())?;
    clock.check()?;
    match response {
        Reply::Append(Ok(AppendEntriesResponse::HigherVote(actual))) => Ok(Some(actual)),
        Reply::Append(Ok(_)) => Ok(None),
        Reply::Append(Err(error)) => Err(error.to_string()),
        _ => Err("original Snapshot heartbeat response was rejected".into()),
    }
}

impl ReplicaNode {
    /// Publishes an actual content Snapshot and purges only its original applied cut under one bounded admin issue.
    pub async fn compact_content_snapshot(
        &self,
        duration_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let clock = ContentBudget::issue(duration_ms)?;
        if !self
            .store
            .network_content_store()?
            .snapshot_receives_enabled()
        {
            return Err("Snapshot content maintenance requires explicit Root6".into());
        }
        let epoch = self
            .current_epoch()
            .ok_or("Snapshot content maintenance requires the actual leader")?;
        self.check_epoch(epoch).await?;
        clock.check()?;
        let applied = self
            .raft
            .metrics()
            .borrow()
            .last_applied
            .ok_or("Snapshot maintenance has no original applied cut")?;
        self.raft
            .trigger()
            .snapshot()
            .await
            .map_err(|e| e.to_string())?;
        self.raft
            .wait(Some(Duration::from_millis(clock.remaining_ms()?)))
            .snapshot(applied, "original content Snapshot")
            .await
            .map_err(|e| e.to_string())?;
        clock.check()?;
        self.check_epoch(epoch).await?;
        self.raft
            .trigger()
            .purge_log(applied.index)
            .await
            .map_err(|e| e.to_string())?;
        self.raft
            .wait(Some(Duration::from_millis(clock.remaining_ms()?)))
            .purged(Some(applied), "original content Log purge")
            .await
            .map_err(|e| e.to_string())?;
        clock.check()?;
        self.check_epoch(epoch).await?;
        Ok(
            serde_json::json!({"original_epoch":epoch,"original_applied":applied,"actual_snapshot":self.raft.metrics().borrow().snapshot,
            "actual_purged":self.raft.metrics().borrow().purged}),
        )
    }
}

/// Fixes each original input stream independently of retries, certificate rotation and elections.
fn stream_operation(
    header: &SnapshotContentHeader,
    receiver: u64,
    content: &ArtifactContentKey,
) -> Result<String, String> {
    Ok(format!(
        "snapshot-content:{:x}",
        Sha256::digest(
            serde_json::to_vec(&(snapshot_receive_operation(header, receiver)?, content))
                .map_err(|e| e.to_string())?
        )
    ))
}

/// Checks receiving retention context against actual local Raft and TLS; installation remains OpenRaft's decision.
/// A new learner may have no applied membership yet; this grants bounded local retention, never a vote or Commit.
fn receiver_permission(
    node: ReplicaNode,
    source: u64,
    header: SnapshotContentHeader,
    evidence: crate::tls::VerifiedTlsConnection,
) -> StreamPermission {
    Arc::new(move || {
        if source != header.source_member
            || header.target.epoch.replica_id != source
            || evidence.peer_digest().as_ref() != Some(&header.source_leaf)
        {
            return Err("Snapshot receiving source differs from original leader/TLS".into());
        }
        let metrics = node.raft.metrics().borrow().clone();
        if metrics.current_term > header.target.epoch.term
            || (metrics.current_term == header.target.epoch.term
                && metrics
                    .current_leader
                    .is_some_and(|leader| leader != source))
        {
            return Err("Snapshot receiving original leader epoch is superseded".into());
        }
        let trust = node.store.connection_certificate_trust()?;
        evidence.authorize_with_trust(source, node.config.replica_id, &trust)
    })
}

/// Admits the complete original receiver issue before opening any new source payload.
pub(super) async fn prepare(
    node: &ReplicaNode,
    source: u64,
    header: SnapshotContentHeader,
    budget: ContentBudget,
    evidence: crate::tls::VerifiedTlsConnection,
) -> Result<SnapshotContentStatus, String> {
    let permission = receiver_permission(node.clone(), source, header.clone(), evidence);
    permission()?;
    log_content::on_worker(node, move |current| {
        current
            .store
            .network_content_store()?
            .prepare_snapshot_receive(header, budget, permission)
    })
    .await
}

/// Verifies actual complete bytes under the first original issue; retries return the first result only.
pub(super) async fn complete(
    node: &ReplicaNode,
    source: u64,
    header: SnapshotContentHeader,
    evidence: crate::tls::VerifiedTlsConnection,
) -> Result<SnapshotContentStatus, String> {
    let command = snapshot_receive_operation(&header, node.config.replica_id)?;
    let permission = receiver_permission(node.clone(), source, header.clone(), evidence);
    permission()?;
    log_content::on_worker(node, move |current| {
        let root = current.store.network_content_store()?;
        root.snapshot_receive_scope(&command, &header)?;
        root.complete_snapshot_receive(&command, permission)
    })
    .await
}

/// Receives only an exact prepared original input, with its independent stream and Snapshot clocks.
#[allow(clippy::too_many_arguments)]
pub(super) async fn receive<S>(
    stream: &mut S,
    node: &ReplicaNode,
    source: u64,
    header: SnapshotContentHeader,
    content: ArtifactContentKey,
    command_id: String,
    budget: ContentBudget,
    evidence: crate::tls::VerifiedTlsConnection,
) -> Result<(), String>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let command = snapshot_receive_operation(&header, node.config.replica_id)?;
    if stream_operation(&header, node.config.replica_id, &content)? != command_id
        || header.target.inputs.binary_search(&content).is_err()
    {
        return Err("Snapshot byte stream changed its original issue/input".into());
    }
    let root = node.store.network_content_store()?;
    let retained_header = header.clone();
    let original_clock = log_content::on_worker(node, move |current| {
        current
            .store
            .network_content_store()?
            .snapshot_receive_scope(&command, &retained_header)
    })
    .await?;
    let permission = receiver_permission(node.clone(), source, header.clone(), evidence);
    let check: StreamPermission = Arc::new(move || {
        original_clock.check()?;
        permission()
    });
    let origin = StreamOrigin::Replica {
        member: source,
        store_owner: header.source_store_owner.clone(),
        peer_leaf: header.source_leaf.clone(),
    };
    let _root = root;
    receive_owned_stream(
        stream,
        node,
        StreamReceive {
            target: header.target,
            content,
            command_id,
            budget,
            origin,
            check,
        },
    )
    .await?;
    Ok(())
}

/// Negotiates and completes original byte prerequisites before sending the first upstream metadata chunk.
async fn before_send(
    network: &ReplicaNetwork,
    meta: &openraft::SnapshotMeta<u64, ReplicaMember>,
    clock: ContentBudget,
) -> Result<Option<SnapshotContentHeader>, String> {
    let Some(source) = network
        .source
        .as_ref()
        .and_then(|source| source.get())
        .and_then(std::sync::Weak::upgrade)
    else {
        return Ok(None);
    };
    if !source.config.streams_content()
        || !source
            .store
            .network_content_store()?
            .snapshot_receives_enabled()
    {
        return Ok(None);
    }
    let observed = network
        .exchange(
            Command::SnapshotContentContractV1,
            RPCOption::new(Duration::from_millis(clock.remaining_ms()?)),
        )
        .await
        .map_err(|e| e.to_string())?;
    if !matches!(observed, Reply::SnapshotContentContract(Ok(true))) {
        return Err("Snapshot peer lacks explicit Root6 prerequisites".into());
    }
    let expected = meta.clone();
    let captured = log_content::on_worker(&source, move |current| {
        current.store.network_snapshot_source(&expected)
    })
    .await?;
    let Some((view, payload_digest, scope)) = captured else {
        return Ok(None);
    };
    let retained = Arc::new(scope);
    let content = view
        .content
        .ok_or("original Snapshot content identity is absent")?;
    let (_, membership) = source.store.try_network_authority()?;
    let metrics = source.raft.metrics().borrow().clone();
    let epoch = ControllerEpoch {
        term: metrics.current_term,
        replica_id: metrics
            .current_leader
            .ok_or("Snapshot source has no actual leader")?,
    };
    if epoch.replica_id != source.config.replica_id {
        return Err("Snapshot source is no longer actual leader".into());
    }
    let member_stores = membership
        .voter_ids()
        .map(|id| {
            membership
                .nodes()
                .find_map(|(member, data)| {
                    (*member == id)
                        .then_some(data.content_store_owner.clone())
                        .flatten()
                })
                .map(|owner| (id, owner))
                .ok_or("Snapshot voter lacks its original Root".to_string())
        })
        .collect::<Result<BTreeMap<_, _>, _>>()?;
    let target = ContentQuorumTarget {
        profile_identity: source.profile_identity(),
        epoch,
        revision: view.revision,
        checkpoint_digest: state::checkpoint_identity(
            view.checkpoint
                .as_ref()
                .ok_or("Snapshot checkpoint is absent")?,
        ),
        batch_digest: content.batch_digest.clone(),
        membership,
        member_stores,
        inputs: content.inputs.clone(),
    };
    source.network_target_authority(&target)?;
    let header = SnapshotContentHeader {
        schema: "roboguide.snapshot-content-header/v0.1".into(),
        source_member: source.config.replica_id,
        source_store_owner: source
            .store
            .network_content_store()?
            .identity()
            .store_owner
            .clone(),
        source_leaf: source
            .tls
            .as_ref()
            .ok_or("Snapshot requires actual mutual TLS")?
            .refreshed()?
            .local_certificate_digest
            .clone(),
        meta: meta.clone(),
        payload_digest,
        target,
    };
    let response = network
        .exchange(
            Command::PrepareSnapshotContentV1 {
                header: Box::new(header.clone()),
                budget_ms: clock.remaining_ms()?,
            },
            RPCOption::new(Duration::from_millis(clock.remaining_ms()?)),
        )
        .await
        .map_err(|e| e.to_string())?;
    let original = match response {
        Reply::SnapshotContent(result) => result?,
        _ => return Err("Snapshot prerequisite issue was rejected".into()),
    };
    check_status(&original, &header, network.target)?;
    if original.ready.is_none() {
        let held = source.as_ref().clone();
        let target = header.target.clone();
        let meta = header.meta.clone();
        let check: StreamPermission = Arc::new(move || {
            let _scope = &retained;
            held.network_target_authority(&target)?;
            held.store.require_network_snapshot_source(&meta)
        });
        for key in &header.target.inputs {
            clock.check()?;
            let result = content_streams::send_stream(
                &source,
                network.target,
                header.target.clone(),
                key.clone(),
                stream_operation(&header, network.target, key)?,
                clock.clone(),
                check.clone(),
                StreamRoute::Snapshot(Arc::new(header.clone())),
            )
            .await?;
            if result.receipt.is_none() {
                return Err("original Snapshot input stream remains Unknown".into());
            }
        }
        let result = network
            .exchange(
                Command::CompleteSnapshotContentV1 {
                    header: Box::new(header.clone()),
                },
                RPCOption::new(Duration::from_millis(clock.remaining_ms()?)),
            )
            .await
            .map_err(|e| e.to_string())?;
        let completed = match result {
            Reply::SnapshotContent(result) => result?,
            _ => return Err("Snapshot byte verification was rejected".into()),
        };
        check_status(&completed, &header, network.target)?;
        if completed.ready.is_none() {
            return Err("original Snapshot byte readiness remains Unknown".into());
        }
    }
    clock.check()?;
    source.network_target_authority(&header.target)?;
    Ok(Some(header))
}

/// Gates each original metadata chunk on the exact prepared issue, actual current TLS and byte-ready evidence.
pub(super) async fn check_delivery(
    node: &ReplicaNode,
    source: u64,
    header: SnapshotContentHeader,
    rpc: &InstallSnapshotRequest<ControllerRaftConfig>,
    evidence: crate::tls::VerifiedTlsConnection,
) -> Result<bool, String> {
    if header.meta != rpc.meta
        || !rpc.vote.committed
        || rpc.vote.leader_id.term != header.target.epoch.term
        || rpc.vote.leader_id.node_id != source
    {
        return Err("Snapshot metadata chunk changed its original prerequisite identity".into());
    }
    let permission = receiver_permission(node.clone(), source, header.clone(), evidence);
    permission()?;
    let command = snapshot_receive_operation(&header, node.config.replica_id)?;
    log_content::on_worker(node, move |current| {
        let root = current.store.network_content_store()?;
        let original = root
            .snapshot_receive_status(&command)?
            .ok_or("original Snapshot receive is absent")?;
        if original.ready.is_none() {
            return Err("Snapshot content has no original complete byte readiness".into());
        }
        permission()?;
        if original.retirement.is_some() {
            current
                .store
                .require_network_snapshot_source(&header.meta)?;
            return Ok(true);
        }
        root.snapshot_receive_scope(&command, &header)?;
        Ok(false)
    })
    .await
}

/// Binds observed original evidence to the exact source/header/receiver, without interpreting it as application success.
fn check_status(
    status: &SnapshotContentStatus,
    header: &SnapshotContentHeader,
    receiver: u64,
) -> Result<(), String> {
    if status.schema != "roboguide.snapshot-content-status/v0.1"
        || status.command_id != snapshot_receive_operation(header, receiver)?
        || status
            .original
            .get("intent")
            .and_then(|intent| intent.get("header"))
            != Some(&serde_json::to_value(header).map_err(|e| e.to_string())?)
    {
        return Err("Snapshot prerequisite response changed its original receiving scope".into());
    }
    Ok(())
}
