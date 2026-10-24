//! One real writer mutex with an explicit request-authority check at acquisition.

use std::sync::{Arc, Mutex, MutexGuard};

/// Hands an actual Tokio worker back before a synchronous writer/SQL transaction can block it.
pub(crate) fn run_synchronous_transaction<T>(apply: impl FnOnce() -> T) -> T {
    if tokio::runtime::Handle::try_current()
        .is_ok_and(|runtime| runtime.runtime_flavor() == tokio::runtime::RuntimeFlavor::MultiThread)
    {
        tokio::task::block_in_place(apply)
    } else {
        // Offline/current-thread callers own their synchronous execution; there is no worker handoff.
        apply()
    }
}

/// Serializes authority transactions; HTTP implementations also recheck the actual transport witness.
pub(crate) trait ApplicationWriteGate: Send + Sync {
    /// Holds the real application mutex until Commit/rollback, or rejects before application mutation.
    fn lock(&self) -> Result<MutexGuard<'_, ()>, String>;
    /// Rechecks the original transport while the caller still holds its real writer guard.
    fn validate_locked(&self) -> Result<(), String> {
        Ok(())
    }
}

impl ApplicationWriteGate for Mutex<()> {
    /// Supplies the existing writer ordering to trusted in-process startup/timer/Node inputs.
    fn lock(&self) -> Result<MutexGuard<'_, ()>, String> {
        Mutex::lock(self).map_err(|_| "event-log write gate is poisoned".into())
    }
}

impl<G: ApplicationWriteGate + ?Sized> ApplicationWriteGate for Arc<G> {
    /// Preserves the wrapped authorization check when ownership moves into a blocking worker.
    fn lock(&self) -> Result<MutexGuard<'_, ()>, String> {
        self.as_ref().lock()
    }
    /// Keeps the underlying witness check at a caller's final Commit boundary.
    fn validate_locked(&self) -> Result<(), String> {
        self.as_ref().validate_locked()
    }
}
