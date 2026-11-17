//! Bounded local source reads; expired work refuses admission without leaving background readers.

use std::fs::OpenOptions;
use std::io::{self, Read};
use std::path::Path;
use std::time::{Duration, Instant};

/// Existing authored Node configuration limit.
pub const MAX_CONFIG_SOURCE_BYTES: usize = 1024 * 1024;
/// Maximum supported local protobuf descriptor set.
pub const MAX_DESCRIPTOR_SOURCE_BYTES: usize = 16 * 1024 * 1024;
/// Same plan byte boundary as the Controller HTTP ingress.
pub const MAX_PLAN_SOURCE_BYTES: usize = 1024 * 1024;
/// Aggregate bytes across a single source compilation, including repeated source reads.
const MAX_BATCH_SOURCE_BYTES: usize = 256 * 1024 * 1024;
/// One original wall-clock admission budget for local source acquisition and compilation.
const SOURCE_BUDGET: Duration = Duration::from_secs(30);

/// Process-local admission budget, never persisted as permission or renewed between source files.
pub(crate) struct SourceBudget {
    /// Original deadline sampled once for the whole batch.
    deadline: Instant,
    /// Actual acquired bytes, including the first byte beyond an individual bound.
    acquired: usize,
}

impl SourceBudget {
    /// Starts one finite source admission without creating a reader task or worker.
    pub(crate) fn new() -> Self {
        Self {
            deadline: Instant::now() + SOURCE_BUDGET,
            acquired: 0,
        }
    }

    /// Refuses late results even when a preceding synchronous kernel operation was slow.
    pub(crate) fn check(&self) -> io::Result<()> {
        if Instant::now() >= self.deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "local source admission budget expired",
            ));
        }
        Ok(())
    }

    /// Opens only regular files, bounds actual bytes and checks original time before/after IO.
    pub(crate) fn read(&mut self, path: &Path, limit: usize) -> io::Result<Vec<u8>> {
        self.check()?;
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC);
        }
        // O_NONBLOCK prevents a FIFO open from waiting for a writer before the actual type check.
        let mut file = options.open(path)?;
        self.check()?;
        let before = file.metadata()?;
        if !before.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "local source must be a regular file",
            ));
        }
        if before.len() > limit as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "local source exceeds its byte bound",
            ));
        }
        let mut bytes = Vec::new();
        let mut chunk = [0_u8; 64 * 1024];
        loop {
            self.check()?;
            let remaining = (limit + 1).saturating_sub(bytes.len());
            let length = remaining.min(chunk.len());
            let received = file.read(&mut chunk[..length])?;
            self.check()?;
            self.acquired = self.acquired.checked_add(received).ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "source batch size overflow")
            })?;
            if self.acquired > MAX_BATCH_SOURCE_BYTES || bytes.len() + received > limit {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "local source exceeds its byte budget",
                ));
            }
            if received == 0 {
                break;
            }
            bytes.extend_from_slice(&chunk[..received]);
        }
        let after = file.metadata()?;
        self.check()?;
        if before.len() != after.len() || before.modified()? != after.modified()? {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "local source changed during acquisition",
            ));
        }
        Ok(bytes)
    }
}

/// Reads one explicit CLI plan through the same byte/time/type gates as authored sources.
pub fn read_plan_source(path: &Path) -> io::Result<Vec<u8>> {
    SourceBudget::new().read(path, MAX_PLAN_SOURCE_BYTES)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exact bounds accept, the first excess byte and already expired original budgets refuse.
    #[test]
    fn byte_type_and_original_time_bounds() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source");
        std::fs::write(&path, b"1234").unwrap();
        assert_eq!(SourceBudget::new().read(&path, 4).unwrap(), b"1234");
        assert!(SourceBudget::new().read(&path, 3).is_err());
        let mut expired = SourceBudget {
            deadline: Instant::now(),
            acquired: 0,
        };
        assert_eq!(
            expired.read(&path, 4).unwrap_err().kind(),
            io::ErrorKind::TimedOut
        );
        assert!(SourceBudget::new().read(directory.path(), 4).is_err());
    }

    /// Repeated files consume the same aggregate budget; a new read cannot reset it.
    #[test]
    fn aggregate_budget_survives_between_sources() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source");
        std::fs::write(&path, b"1234").unwrap();
        let mut budget = SourceBudget {
            deadline: Instant::now() + SOURCE_BUDGET,
            acquired: MAX_BATCH_SOURCE_BYTES - 4,
        };
        assert!(budget.read(&path, 4).is_ok());
        assert!(budget.read(&path, 4).is_err());
    }

    /// A real FIFO without a writer must refuse promptly, never block waiting for contents.
    #[cfg(unix)]
    #[test]
    fn fifo_never_waits_for_a_writer() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.fifo");
        assert!(
            std::process::Command::new("mkfifo")
                .arg(&path)
                .status()
                .unwrap()
                .success()
        );
        let start = Instant::now();
        assert!(SourceBudget::new().read(&path, 1024).is_err());
        assert!(start.elapsed() < Duration::from_secs(2));
    }
}
