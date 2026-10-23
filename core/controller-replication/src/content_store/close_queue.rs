//! One actual blocking drainer per original root; pending work never becomes a durable close proof.

use super::reader::ReaderIntent;
use super::*;
use std::collections::VecDeque;

/// Existing retained reader-row bound also bounds actual queued/active nonempty scope work.
const MAX_PENDING_READERS: usize = 4096;

/// Process-local observations only; callers independently check original SQL receipts and pins.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaSnapshotCloseStatus {
    /// Actual original reader entries held by queued or active close work.
    pub pending_readers: usize,
    /// Whether this root has one actual scheduled/running blocking drainer.
    pub worker_active: bool,
    /// Actual close publication errors; the corresponding durable outcome may remain Unknown.
    pub publication_errors_total: u64,
    /// Actual lost/unadmitted original close entries; this never authorizes their release.
    pub abandoned_readers_total: u64,
}

/// Unique moved scope data; dropping an unprocessed job never runs synchronous receipt SQL.
pub(super) struct CloseJob {
    /// Exact original admitted identities and budgets, never reconstructed from caller labels.
    pub(super) originals: Vec<ReaderIntent>,
    /// Actual original writer ownership retained through its last SQL use.
    pub(super) store: ReplicaContentStore,
}

/// One original generation's actual queue state, unrelated to replicated or device authority.
#[derive(Default)]
struct QueueState {
    /// Bounded unique actual scope jobs, each consuming at least one retained reader row.
    jobs: VecDeque<CloseJob>,
    /// Original job entries not yet finished, including the active job.
    pending: usize,
    /// Prevents scheduling another drainer for this root while the current one owns work.
    running: bool,
    /// Original worker incarnation; a late old guard cannot clear a replacement worker.
    generation: u64,
    /// Count of actual SQL close methods which returned an error.
    publication_errors: u64,
    /// Count of original entries whose actual close work did not finish through its worker.
    abandoned: u64,
}

/// Shared by all original root clones; only the actual blocking drainer performs close SQL.
#[derive(Default)]
pub(super) struct SnapshotCloseQueue {
    /// Short queue-only critical sections never contain SQL, filesystem reads or durable publication.
    state: Mutex<QueueState>,
}

impl SnapshotCloseQueue {
    /// Reads local work ownership only, including actual loss/error observations.
    pub(super) fn status(&self) -> ReplicaSnapshotCloseStatus {
        let state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        ReplicaSnapshotCloseStatus {
            pending_readers: state.pending,
            worker_active: state.running,
            publication_errors_total: state.publication_errors,
            abandoned_readers_total: state.abandoned,
        }
    }

    /// Moves one unique scope into its bounded root queue without waiting for SQL or another job.
    pub(super) fn enqueue(self: &Arc<Self>, job: CloseJob, runtime: tokio::runtime::Handle) {
        if job.originals.is_empty() {
            return;
        }
        let generation = {
            let mut state = self
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let count = job.originals.len();
            if state.pending.saturating_add(count) > MAX_PENDING_READERS
                || (!state.running && state.generation == u64::MAX)
            {
                state.abandoned = state.abandoned.saturating_add(count as u64);
                return;
            }
            state.pending += count;
            state.jobs.push_back(job);
            if state.running {
                return;
            }
            state.running = true;
            state.generation += 1;
            state.generation
        };
        // The guard is captured before scheduling, including cancellation before the worker starts.
        let guard = DrainGuard {
            queue: self.clone(),
            generation,
        };
        drop(runtime.spawn_blocking(move || guard.run()));
    }
}

/// Owns one original scheduled worker; cancellation/panic only abandons its own in-memory jobs.
struct DrainGuard {
    /// Actual original root queue, not a new or replacement content backend.
    queue: Arc<SnapshotCloseQueue>,
    /// Original generation captured before scheduling.
    generation: u64,
}

impl DrainGuard {
    /// Drains only this original worker generation, retaining each actual root through its SQL use.
    fn run(self) {
        loop {
            let job = {
                let mut state = self
                    .queue
                    .state
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                if state.generation != self.generation || !state.running {
                    return;
                }
                match state.jobs.pop_front() {
                    Some(job) => job,
                    None => {
                        state.running = false;
                        return;
                    }
                }
            };
            let count = job.originals.len();
            let failures = super::pins::close_originals(&job.store, &job.originals);
            drop(job);
            let mut state = self
                .queue
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.pending -= count;
            state.publication_errors = state.publication_errors.saturating_add(failures as u64);
        }
    }
}

impl Drop for DrainGuard {
    /// Retains durable Unknown after lost work; an old guard cannot interfere with a new generation.
    fn drop(&mut self) {
        let abandoned = {
            let mut state = self
                .queue
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if state.generation != self.generation || !state.running {
                return;
            }
            state.running = false;
            state.abandoned = state.abandoned.saturating_add(state.pending as u64);
            state.pending = 0;
            std::mem::take(&mut state.jobs)
        };
        // CloseJob has no SQL Drop. Root descriptors, if final, close outside the queue mutex.
        drop(abandoned);
    }
}

impl ReplicaContentStore {
    /// Observes actual local close work; an idle queue never proves an original reader is closed.
    pub fn snapshot_close_status(&self) -> ReplicaSnapshotCloseStatus {
        self.inner.snapshot_closes.status()
    }

    /// Waits only for current local work with a fresh bounded observation budget, never a read grant.
    /// Errors/abandonment remain explicit in the result; original SQL receipts still govern pins.
    pub async fn wait_for_snapshot_closures(
        &self,
        budget: std::time::Duration,
    ) -> Result<ReplicaSnapshotCloseStatus, String> {
        if !(std::time::Duration::from_millis(100)..=std::time::Duration::from_secs(30))
            .contains(&budget)
            || tokio::runtime::Handle::try_current().is_err()
        {
            return Err(
                "snapshot cleanup wait requires Tokio and a bounded observation budget".into(),
            );
        }
        let deadline = tokio::time::Instant::now() + budget;
        loop {
            let status = self.snapshot_close_status();
            if status.pending_readers == 0 && !status.worker_active {
                return Ok(status);
            }
            if tokio::time::Instant::now() >= deadline {
                return Err("original Snapshot cleanup work remains pending".into());
            }
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
    }
}
