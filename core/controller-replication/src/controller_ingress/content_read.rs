//! Qualified original Root reads over the actual owned-child channel; no Artifact owner is created.

use super::wire::{ChildReply, peer_process, permission, write};
use super::*;
use crate::content_store::ReplicaContentReaderReceipt;
use tokio::io::AsyncWriteExt;

/// Explicit infrastructure read purpose; serialized labels never grant semantic use.
#[derive(Clone, Serialize, Deserialize)]
pub(super) enum ReadPurpose {
    /// A committed independent recovery intent grants only its original bounded payload copy.
    PreparedRecovery {
        /// Exact original recovery command, never an implicit upload retry.
        command_id: String,
    },
    /// Existing public ACL/import/use checks still gate every HTTP chunk.
    Published {
        /// Exact public content digest; the parent resolves one unambiguous original qualified owner.
        digest: String,
    },
    /// Startup verifies the exact qualified retained dependency without publishing local CAS bytes.
    RetainedIntegrity {
        /// Complete original retained dependency; no alternate source owner or size is inferred.
        content: ArtifactContentKey,
    },
}

/// Actual source metadata captured before any bytes are returned to the original child.
#[derive(Clone, Serialize, Deserialize)]
pub(super) struct ReadHead {
    /// Exact current complete accepted target, independently checked throughout the original scope.
    pub(super) target: Box<ContentQuorumTarget>,
    /// One complete original qualified input, never the current child's Artifact owner alias.
    pub(super) content: ArtifactContentKey,
    /// Original registered child incarnation, independently matched against the Unix peer.
    pub(super) process: ControllerProcessIdentity,
    /// Exact original one-shot reader operation.
    pub(super) reader_id: String,
    /// Digest of actual process/leaf/target/purpose/request context, supplied to the durable Root reader.
    pub(super) authority_digest: String,
}

/// Bounded queue elements keep the original reader/lease on its actual worker through cancellation.
enum ReadChunk {
    /// Complete original file has been opened and independently verified after durable reader admission.
    Ready(Result<(), String>),
    /// At most one original 64 KiB payload read.
    Bytes(Vec<u8>),
    /// Actual original reader outcome; failure never becomes a completion receipt.
    Closed(Result<ReplicaContentReaderReceipt, String>),
}

/// Opens one actual original reader only after process, full target and explicit purpose checks.
pub(super) async fn serve<S: tokio::io::AsyncWrite + Unpin>(
    stream: &mut S,
    node: &ReplicaNode,
    peer: ControllerPeer,
    reader_id: String,
    purpose: ReadPurpose,
    context_digest: String,
    clock: ContentBudget,
) -> Result<(), String> {
    if reader_id.is_empty()
        || reader_id.len() > 128
        || reader_id.chars().any(char::is_control)
        || !crate::content::hex_digest(&context_digest)
    {
        return Err("invalid original content read purpose/operation".into());
    }
    clock.check()?;
    node.check_epoch(peer.epoch).await?;
    let process = peer_process(node, peer.clone()).await?;
    let target = node.accepted_content_target().await?;
    let view = node.store.network_view().await?;
    let expected = target.clone();
    let requested_purpose = purpose.clone();
    let admission_reader = reader_id.clone();
    let admission_context = context_digest.clone();
    let admission_process = process.clone();
    let (content, recovery) = crate::transport::content_worker(node, move |current| {
        let purpose = requested_purpose;
        let reader_id = admission_reader;
        let context_digest = admission_context;
        let process = admission_process;
        let recovery = if let ReadPurpose::PreparedRecovery { command_id } = &purpose {
            let original = super::recovery::pending(&view, command_id)?;
            let (owner, budget) = super::recovery::evidence(&original)?;
            budget.check()?;
            if original.reader_id != reader_id
                || owner != process
                || context_digest != domain::artifact_adoption_digest(&original)?[7..]
            {
                return Err("Prepared read differs from original recovery process/context".into());
            }
            Some(original)
        } else {
            None
        };
        let content = match &purpose {
            ReadPurpose::PreparedRecovery { .. } => {
                super::recovery::source_key(recovery.as_ref().ok_or("original recovery absent")?)?
            }
            ReadPurpose::Published { digest } => {
                let digest =
                    artifact_store::normalize_digest(digest).map_err(|error| error.to_string())?;
                view.content
                    .as_ref()
                    .ok_or("actual published original metadata unavailable")?
                    .published_content(&view.events, &digest)?
            }
            ReadPurpose::RetainedIntegrity { content } => {
                content.validate()?;
                content.clone()
            }
        };
        if expected.inputs.binary_search(&content).is_err() {
            return Err("original content is not currently retained by the complete target".into());
        }
        current.store.require_committed_content_target(&expected)?;
        Ok((content, recovery))
    })
    .await?;
    let leaf = peer
        .connection
        .peer_digest()
        .ok_or("actual original read peer leaf unavailable")?;
    let published = matches!(&purpose, ReadPurpose::Published { .. });
    let authority = serde_json::json!({"process":process,"peer_leaf":leaf,"target":target,
        "content":content,"purpose":purpose,"context_digest":context_digest,"reader_id":reader_id});
    let authority_digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&authority).map_err(|error| error.to_string())?)
    );
    let head = ReadHead {
        target: Box::new(target.clone()),
        content: content.clone(),
        process,
        reader_id: reader_id.clone(),
        authority_digest: authority_digest.clone(),
    };
    let original_permission = permission(node.clone(), peer, clock.clone(), Some(target.clone()));
    let held = node.clone();
    let expected = target;
    let original_key = content.clone();
    let check: StreamPermission = Arc::new(move || {
        original_permission()?;
        held.store.require_current_content_read(
            &expected,
            &original_key,
            published,
            recovery.as_ref(),
        )
    });
    let admission = check.clone();
    crate::transport::content_worker(node, move |_| admission()).await?;
    let root = node.store.network_content_store()?;
    let capacity = root.stream_work_permit()?;
    let (send, mut receive) = tokio::sync::mpsc::channel(2);
    let read_clock = clock.clone();
    tokio::task::spawn_blocking(move || {
        let _capacity = capacity;
        let opened = (|| {
            read_clock.check()?;
            check()?;
            root.open_reader_checked(
                &content,
                &reader_id,
                &authority_digest,
                read_clock.duration_ms,
                check.clone(),
            )
        })();
        let mut source = match opened {
            Ok(source) => source,
            Err(error) => {
                let _ = send.blocking_send(ReadChunk::Ready(Err(error)));
                return;
            }
        };
        if send.blocking_send(ReadChunk::Ready(Ok(()))).is_err() {
            return;
        }
        let result = (|| {
            loop {
                read_clock.check()?;
                check()?;
                let mut chunk = vec![0; crate::content_store::STREAM_CHUNK_BYTES];
                let count = std::io::Read::read(&mut source, &mut chunk)
                    .map_err(|error| error.to_string())?;
                read_clock.check()?;
                check()?;
                if count == 0 {
                    break;
                }
                chunk.truncate(count);
                send.blocking_send(ReadChunk::Bytes(chunk))
                    .map_err(|_| "original child no longer receives its read")?;
            }
            let receipt = source.finish()?;
            read_clock.check()?;
            check()?;
            if !receipt.complete {
                return Err("original qualified reader is incomplete".into());
            }
            Ok(receipt)
        })();
        let _ = send.blocking_send(ReadChunk::Closed(result));
    });
    match receive.recv().await {
        Some(ReadChunk::Ready(result)) => result?,
        _ => return Err("actual qualified reader ended before opening".into()),
    }
    clock.check()?;
    write(stream, &ChildReply::ReadReady(Box::new(head)), node).await?;
    while let Some(chunk) = receive.recv().await {
        clock.check()?;
        match chunk {
            ReadChunk::Bytes(bytes) => {
                stream
                    .write_u32(bytes.len() as u32)
                    .await
                    .map_err(|error| error.to_string())?;
                stream
                    .write_all(&bytes)
                    .await
                    .map_err(|error| error.to_string())?;
            }
            ReadChunk::Closed(result) => {
                stream
                    .write_u32(0)
                    .await
                    .map_err(|error| error.to_string())?;
                return write(stream, &ChildReply::ReadClosed(result), node).await;
            }
            ReadChunk::Ready(_) => {
                return Err("actual qualified reader repeated its admission".into());
            }
        }
    }
    Err("actual qualified reader ended without its original result".into())
}
