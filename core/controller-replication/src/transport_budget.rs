//! Local resource admission for actual RPC lifetimes, never consensus or execution authority.

use serde::{Deserialize, Serialize};
use std::io::{Error, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

#[cfg(test)]
#[path = "transport_budget_tests.rs"]
mod tests;

/// Original compatible wire bound, including escaped authority/history JSON.
pub(crate) const MAX_FRAME_BYTES: usize = 256 * 1024 * 1024;
/// Fixed small-frame class leaves independent room for election, heartbeat and fence envelopes.
const SMALL_FRAME_BYTES: usize = 64 * 1024;

/// Closed local deployment capacities; these never alter cluster or application policy identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaTransportLimits {
    /// Actual inbound handlers admitted before TLS; excess sockets close without a task.
    pub inbound_connections: usize,
    /// Actual asynchronous outbound exchanges, including encoding and reply consumption.
    pub outbound_connections: usize,
    /// Shared raw-frame capacity dedicated to frames no larger than 64 KiB.
    pub small_frame_bytes: u32,
    /// Shared raw-frame capacity for larger frames, with no implicit wait queue.
    pub large_frame_bytes: u32,
}

impl Default for ReplicaTransportLimits {
    /// Keeps valid legacy frames possible while bounding default local transport resources.
    fn default() -> Self {
        Self {
            inbound_connections: 128,
            outbound_connections: 64,
            small_frame_bytes: 8 * 1024 * 1024,
            large_frame_bytes: 512 * 1024 * 1024,
        }
    }
}

impl ReplicaTransportLimits {
    /// Rejects zero, oversized and inconsistent local budgets before any socket or store opens.
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=1024).contains(&self.inbound_connections)
            || !(1..=1024).contains(&self.outbound_connections)
            || !(SMALL_FRAME_BYTES as u32..=64 * 1024 * 1024).contains(&self.small_frame_bytes)
            || !(1024 * 1024..=2 * 1024 * 1024 * 1024).contains(&self.large_frame_bytes)
            || self.small_frame_bytes as usize > Semaphore::MAX_PERMITS
            || self.large_frame_bytes as usize > Semaphore::MAX_PERMITS
        {
            return Err("replica transport limits exceed supported local capacities".into());
        }
        Ok(())
    }
}

/// Sampled local observations, with fixed fields and no request/member/tenant labels or authority.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaTransportSnapshot {
    /// Original configured local capacities.
    pub limits: ReplicaTransportLimits,
    /// Currently held inbound permits, including incomplete TLS or frame reads.
    pub inbound_active: usize,
    /// Currently held outbound permits, including encoding and pending replies.
    pub outbound_active: usize,
    /// Currently reserved original encoded bytes in the small-frame pool.
    pub small_frame_active_bytes: usize,
    /// Currently reserved original encoded bytes in the large-frame pool.
    pub large_frame_active_bytes: usize,
    /// Highest sampled inbound permit occupancy since this process started.
    pub inbound_high_water: usize,
    /// Actual sockets refused before creating a TLS handler.
    pub inbound_rejected_total: u64,
    /// Actual exchanges refused before encoding or opening a socket.
    pub outbound_rejected_total: u64,
    /// Invalid lengths or unavailable frame capacity, before allocation.
    pub frame_rejected_total: u64,
    /// Actual admitted inbound handlers whose original fifteen-second wait expired.
    pub inbound_timeout_total: u64,
}

/// One shared actual process budget used by inbound handlers and the node's async network clients.
pub(crate) struct TransportBudget {
    /// Startup-validated immutable local capacities.
    limits: ReplicaTransportLimits,
    /// Actual inbound lifetimes, acquired before a new task or TLS handshake.
    inbound: Arc<Semaphore>,
    /// Actual outgoing exchanges, acquired before encoding.
    outbound: Arc<Semaphore>,
    /// Separate small-frame reserve, unavailable to large-frame borrowers.
    small: Arc<Semaphore>,
    /// Aggregate capacity for original large encoded frames.
    large: Arc<Semaphore>,
    /// Sampled inbound occupancy maximum.
    inbound_high_water: AtomicUsize,
    /// Actual pre-TLS capacity refusals.
    inbound_rejected: AtomicU64,
    /// Actual outbound capacity refusals.
    outbound_rejected: AtomicU64,
    /// Actual invalid or capacity-refused frames.
    frame_rejected: AtomicU64,
    /// Actual original handler deadlines which expired.
    inbound_timeout: AtomicU64,
}

impl TransportBudget {
    /// Builds independent semaphores only after validating the complete declaration.
    pub(crate) fn new(limits: ReplicaTransportLimits) -> Result<Arc<Self>, String> {
        limits.validate()?;
        Ok(Arc::new(Self {
            inbound: Arc::new(Semaphore::new(limits.inbound_connections)),
            outbound: Arc::new(Semaphore::new(limits.outbound_connections)),
            small: Arc::new(Semaphore::new(limits.small_frame_bytes as usize)),
            large: Arc::new(Semaphore::new(limits.large_frame_bytes as usize)),
            limits,
            inbound_high_water: AtomicUsize::new(0),
            inbound_rejected: AtomicU64::new(0),
            outbound_rejected: AtomicU64::new(0),
            frame_rejected: AtomicU64::new(0),
            inbound_timeout: AtomicU64::new(0),
        }))
    }

    /// Returns the actual inbound lifetime permit without queuing a waiter.
    pub(crate) fn inbound(&self) -> Result<OwnedSemaphorePermit, Error> {
        let permit = self.inbound.clone().try_acquire_owned().map_err(|_| {
            self.inbound_rejected.fetch_add(1, Ordering::Relaxed);
            Error::other("replica inbound connection capacity unavailable")
        })?;
        self.inbound_high_water.fetch_max(
            self.limits.inbound_connections - self.inbound.available_permits(),
            Ordering::Relaxed,
        );
        Ok(permit)
    }

    /// Reserves an outgoing exchange before JSON encoding or network I/O, with no wait queue.
    pub(crate) fn outbound(&self) -> Result<OwnedSemaphorePermit, Error> {
        self.outbound.clone().try_acquire_owned().map_err(|_| {
            self.outbound_rejected.fetch_add(1, Ordering::Relaxed);
            Error::other("replica outbound connection capacity unavailable")
        })
    }

    /// Reserves an exact original encoded frame before allocation, retaining the legacy wire bound.
    pub(crate) fn frame(&self, length: u64) -> Result<OwnedSemaphorePermit, Error> {
        let result = if length == 0 || length > MAX_FRAME_BYTES as u64 {
            Err(Error::other("replica frame exceeds bounds"))
        } else {
            let capacity = if length <= SMALL_FRAME_BYTES as u64 {
                &self.small
            } else {
                &self.large
            };
            capacity
                .clone()
                .try_acquire_many_owned(length as u32)
                .map_err(|_| Error::other("replica frame capacity unavailable"))
        };
        if result.is_err() {
            self.frame_rejected.fetch_add(1, Ordering::Relaxed);
        }
        result
    }

    /// Counts an actual deadline expiry without claiming that a dispatched effect was cancelled.
    pub(crate) fn timed_out(&self) {
        self.inbound_timeout.fetch_add(1, Ordering::Relaxed);
    }

    /// Reads current semaphore ownership and counters without acquiring authority or renewing work.
    pub(crate) fn snapshot(&self) -> ReplicaTransportSnapshot {
        ReplicaTransportSnapshot {
            limits: self.limits.clone(),
            inbound_active: self.limits.inbound_connections - self.inbound.available_permits(),
            outbound_active: self.limits.outbound_connections - self.outbound.available_permits(),
            small_frame_active_bytes: self.limits.small_frame_bytes as usize
                - self.small.available_permits(),
            large_frame_active_bytes: self.limits.large_frame_bytes as usize
                - self.large.available_permits(),
            inbound_high_water: self.inbound_high_water.load(Ordering::Relaxed),
            inbound_rejected_total: self.inbound_rejected.load(Ordering::Relaxed),
            outbound_rejected_total: self.outbound_rejected.load(Ordering::Relaxed),
            frame_rejected_total: self.frame_rejected.load(Ordering::Relaxed),
            inbound_timeout_total: self.inbound_timeout.load(Ordering::Relaxed),
        }
    }
}

/// Encoded bytes and the exact original capacity retained until their actual network use ends.
pub(crate) struct EncodedFrame {
    /// Wire bytes, dropped before their capacity is returned.
    pub(crate) bytes: Vec<u8>,
    /// Original frame reservation, including incomplete network writes.
    _permit: OwnedSemaphorePermit,
}

/// Serializes twice to count a bounded frame before its sole buffer allocation and capacity grant.
pub(crate) fn encode<T: Serialize>(
    value: &T,
    budget: &TransportBudget,
) -> Result<EncodedFrame, Error> {
    let mut count = BoundedLength(0);
    serde_json::to_writer(&mut count, value).map_err(Error::other)?;
    let permit = budget.frame(count.0 as u64)?;
    let mut output = BoundedBuffer {
        bytes: Vec::with_capacity(count.0),
        limit: count.0,
    };
    serde_json::to_writer(&mut output, value).map_err(Error::other)?;
    if output.bytes.len() != count.0 {
        return Err(Error::other(
            "replica serialization changed during encoding",
        ));
    }
    Ok(EncodedFrame {
        bytes: output.bytes,
        _permit: permit,
    })
}

/// Exact counted allocation which also fences serializers that change between the two passes.
struct BoundedBuffer {
    /// Sole original allocation, never grown past the counted and reserved length.
    bytes: Vec<u8>,
    /// Original exact reservation; a second pass cannot increase it.
    limit: usize,
}

impl Write for BoundedBuffer {
    /// Checks remaining actual capacity before copying any new bytes.
    fn write(&mut self, bytes: &[u8]) -> Result<usize, Error> {
        if bytes.len() > self.limit - self.bytes.len() {
            return Err(Error::other(
                "replica serialization exceeded original counted length",
            ));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    /// This in-memory encoder has no external I/O or authority to flush.
    fn flush(&mut self) -> Result<(), Error> {
        Ok(())
    }
}

/// Allocation-free checked encoded-length counter; no partial success after exceeding the wire cap.
struct BoundedLength(usize);

impl Write for BoundedLength {
    /// Counts complete serialization writes while refusing overflow or an unsupported wire frame.
    fn write(&mut self, bytes: &[u8]) -> Result<usize, Error> {
        self.0 = self
            .0
            .checked_add(bytes.len())
            .filter(|length| *length <= MAX_FRAME_BYTES)
            .ok_or_else(|| Error::other("replica encoded frame exceeds bounds"))?;
        Ok(bytes.len())
    }

    /// The counter retains no buffered I/O or authority to flush.
    fn flush(&mut self) -> Result<(), Error> {
        Ok(())
    }
}
