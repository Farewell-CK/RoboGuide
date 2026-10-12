//! Optional cluster epoch/quorum fence at the actual command transport boundary.

/// Deployment-owned delivery admission after durable application acceptance.
pub trait CommandDeliveryFence: Send + Sync {
    /// Rejects delivery when the original issuing Controller epoch is no longer quorum-authorized.
    fn check_delivery(&self) -> Result<(), String>;
}
