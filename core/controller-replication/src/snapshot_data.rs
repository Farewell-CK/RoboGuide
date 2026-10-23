//! Actual OpenRaft Snapshot handle retains independent original content readers through its lifetime.

use crate::ReplicaContentReadLease;
use std::io::Cursor;
use std::pin::Pin;
use std::task::{Context, Poll};
use tokio::io::{AsyncRead, AsyncSeek, AsyncWrite, ReadBuf};

/// Bounded upstream Snapshot bytes plus independent actual original content-retention ownership.
/// Member-local leases/history never enter the serialized Raft Snapshot body.
pub struct ReplicaSnapshotData {
    /// Actual upstream metadata cursor, separate from opaque CAS payloads.
    data: Cursor<Vec<u8>>,
    /// Actual original managed reader scope; absent in unchanged metadata-only profiles.
    reader: Option<ReplicaContentReadLease>,
}

impl ReplicaSnapshotData {
    /// Wraps bounded old-profile metadata without inventing a content reader or availability proof.
    pub fn new(data: Vec<u8>) -> Self {
        Self {
            data: Cursor::new(data),
            reader: None,
        }
    }

    /// Retains the exact actual scoped content owner before the upstream handle can expose any bytes.
    pub(crate) fn retained(data: Vec<u8>, reader: ReplicaContentReadLease) -> Self {
        Self {
            data: Cursor::new(data),
            reader: Some(reader),
        }
    }

    /// Consumes only this actual handle; its original reader ends independently of other Snapshots/readers.
    pub fn into_inner(mut self) -> Vec<u8> {
        std::mem::take(&mut self.data).into_inner()
    }
}

impl Drop for ReplicaSnapshotData {
    /// Releases this actual Snapshot scope through its original root without blocking a Tokio worker.
    fn drop(&mut self) {
        if let Some(reader) = self.reader.take() {
            reader.close_without_blocking();
        }
    }
}

impl AsyncRead for ReplicaSnapshotData {
    /// Reads only this bounded actual metadata cursor while its original content scope remains owned.
    fn poll_read(
        self: Pin<&mut Self>,
        context: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.get_mut().data).poll_read(context, buffer)
    }
}

impl AsyncWrite for ReplicaSnapshotData {
    /// Refuses upstream Snapshot receive growth beyond the fixed actual metadata transfer bound.
    fn poll_write(
        self: Pin<&mut Self>,
        context: &mut Context<'_>,
        input: &[u8],
    ) -> Poll<std::io::Result<usize>> {
        let this = self.get_mut();
        if this
            .data
            .position()
            .checked_add(input.len() as u64)
            .is_none_or(|size| size > 256 * 1024 * 1024)
        {
            return Poll::Ready(Err(std::io::Error::other(
                "replica Snapshot receive exceeds bound",
            )));
        }
        Pin::new(&mut this.data).poll_write(context, input)
    }
    /// Flushes actual metadata receive buffers without granting content or application success.
    fn poll_flush(self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.get_mut().data).poll_flush(context)
    }
    /// Ends this upstream write half only; it does not release another original operation's ownership.
    fn poll_shutdown(self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.get_mut().data).poll_shutdown(context)
    }
}

impl AsyncSeek for ReplicaSnapshotData {
    /// Preserves upstream metadata cursor positioning without reopening content or changing budgets.
    fn start_seek(self: Pin<&mut Self>, position: std::io::SeekFrom) -> std::io::Result<()> {
        Pin::new(&mut self.get_mut().data).start_seek(position)
    }
    /// Completes the upstream seek while actual original scope ownership stays retained.
    fn poll_complete(
        self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<std::io::Result<u64>> {
        Pin::new(&mut self.get_mut().data).poll_complete(context)
    }
}
