//! Optional cluster epoch/quorum fence at the actual command transport boundary.

/// Deployment-owned delivery admission after durable application acceptance.
pub trait CommandDeliveryFence: Send + Sync {
    /// Rejects delivery when the original issuing Controller epoch is no longer quorum-authorized.
    fn check_delivery(&self) -> Result<(), String>;

    /// Returns the issuing Controller epoch for Node-side monotonic fencing, when configured.
    ///
    /// Standalone Controllers return `None`; replicated composition roots provide a stable
    /// serialized epoch that the Node journal compares before admitting Execute or Cancel.
    fn controller_epoch(&self) -> Option<String> {
        None
    }
}
