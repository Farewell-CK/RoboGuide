//! Actual bounded blocking ownership for opted-in original producer operations, including cancellation.

use super::*;
use crate::content_store::ProducerClock;

impl SqliteRaftStore {
    /// Admits at most eight actual running/queued heavy jobs before spawning, retaining original leases.
    pub(super) fn original_storage_work(
        &self,
    ) -> Result<(Self, tokio::sync::OwnedSemaphorePermit), StorageError<u64>> {
        if !self.has_producer_journal() {
            return Err(io_error(
                "original managed storage work requires its explicit producer profile",
            ));
        }
        let clock = ProducerClock::issue().map_err(io_error)?;
        let capacity = self
            .storage_jobs
            .clone()
            .try_acquire_owned()
            .map_err(|_| io_error("original content storage work capacity is exhausted"))?;
        let mut store = self.clone();
        store.work_clock = Some(clock);
        Ok((store, capacity))
    }

    /// Moves actual queue capacity, original budget and all root/SQL leases into the real worker scope.
    /// Cancelling the waiting future never drops a still-running worker's permit or original ownership.
    pub(super) async fn run_storage_work<T, F>(&self, work: F) -> Result<T, StorageError<u64>>
    where
        T: Send + 'static,
        F: FnOnce(Self) -> Result<T, StorageError<u64>> + Send + 'static,
    {
        let (store, capacity) = self.original_storage_work()?;
        tokio::task::spawn_blocking(move || {
            let _capacity = capacity;
            store
                .work_clock
                .as_ref()
                .ok_or_else(|| io_error("original queued work clock is missing"))?
                .check()
                .map_err(io_error)?;
            work(store)
        })
        .await
        .map_err(|error| {
            io_error(format!(
                "original content storage worker did not return: {error}"
            ))
        })?
    }
}
