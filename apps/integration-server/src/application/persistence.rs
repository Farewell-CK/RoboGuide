//! Controller writer fencing and checkpoint serialization.

use crate::*;
/// Acquires the process-wide single-writer lease for one controller event database.
///
/// The returned file must remain alive for the server lifetime. A second server using the same
/// database fails before it can replay a stale projection or append a conflicting event sequence.
pub(crate) fn acquire_event_log_writer_lock(
    event_path: &Path,
) -> Result<std::fs::File, std::io::Error> {
    let lock_path = event_log_lock_path(event_path)?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&lock_path)?;
    file.try_lock().map_err(|error| {
        std::io::Error::other(format!(
            "controller database {} is already owned by another Integration Server: {error}",
            event_path.display()
        ))
    })?;
    Ok(file)
}

/// Returns a canonical sibling lock path so relative and symlink aliases share one lease.
fn event_log_lock_path(event_path: &Path) -> Result<PathBuf, std::io::Error> {
    let canonical_event_path = if event_path.exists() {
        event_path.canonicalize()?
    } else {
        let file_name = event_path.file_name().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "controller database path must name a file",
            )
        })?;
        let parent = event_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        parent.canonicalize()?.join(file_name)
    };
    let mut lock_path = canonical_event_path.as_os_str().to_os_string();
    lock_path.push(".writer.lock");
    Ok(PathBuf::from(lock_path))
}
/// Serializes Integration and Mission orchestration into one versioned durable wrapper.
pub(crate) fn server_checkpoint_json(
    controller: &ControllerState,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let integration_json = controller
        .bridge
        .checkpoint_json()
        .map_err(|error| format!("integration checkpoint failure: {error}"))?;
    let integration_value: serde_json::Value = serde_json::from_str(&integration_json)
        .map_err(|error| format!("integration checkpoint JSON failure: {error}"))?;
    if integration_value
        .get("schema")
        .and_then(serde_json::Value::as_str)
        != Some(INTEGRATION_CHECKPOINT_SCHEMA)
    {
        return Err("Integration checkpoint schema changed unexpectedly".into());
    }
    let orchestration_json = controller
        .orchestrator
        .checkpoint_json()
        .map_err(|error| format!("orchestration checkpoint failure: {error}"))?;
    serde_json::to_string(&ServerCheckpoint {
        schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
        integration_json,
        orchestration_json,
    })
    .map_err(|error| format!("controller checkpoint wrapper failure: {error}").into())
}
