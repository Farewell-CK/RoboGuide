//! Actual bounded blocking ownership for opted-in original producer operations, including cancellation.

use super::*;
use crate::content_store::ProducerClock;

impl SqliteRaftStore {
    /// Observes the retained original handoff without blocking async workers on the parent writer.
    pub(crate) async fn leader_transfer_record_on_worker(
        &self,
    ) -> Result<Option<LeaderTransferRecord>, String> {
        self.run_consensus_work(|store| store.leader_transfer_record().map_err(io_error))
            .await
            .map_err(|error| error.to_string())
    }

    /// Preserves the original handoff transition while retaining its writer through owned SQL work.
    pub(crate) async fn persist_leader_transfer_on_worker(
        &self,
        record: LeaderTransferRecord,
    ) -> Result<(), String> {
        self.run_consensus_work(move |store| {
            store.persist_leader_transfer(&record).map_err(io_error)
        })
        .await
        .map_err(|error| error.to_string())
    }

    /// Keeps actual consensus SQL/parse/flush waits off the network worker while preserving all leases.
    pub(super) async fn run_consensus_work<T, F>(&self, work: F) -> Result<T, StorageError<u64>>
    where
        T: Send + 'static,
        F: FnOnce(Self) -> Result<T, StorageError<u64>> + Send + 'static,
    {
        if !self.has_producer_journal() {
            return work(self.clone());
        }
        let clock = ProducerClock::issue().map_err(io_error)?;
        let capacity = self
            .consensus_jobs
            .clone()
            .try_acquire_owned()
            .map_err(|_| io_error("actual consensus SQL work capacity is exhausted"))?;
        let mut store = self.clone();
        store.work_clock = Some(clock.clone());
        tokio::task::spawn_blocking(move || {
            let _capacity = capacity;
            clock.check().map_err(io_error)?;
            work(store)
        })
        .await
        .map_err(|error| {
            io_error(format!(
                "actual consensus SQL worker did not return: {error}"
            ))
        })?
    }
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
