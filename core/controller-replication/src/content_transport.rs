//! Real mutual-TLS content streams with original owned worker lifetimes and bounded byte queues.

use super::*;
use crate::content_store::{
    ContentBudget, STREAM_CHUNK_BYTES, StreamAdmission, StreamOrigin, StreamPermission,
};

/// Readiness is a durable receive intent only; duplicate outcomes never create a second writer.
#[derive(Serialize, Deserialize)]
pub(crate) enum StreamNotice {
    /// Exact first admitted durable request, before any source payload is opened.
    Ready {
        /// Immutable complete receiver intent digest, never a quorum or byte receipt.
        request_digest: String,
    },
    /// Original retained result, including Unknown, without another payload transfer.
    Original(Box<ReplicaContentStreamStatus>),
}

/// Actual original receive context supplied only by a trusted live member or owned-child adapter.
pub(crate) struct StreamReceive {
    /// Complete immutable original metadata/membership/input target.
    pub(crate) target: ContentQuorumTarget,
    /// One exact original qualified byte input.
    pub(crate) content: ArtifactContentKey,
    /// Original immutable receive identity.
    pub(crate) command_id: String,
    /// Actual original receiver kernel budget sampled before queuing.
    pub(crate) budget: ContentBudget,
    /// Actual original source identity independently observed by its trusted adapter.
    pub(crate) origin: StreamOrigin,
    /// Nonserialized current captured source/epoch/TLS/permission checks.
    pub(crate) check: StreamPermission,
}

/// Each bounded message belongs to one unique original receive worker.
enum ReceiveChunk {
    /// One exact next bounded byte segment.
    Bytes {
        /// Original expected byte position.
        offset: u64,
        /// At most 64 KiB, checked before allocation on the actual network boundary.
        data: Vec<u8>,
    },
    /// End of this actual stream, never permission to publish incomplete bytes.
    Finish,
}

/// Bounded actual source observations; local read closure and remote publication stay separate facts.
enum SourceChunk {
    /// One actual bounded file read from the original managed source.
    Bytes(Vec<u8>),
    /// Actual original source reader result, never a receiver byte or quorum receipt.
    Closed(Result<(), String>),
}

/// Nonserializable original source ownership moved intact into its actual blocking worker.
struct SourceWork {
    /// Actual original managed root and its held writer leases.
    root: ReplicaContentStore,
    /// Actual pre-queue capacity, retained until original source FD/SQL work ends.
    capacity: tokio::sync::OwnedSemaphorePermit,
    /// Exact immutable original target including both actual source/receiver member data.
    target: ContentQuorumTarget,
    /// Full original qualified bytes, independent of the receiver root.
    content: ArtifactContentKey,
    /// Exact original receive operation, separately bound to its actual target member.
    command_id: String,
    /// Actual target on the authenticated original connection.
    receiver: u64,
    /// Original local clock sampled before any queue/network wait.
    clock: ContentBudget,
    /// Actual captured connection/current authority, never a serializable success flag.
    check: StreamPermission,
}

/// Moves the actual reader/root/queue permit into one owned worker only after receiver Ready.
fn source_worker(work: SourceWork) -> tokio::sync::mpsc::Receiver<SourceChunk> {
    let (send, receive) = tokio::sync::mpsc::channel(2);
    let _worker = tokio::task::spawn_blocking(move || {
        let SourceWork {
            root,
            capacity,
            target,
            content,
            command_id,
            receiver,
            clock,
            check,
        } = work;
        let _capacity = capacity;
        let result = (|| -> Result<(), String> {
            clock.check()?;
            check()?;
            let original = serde_json::to_vec(&(receiver, &target, &content, &command_id))
                .map_err(|error| error.to_string())?;
            let reader_id = format!("stream-source:{:x}", Sha256::digest(original));
            let live_check = check.clone();
            let source_clock = clock.clone();
            let opening: StreamPermission = Arc::new(move || {
                source_clock.check()?;
                live_check()
            });
            let mut source = root.open_reader_checked(
                &content,
                &reader_id,
                &target.batch_digest,
                clock.duration_ms,
                opening,
            )?;
            loop {
                clock.check()?;
                check()?;
                let mut data = vec![0; STREAM_CHUNK_BYTES];
                let length = source.read(&mut data).map_err(|error| error.to_string())?;
                clock.check()?;
                check()?;
                if length == 0 {
                    break;
                }
                data.truncate(length);
                send.blocking_send(SourceChunk::Bytes(data))
                    .map_err(|_| "original source connection no longer receives bytes")?;
            }
            let closed = source.finish()?;
            clock.check()?;
            check()?;
            if !closed.complete {
                return Err("original source reader did not verify complete bytes".into());
            }
            Ok(())
        })();
        let _ = send.blocking_send(SourceChunk::Closed(result));
    });
    receive
}

/// Sends one real mTLS stream under original local/receiver budgets; duplicate IDs never open a source.
pub(crate) async fn send_content_stream(
    node: &ReplicaNode,
    receiver: u64,
    target: ContentQuorumTarget,
    content: ArtifactContentKey,
    command_id: String,
    duration_ms: u64,
) -> Result<ReplicaContentStreamStatus, String> {
    let clock = ContentBudget::issue(duration_ms)?;
    send_content_stream_checked(
        node,
        receiver,
        target,
        content,
        command_id,
        clock,
        Arc::new(|| Ok(())),
    )
    .await
}

/// Carries the original admitted caller clock and live witness through every actual source chunk.
pub(crate) async fn send_content_stream_checked(
    node: &ReplicaNode,
    receiver: u64,
    target: ContentQuorumTarget,
    content: ArtifactContentKey,
    command_id: String,
    clock: ContentBudget,
    caller: StreamPermission,
) -> Result<ReplicaContentStreamStatus, String> {
    clock.check()?;
    caller()?;
    let duration_ms = clock.remaining_ms()?.min(15000);
    let _exchange = node
        .transport_budget
        .outbound()
        .map_err(|error| error.to_string())?;
    let root = node.store.network_content_store()?;
    let capacity = root.stream_work_permit()?;
    let address = target
        .membership
        .nodes()
        .find_map(|(id, member)| (*id == receiver).then_some(member.addr.clone()))
        .ok_or("original receiver is absent from the exact committed membership")?;
    let command = Command::PushContent {
        target: Box::new(target.clone()),
        content: content.clone(),
        command_id: command_id.clone(),
        budget_ms: duration_ms,
    };
    let request = Request {
        profile: node.profile_identity(),
        target: receiver,
        sender: node.config.replica_id,
        auth_digest: request_auth_digest(
            node.config.shared_secret.as_deref(),
            &node.profile_identity(),
            receiver,
            node.config.replica_id,
            &command,
        ),
        command,
    };
    let encoded = crate::transport_budget::encode(&request, &node.transport_budget)
        .map_err(|error| error.to_string())?;
    let expected = (target.clone(), content.clone(), command_id.clone());
    let operation = async {
        clock.check()?;
        let tls = node
            .tls
            .as_ref()
            .ok_or("content stream requires actual mTLS")?
            .refreshed()?;
        let socket = tokio::net::TcpStream::connect(address)
            .await
            .map_err(|error| error.to_string())?;
        let name = rustls::pki_types::ServerName::try_from(tls.server_name.clone())
            .map_err(|error| error.to_string())?;
        let mut stream = TlsConnector::from(tls.client.clone())
            .connect(name, socket)
            .await
            .map_err(|error| error.to_string())?;
        tls.authenticate_peer(receiver, stream.get_ref().1.peer_certificates())?;
        let evidence = crate::tls::VerifiedTlsConnection {
            peer_chain: stream
                .get_ref()
                .1
                .peer_certificates()
                .ok_or("actual content receiver certificate is absent")?
                .to_vec(),
            local: tls,
        };
        let network = permission(node.clone(), target.clone(), receiver, evidence);
        let check: StreamPermission = Arc::new(move || {
            caller()?;
            network()
        });
        check()?;
        clock.check()?;
        stream
            .write_u64(encoded.bytes.len() as u64)
            .await
            .map_err(|error| error.to_string())?;
        stream
            .write_all(&encoded.bytes)
            .await
            .map_err(|error| error.to_string())?;
        let ready_digest = match read_reply(&mut stream, &node.transport_budget)
            .await
            .map_err(|error| error.to_string())?
        {
            Reply::ContentStream(Ok(StreamNotice::Original(original))) => {
                check()?;
                clock.check()?;
                return Ok(*original);
            }
            Reply::ContentStream(Ok(StreamNotice::Ready { request_digest })) => {
                if !crate::content::hex_digest(&request_digest) {
                    return Err("actual receiver Ready lost its original request identity".into());
                }
                request_digest
            }
            Reply::ContentStream(Err(error)) | Reply::Rejected(error) => return Err(error),
            _ => return Err("actual receiver did not provide its original receive outcome".into()),
        };
        check()?;
        clock.check()?;
        let mut source = source_worker(SourceWork {
            root,
            capacity,
            target,
            content,
            command_id,
            receiver,
            clock: clock.clone(),
            check: check.clone(),
        });
        let mut offset = 0u64;
        while let Some(chunk) = source.recv().await {
            clock.check()?;
            check()?;
            match chunk {
                SourceChunk::Bytes(data) => {
                    stream
                        .write_u64(offset)
                        .await
                        .map_err(|error| error.to_string())?;
                    stream
                        .write_u32(data.len() as u32)
                        .await
                        .map_err(|error| error.to_string())?;
                    stream
                        .write_all(&data)
                        .await
                        .map_err(|error| error.to_string())?;
                    offset = offset
                        .checked_add(data.len() as u64)
                        .ok_or("original source offset overflow")?;
                }
                SourceChunk::Closed(result) => {
                    result?;
                    stream
                        .write_u64(offset)
                        .await
                        .map_err(|error| error.to_string())?;
                    stream
                        .write_u32(0)
                        .await
                        .map_err(|error| error.to_string())?;
                    let result = match read_reply(&mut stream, &node.transport_budget)
                        .await
                        .map_err(|error| error.to_string())?
                    {
                        Reply::ContentStreamFinished(outcome) => outcome,
                        _ => Err("actual receiver full-byte outcome is absent".into()),
                    }?;
                    check()?;
                    clock.check()?;
                    if result.request_digest != ready_digest {
                        return Err("actual receipt changed its original Ready identity".into());
                    }
                    return Ok(result);
                }
            }
        }
        Err("actual original source worker ended without its retained read result".into())
    };
    let observed = tokio::time::timeout(Duration::from_millis(duration_ms.min(15_000)), operation)
        .await
        .map_err(|_| "original content transport deadline expired".to_string())??;
    clock.check()?;
    node.network_target_authority(&expected.0)?;
    crate::content_store::verify_stream_observation(
        &observed,
        &expected.0,
        &expected.1,
        &expected.2,
        receiver,
    )?;
    Ok(observed)
}

/// Rechecks actual captured TLS and current committed membership without blocking on the parent writer.
pub(super) fn permission(
    node: ReplicaNode,
    target: ContentQuorumTarget,
    peer: u64,
    evidence: crate::tls::VerifiedTlsConnection,
) -> StreamPermission {
    Arc::new(move || {
        let trust = node.network_target_authority(&target)?;
        evidence.authorize_with_trust(peer, node.config.replica_id, &trust)
    })
}

/// Writes only bounded existing response framing, retaining its actual frame permit through I/O.
async fn reply<S>(stream: &mut S, value: Reply, budget: &TransportBudget) -> Result<(), String>
where
    S: tokio::io::AsyncWrite + Unpin,
{
    let encoded =
        crate::transport_budget::encode(&value, budget).map_err(|error| error.to_string())?;
    stream
        .write_u64(encoded.bytes.len() as u64)
        .await
        .map_err(|error| error.to_string())?;
    stream
        .write_all(&encoded.bytes)
        .await
        .map_err(|error| error.to_string())
}

/// Admits before Ready, then moves actual root/FD/capacity into one independent blocking receiver.
pub(super) async fn receive_content_stream<S>(
    stream: &mut S,
    node: ReplicaNode,
    target: ContentQuorumTarget,
    content: ArtifactContentKey,
    command_id: String,
    budget: ContentBudget,
    evidence: crate::tls::VerifiedTlsConnection,
) -> Result<(), String>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    budget.check()?;
    let peer = target.epoch.replica_id;
    let source_owner = target
        .membership
        .nodes()
        .find_map(|(id, member)| {
            (*id == peer)
                .then_some(member.content_store_owner.clone())
                .flatten()
        })
        .ok_or("actual original sending leader has no committed root")?;
    let origin = StreamOrigin::Replica {
        member: peer,
        store_owner: source_owner,
        peer_leaf: evidence
            .peer_digest()
            .ok_or("actual content peer leaf absent")?,
    };
    let check = permission(node.clone(), target.clone(), peer, evidence);
    receive_owned_stream(
        stream,
        &node,
        StreamReceive {
            target,
            content,
            command_id,
            budget,
            origin,
            check,
        },
    )
    .await
    .map(|_| ())
}

/// Receives one original stream while unique actual worker/FD/root scopes outlive async cancellation.
pub(crate) async fn receive_owned_stream<S>(
    stream: &mut S,
    node: &ReplicaNode,
    work: StreamReceive,
) -> Result<ReplicaContentStreamStatus, String>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let StreamReceive {
        target,
        content,
        command_id,
        budget,
        origin,
        check,
    } = work;
    budget.check()?;
    check()?;
    let root = node.store.network_content_store()?;
    let capacity = root.stream_work_permit()?;
    let expected_size = content.byte_size;
    let (ready_send, ready_receive) = tokio::sync::oneshot::channel();
    let (chunks_send, mut chunks_receive) = tokio::sync::mpsc::channel(2);
    let (done_send, done_receive) = tokio::sync::oneshot::channel();
    let _worker = tokio::task::spawn_blocking(move || {
        let _capacity = capacity;
        let admission = root.admit_stream(&command_id, target, content, origin, budget, check);
        let mut writer = match admission {
            Err(error) => {
                let _ = ready_send.send(Err(error));
                return;
            }
            Ok(StreamAdmission::Existing(original)) => {
                let _ = ready_send.send(Ok(StreamNotice::Original(original)));
                return;
            }
            Ok(StreamAdmission::New(writer)) => writer,
        };
        if ready_send
            .send(Ok(StreamNotice::Ready {
                request_digest: writer.original_digest().into(),
            }))
            .is_err()
        {
            return;
        }
        let result = (|| {
            while let Some(chunk) = chunks_receive.blocking_recv() {
                match chunk {
                    ReceiveChunk::Bytes { offset, data } => writer.write_chunk(offset, &data)?,
                    ReceiveChunk::Finish => return writer.finish(),
                }
            }
            Err("actual original content connection ended before its complete stream".into())
        })();
        let _ = done_send.send(result);
    });
    let notice = ready_receive.await.map_err(|error| error.to_string())?;
    let existing = match &notice {
        Ok(StreamNotice::Original(original)) => Some(*original.clone()),
        _ => None,
    };
    let transfer = matches!(notice, Ok(StreamNotice::Ready { .. }));
    reply(stream, Reply::ContentStream(notice), &node.transport_budget).await?;
    if !transfer {
        return existing.ok_or("actual original receive did not admit a worker".into());
    }
    loop {
        let offset = stream.read_u64().await.map_err(|error| error.to_string())?;
        let length = stream.read_u32().await.map_err(|error| error.to_string())?;
        if length == 0 {
            if offset != expected_size {
                return Err("original content stream ended at another offset".into());
            }
            chunks_send
                .send(ReceiveChunk::Finish)
                .await
                .map_err(|error| error.to_string())?;
            break;
        }
        if length as usize > STREAM_CHUNK_BYTES {
            return Err("original content chunk exceeds its fixed network bound".into());
        }
        let mut data = vec![0; length as usize];
        stream
            .read_exact(&mut data)
            .await
            .map_err(|error| error.to_string())?;
        chunks_send
            .send(ReceiveChunk::Bytes { offset, data })
            .await
            .map_err(|error| error.to_string())?;
    }
    drop(chunks_send);
    let outcome = done_receive.await.map_err(|error| error.to_string())?;
    reply(
        stream,
        Reply::ContentStreamFinished(outcome.clone()),
        &node.transport_budget,
    )
    .await?;
    outcome
}
