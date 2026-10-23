//! Actual owned software-file source through the maintained State port; no Node/device inventory.

use artifact_store::{
    ArtifactUpload, FileSystemArtifactStore, PreparedArtifactSourceGuard, digest_bytes,
};
use controller_replication::{ControllerContentClient, ControllerContentClientConfig};
use domain::{ArtifactUploadPhase, ArtifactUploadRecord, CorrelationId, EventPayload, TimestampMs};
use ports::EventSink;
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{BufRead, Read, Write};
use std::path::PathBuf;
use std::sync::Arc;

/// Explicit actual source and transport, independent of real devices and application deployment.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    /// Original actual supervisor transport; its actual PID is independently kernel-observed.
    client: ControllerContentClientConfig,
    /// Actual managed original source CAS.
    source_cas: PathBuf,
    /// Actual original local software-contract SQL.
    source_sql: PathBuf,
}

/// Closed software-only operations; these never install a device or claim a complete Controller kernel.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Require actual original process and current complete content quorum.
    Fence,
    /// Persist one original Started upload through the normal State replication port.
    Start {
        /// Explicit original path-safe upload identity.
        upload_id: String,
    },
    /// Supply actual staged bytes, retaining Prepared scope without finalized public CAS.
    Prepare {
        /// Actual original file bytes, bounded independently of caller metadata.
        payload: PathBuf,
    },
    /// Finalize the original file and persist Completed through the same actual port.
    Complete,
    /// Read actual source/SQL observations without granting work.
    Inspect,
    /// Revoke only the actual nonpersistent Prepared source scope.
    Revoke,
    /// Repeat a retained original ID solely to verify the readonly duplicate path.
    Replay {
        /// Exact original operation retained in parent8.
        command_id: String,
    },
    /// End only this actual software process.
    Shutdown,
}

/// Emits only bounded actual observations with this original OS PID.
fn emit(mut value: Value) -> Result<(), String> {
    value
        .as_object_mut()
        .ok_or("actual source observation must be an object")?
        .insert("pid".into(), json!(std::process::id()));
    println!("{value}");
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Reads actual local wall time without borrowing another process's timestamp or renewing a record.
fn now() -> Result<u64, String> {
    u64::try_from(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|error| error.to_string())?
            .as_millis(),
    )
    .map_err(|error| error.to_string())
}

/// Commits an actual original typed file record; its opaque checkpoint is explicitly a contract fixture.
fn append(
    log: &state::SqliteEventLog,
    record: &ArtifactUploadRecord,
    epoch: &str,
) -> Result<(), String> {
    let phase = match record.phase {
        ArtifactUploadPhase::Started => "started",
        ArtifactUploadPhase::Prepared => "prepared",
        ArtifactUploadPhase::Completed => "completed",
        ArtifactUploadPhase::Abandoned => "abandoned",
    };
    let checkpoint = json!({"owned_software_file_contract":phase}).to_string();
    let command = format!("native-file-{phase}:{}", record.upload_id);
    log.begin_batch().map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        let mut writer = log.clone();
        writer.append(
            TimestampMs::new(now()?),
            &CorrelationId::new(&record.upload_id).map_err(|error| error.to_string())?,
            None,
            EventPayload::ArtifactUploadTransition {
                record: record.clone(),
            },
        );
        if let Some(error) = log.take_error().map_err(|error| error.to_string())? {
            return Err(error.to_string());
        }
        let sequence = log
            .current_batch_event_sequence()
            .map_err(|error| error.to_string())?;
        log.save_checkpoint_with_command(
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            &checkpoint,
            state::ControlCommandJournalEntry {
                command_id: command,
                causation_id: None,
                authority_revision: 0,
                controller_epoch: Some(epoch.into()),
                plan_digest: None,
                input_digest: digest_bytes(
                    &serde_json::to_vec(record).map_err(|error| error.to_string())?,
                ),
                ownership_digest: None,
                resulting_state_digest: digest_bytes(checkpoint.as_bytes()),
                checkpoint_event_sequence: sequence,
                checkpoint_schema: state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
            },
        )
        .map_err(|error| error.to_string())?;
        log.commit_batch().map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ = log.rollback_batch();
    }
    result
}

/// Owns real staged FD/guard/SQL through ordinary State commits and leaves actual death unreconstructed.
fn run(config: Config) -> Result<(), String> {
    let epoch = serde_json::to_string(&config.client.epoch).map_err(|error| error.to_string())?;
    let source = FileSystemArtifactStore::new_with_archive_references(&config.source_cas)
        .map_err(|error| error.to_string())?;
    let client = Arc::new(ControllerContentClient::new(config.client, source.clone())?);
    let log = state::SqliteEventLog::open(&config.source_sql)
        .map_err(|error| error.to_string())?
        .with_replicator(client.clone());
    let mut record: Option<ArtifactUploadRecord> = None;
    for event in log.decoded_events().map_err(|error| error.to_string())? {
        if let EventPayload::ArtifactUploadTransition { record: original } = event.payload() {
            record = Some(original.clone());
        }
    }
    let mut upload: Option<ArtifactUpload> = None;
    let mut guard: Option<PreparedArtifactSourceGuard> = None;
    emit(json!({"ready":true,"source_owner":source.owner_id()}))?;
    let mut input = std::io::stdin().lock();
    loop {
        let mut raw = Vec::new();
        (&mut input)
            .take(1024 * 1024 + 1)
            .read_until(b'\n', &mut raw)
            .map_err(|error| error.to_string())?;
        if raw.is_empty() || raw.len() > 1024 * 1024 {
            return Err("original native request ended or exceeded its bound".into());
        }
        let request: Request = serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
        let shutdown = matches!(request, Request::Shutdown);
        let result = (|| -> Result<Value, String> {
            match request {
                Request::Fence => Ok(json!({"process":client.check()?})),
                Request::Start { upload_id } => {
                    if record.is_some() {
                        return Err("original file contract already exists".into());
                    }
                    let admitted = now()?;
                    let original = ArtifactUploadRecord {
                        upload_id: upload_id.clone(),
                        principal_id: "explicit-native-file-contract".into(),
                        tenant_id: "native-file-contract".into(),
                        original_certificate: "explicit-software-contract-fixture".into(),
                        admitted_at_ms: admitted,
                        deadline_ms: admitted.checked_add(300000).ok_or("budget overflow")?,
                        phase: ArtifactUploadPhase::Started,
                        content_digest: None,
                        byte_size: None,
                        prepared_at_ms: None,
                        completed_at_ms: None,
                    };
                    append(&log, &original, &epoch)?;
                    upload = Some(
                        source
                            .begin_upload(upload_id)
                            .map_err(|error| error.to_string())?,
                    );
                    record = Some(original.clone());
                    Ok(json!({"record":original}))
                }
                Request::Prepare { payload } => {
                    let mut original = record
                        .clone()
                        .filter(|record| record.phase == ArtifactUploadPhase::Started)
                        .ok_or("original Started upload absent")?;
                    let file = std::fs::File::open(payload).map_err(|error| error.to_string())?;
                    let metadata = file.metadata().map_err(|error| error.to_string())?;
                    if !metadata.is_file() || metadata.len() > 8 * 1024 * 1024 {
                        return Err("native payload exceeds regular-file bound".into());
                    }
                    let mut bytes = Vec::new();
                    file.take(8 * 1024 * 1024 + 1)
                        .read_to_end(&mut bytes)
                        .map_err(|error| error.to_string())?;
                    if bytes.len() as u64 != metadata.len() {
                        return Err("actual original payload changed".into());
                    }
                    let original_upload = upload.as_mut().ok_or("original upload FD absent")?;
                    for chunk in bytes.chunks(64 * 1024) {
                        original_upload
                            .write_chunk(chunk)
                            .map_err(|error| error.to_string())?;
                    }
                    let digest = digest_bytes(&bytes);
                    guard = Some(
                        original_upload
                            .prepared_replication_source(&digest, bytes.len() as u64)
                            .map_err(|error| error.to_string())?,
                    );
                    original.phase = ArtifactUploadPhase::Prepared;
                    original.content_digest = Some(digest.clone());
                    original.byte_size = Some(bytes.len() as u64);
                    original.prepared_at_ms = Some(now()?);
                    append(&log, &original, &epoch)?;
                    record = Some(original.clone());
                    Ok(
                        json!({"record":original,"public_cas":source.contains(&digest).map_err(|error|error.to_string())?}),
                    )
                }
                Request::Complete => {
                    let mut original = record
                        .clone()
                        .filter(|record| record.phase == ArtifactUploadPhase::Prepared)
                        .ok_or("original Prepared upload absent")?;
                    upload
                        .as_mut()
                        .ok_or("original upload FD absent")?
                        .finalize(
                            original
                                .content_digest
                                .as_deref()
                                .ok_or("original digest absent")?,
                            original.byte_size.ok_or("original byte size absent")?,
                        )
                        .map_err(|error| error.to_string())?;
                    original.phase = ArtifactUploadPhase::Completed;
                    original.completed_at_ms = Some(now()?);
                    append(&log, &original, &epoch)?;
                    record = Some(original.clone());
                    drop(guard.take());
                    Ok(json!({"record":original,"public_cas":true}))
                }
                Request::Inspect => Ok(
                    json!({"record":record,"events":log.len().map_err(|error|error.to_string())?,
                "view":client.view()?}),
                ),
                Request::Revoke => {
                    drop(guard.take());
                    Ok(json!({"revoked":true}))
                }
                Request::Replay { command_id } => {
                    let original = client
                        .observe(&command_id)?
                        .ok_or("original source command absent")?;
                    let candidate =
                        serde_json::from_value(original.original["intent"]["candidate"].clone())
                            .map_err(|error| error.to_string())?;
                    Ok(json!({"original":client.submit(candidate,&command_id)?}))
                }
                Request::Shutdown => Ok(json!({"shutdown_requested":true})),
            }
        })();
        emit(match result {
            Ok(result) => json!({"result":result}),
            Err(error) => json!({"error":error}),
        })?;
        if shutdown {
            break;
        }
    }
    Ok(())
}

/// Parses only one bounded original actual configuration and retains honest process failure codes.
fn main() {
    let result = (|| -> Result<(), String> {
        let path = std::env::args()
            .nth(1)
            .ok_or("explicit original config required")?;
        let mut raw = Vec::new();
        std::fs::File::open(path)
            .map_err(|error| error.to_string())?
            .take(1024 * 1024 + 1)
            .read_to_end(&mut raw)
            .map_err(|error| error.to_string())?;
        if raw.len() > 1024 * 1024 {
            return Err("original config exceeds its bound".into());
        }
        run(serde_json::from_slice(&raw).map_err(|error| error.to_string())?)
    })();
    if let Err(error) = result {
        let _ = emit(json!({"error":error}));
        std::process::exit(1);
    }
}
