//! One real writer mutex with an explicit request-authority check at acquisition.

use std::marker::PhantomData;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};

/// One process-local FIFO writer; queue ordering grants no persisted or external authority.
#[derive(Debug, Default)]
pub(crate) struct ApplicationWriter {
    /// Ticket bookkeeping is held only while entering or leaving, never through application work.
    state: Mutex<WriterQueue>,
    /// Wakes waiters after release or poison; only the original next ticket may enter.
    changed: Condvar,
}

/// Original arrival order and a monotonic process-local panic fence.
#[derive(Debug, Default)]
struct WriterQueue {
    /// Next unique arrival, never reused while this process exists.
    next: u64,
    /// Original ticket permitted to hold the writer.
    serving: u64,
    /// A panic or bookkeeping failure refuses every later application mutation.
    poisoned: bool,
}

/// Original exclusive turn, retained through the complete authorized Commit or rollback.
#[derive(Debug)]
pub(crate) struct WriteGuard<'a> {
    /// Actual process-local writer which admitted this original ticket.
    writer: &'a ApplicationWriter,
    /// Immutable original arrival identity.
    ticket: u64,
    /// Preserve the original non-Send mutex-guard contract across blocking work.
    thread: PhantomData<MutexGuard<'a, ()>>,
}

impl ApplicationWriter {
    /// Enqueues once; timers and Node facts cannot repeatedly overtake an already waiting command.
    pub(crate) fn lock(&self) -> Result<WriteGuard<'_>, String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "application writer is poisoned")?;
        if state.poisoned {
            return Err("application writer is poisoned".into());
        }
        let ticket = state.next;
        let Some(next) = state.next.checked_add(1) else {
            state.poisoned = true;
            self.changed.notify_all();
            return Err("application writer arrival bound exhausted".into());
        };
        state.next = next;
        while ticket != state.serving && !state.poisoned {
            state = self
                .changed
                .wait(state)
                .map_err(|_| "application writer is poisoned")?;
        }
        if state.poisoned {
            return Err("application writer is poisoned".into());
        }
        Ok(WriteGuard {
            writer: self,
            ticket,
            thread: PhantomData,
        })
    }
}

impl Drop for WriteGuard<'_> {
    /// Releases only this original turn; unwinding fences all queued and future mutations.
    fn drop(&mut self) {
        let mut state = match self.writer.state.lock() {
            Ok(state) => state,
            Err(error) => {
                let mut state = error.into_inner();
                state.poisoned = true;
                state
            }
        };
        state.poisoned |= std::thread::panicking() || state.serving != self.ticket;
        state.serving = self.ticket.saturating_add(1);
        self.writer.changed.notify_all();
    }
}

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
    fn lock(&self) -> Result<WriteGuard<'_>, String>;
    /// Rechecks the original transport while the caller still holds its real writer guard.
    fn validate_locked(&self) -> Result<(), String> {
        Ok(())
    }
}

impl ApplicationWriteGate for ApplicationWriter {
    /// Supplies the existing writer ordering to trusted in-process startup/timer/Node inputs.
    fn lock(&self) -> Result<WriteGuard<'_>, String> {
        ApplicationWriter::lock(self)
    }
}

impl<G: ApplicationWriteGate + ?Sized> ApplicationWriteGate for Arc<G> {
    /// Preserves the wrapped authorization check when ownership moves into a blocking worker.
    fn lock(&self) -> Result<WriteGuard<'_>, String> {
        self.as_ref().lock()
    }
    /// Keeps the underlying witness check at a caller's final Commit boundary.
    fn validate_locked(&self) -> Result<(), String> {
        self.as_ref().validate_locked()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Real queued work must enter before the releasing thread can immediately reacquire authority.
    #[test]
    fn queued_original_writer_cannot_be_overtaken_by_hot_producer() {
        let writer = Arc::new(ApplicationWriter::default());
        let first = writer.lock().unwrap();
        let entered = Arc::new(Mutex::new(Vec::new()));
        let queued_writer = writer.clone();
        let queued_entered = entered.clone();
        let waiter = std::thread::spawn(move || {
            let _guard = queued_writer.lock().unwrap();
            queued_entered.lock().unwrap().push("original-waiter");
        });
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while writer.state.lock().unwrap().next != 2 {
            assert!(std::time::Instant::now() < deadline);
            std::thread::yield_now();
        }
        drop(first);
        let _later = writer.lock().unwrap();
        entered.lock().unwrap().push("hot-producer");
        waiter.join().unwrap();
        assert_eq!(
            *entered.lock().unwrap(),
            ["original-waiter", "hot-producer"]
        );
    }

    /// A real panic while holding the original turn cannot let another mutation continue.
    #[test]
    fn original_writer_panic_keeps_fail_closed_poison() {
        let writer = Arc::new(ApplicationWriter::default());
        let owned = writer.clone();
        assert!(
            std::thread::spawn(move || {
                let _guard = owned.lock().unwrap();
                panic!("explicit original writer failure");
            })
            .join()
            .is_err()
        );
        assert!(writer.lock().unwrap_err().contains("poisoned"));
        assert!(writer.lock().unwrap_err().contains("poisoned"));
    }

    /// Exhausted process-local arrival identity refuses rather than reusing an older writer turn.
    #[test]
    fn arrival_identity_never_wraps_into_an_existing_turn() {
        let writer = ApplicationWriter::default();
        writer.state.lock().unwrap().next = u64::MAX;
        assert!(writer.lock().unwrap_err().contains("bound exhausted"));
        assert!(writer.lock().unwrap_err().contains("poisoned"));
    }
}
