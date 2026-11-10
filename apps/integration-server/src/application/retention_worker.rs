//! One process-owned lossless hot-history archiver, separate from permanent expiry permission.

use super::{ApplicationWriter, archive_content::ContentArchiveSink};
use crate::*;
use sha2::{Digest as _, Sha256};

/// Frozen source lease and policy witness survive outside-writer publication as one operation.
type Prepared = (state::PreparedRetentionContentArchive, ContentArchiveSink);

/// Captures a bounded original prefix only when the committed administrator policy enables it.
fn prepare(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &ApplicationWriter,
    now: domain::TimestampMs,
) -> Result<(serde_json::Value, Option<Prepared>), String> {
    let _guard = gate.lock()?;
    let assessment = super::retention::assess(log, now)?;
    let relocation = log
        .retention_archive_status(now)
        .map_err(|error| error.to_string())?;
    let status = serde_json::json!({"retention":assessment,"archive_only":relocation,"permanent_expiry_enabled":false});
    if relocation.mode != state::RetentionMode::Enabled || relocation.end_sequence.is_none() {
        return Ok((status, None));
    }
    let root = std::env::var_os("ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY")
        .map(std::path::PathBuf::from)
        .filter(|path| path.is_absolute())
        .ok_or("enabled retention requires its original absolute archive directory")?;
    let source = {
        let live = controller
            .lock()
            .map_err(|_| "retention Controller mutex is poisoned")?;
        live.memory_preparation
            .catalog
            .as_ref()
            .ok_or("retention requires its original managed source Catalog")?
            .archive_source()?
    };
    let authority = log
        .archive_authority_id()
        .map_err(|error| error.to_string())?;
    let identity = serde_json::to_vec(&(
        authority.as_str(),
        relocation.policy_revision,
        relocation.start_sequence,
        relocation.end_sequence,
    ))
    .map_err(|error| error.to_string())?;
    let request = state::ArchiveRequest {
        command_id: format!("retention-{:x}", Sha256::digest(identity)),
        principal: format!("retention-policy:{}", relocation.policy_revision),
        tenant: "*".into(),
        source_owner: source
            .owner_id()
            .ok_or("retention source lacks original managed identity")?
            .into(),
        destination: root
            .to_str()
            .ok_or("retention archive path must be UTF-8")?
            .into(),
        authority_id: authority,
        budget_ms: 60_000,
    };
    let request = if let Some(original) = log
        .pending_archive_operation()
        .map_err(|error| error.to_string())?
    {
        if original.request.principal != request.principal
            || original.request.tenant != request.tenant
            || original.request.source_owner != request.source_owner
            || original.request.destination != request.destination
            || original.request.authority_id != request.authority_id
        {
            return Err(format!(
                "original pending archive {} requires explicit reconciliation; current policy cannot adopt its authority",
                original.request.command_id
            ));
        }
        original.request
    } else {
        request
    };
    let Some(prepared) = log
        .prepare_retention_content_archive(now, relocation.policy_revision, request)
        .map_err(|error| error.to_string())?
    else {
        return Ok((status, None));
    };
    let witness = prepared.policy_witness();
    let availability = log.clone();
    let permission = Arc::new(move || {
        if !availability
            .write_status()
            .map_err(|error| error.to_string())?
            .write_enabled
        {
            return Err("retention source writer is unavailable".into());
        }
        witness.require_current().map_err(|error| error.to_string())
    });
    Ok((
        status,
        Some((prepared, ContentArchiveSink::new(source, permission))),
    ))
}

/// Performs exactly one bounded serial cycle; publication and verification never hold the writer.
pub(crate) async fn cycle(
    controller: Arc<Mutex<ControllerState>>,
    log: state::SqliteEventLog,
    gate: Arc<ApplicationWriter>,
    clock: Arc<runtime::SystemMonotonicClock>,
) -> Result<serde_json::Value, String> {
    let source_log = log.clone();
    let source_gate = gate.clone();
    let source_clock = clock.clone();
    let (mut status, prepared) = tokio::task::spawn_blocking(move || {
        prepare(&controller, &source_log, &source_gate, source_clock.now())
    })
    .await
    .map_err(|error| error.to_string())??;
    let Some((prepared, sink)) = prepared else {
        return Ok(status);
    };
    let (published, sink) = tokio::task::spawn_blocking(move || {
        let published = state::SqliteEventLog::publish_retention_content_archive(prepared, &sink)
            .map_err(|error| error.to_string())?;
        Ok::<_, String>((published, sink))
    })
    .await
    .map_err(|error| error.to_string())??;
    let manifest = tokio::task::spawn_blocking(move || {
        let _guard = gate.lock()?;
        log.finish_retention_content_archive(published, &sink, clock.now())
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())??;
    status["completed_archive"] =
        serde_json::to_value(manifest).map_err(|error| error.to_string())?;
    Ok(status)
}
