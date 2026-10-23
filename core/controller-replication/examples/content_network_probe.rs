//! Actual infrastructure network content processes; no embodied Nodes, devices or execution inventory.

use controller_replication::*;
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};

/// Explicit actual existing roots and infrastructure profile; no missing root is implicitly created.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    /// Actual full-content infrastructure declaration with real TLS material.
    replica: ReplicaConfig,
    /// Actual original existing private Root4.
    content_root: PathBuf,
    /// Actual original existing parent or declared fresh parent7 file.
    parent_sql: PathBuf,
    /// Declared original member roots, independently matched with the actual local opened root.
    member_stores: BTreeMap<u64, String>,
}

/// Closed trusted local process operations; no arbitrary SQL, shell or fabricated execution is accepted.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Observes actual current role and quorum without mutation.
    Status,
    /// Reads a quiescent actual original Controller/CAS and stages its exact complete candidate.
    Capture {
        /// Actual stopped original Controller SQL, protected by its actual writer leases.
        source_sql: PathBuf,
        /// Actual original Artifact writer root.
        source_cas: PathBuf,
        /// Explicit immutable original storage staging command.
        operation_id: String,
    },
    /// Commits the exact retained candidate through actual metadata and same-member bytes quorum.
    Commit,
    /// Reads actual current accepted metadata and independently hashes actual complete member bytes.
    Quorum,
    /// Reopens each exact target only through the actual original observation path.
    Observe {
        /// Explicit original complete target; parsing grants no authority.
        target: Value,
    },
    /// Stops only this owned infrastructure service; no device stopping is inferred.
    Shutdown,
}

/// Emits bounded structured observations and the actual process identity; stdout grants no authority.
fn emit(value: Value) -> Result<(), String> {
    println!(
        "{}",
        serde_json::to_string(&value).map_err(|error| error.to_string())?
    );
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Acquires both actual original Controller leases before a complete readonly snapshot.
fn source_leases(path: &Path) -> Result<(File, File), String> {
    let path = path.canonicalize().map_err(|error| error.to_string())?;
    let mut sibling = path.as_os_str().to_os_string();
    sibling.push(".writer.lock");
    let first = OpenOptions::new()
        .read(true)
        .write(true)
        .open(Path::new(&sibling))
        .map_err(|error| error.to_string())?;
    first
        .try_lock()
        .map_err(|error| format!("actual original Controller writer is active: {error}"))?;
    let second = OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(|error| error.to_string())?;
    second
        .try_lock()
        .map_err(|error| format!("actual original Controller inode is active: {error}"))?;
    Ok((first, second))
}

/// Captures only actual original SQL/CAS under their real owners; never creates an application event.
fn capture(
    source_sql: &Path,
    source_cas: &Path,
    previous: &ReplicaView,
    profile: &str,
    epoch: ControllerEpoch,
) -> Result<
    (
        ContentApplicationBatch,
        artifact_store::FileSystemArtifactStore,
    ),
    String,
> {
    let _leases = source_leases(source_sql)?;
    let original = state::SqliteEventLog::read_complete_replica_state(source_sql)
        .map_err(|error| error.to_string())?;
    let source = artifact_store::FileSystemArtifactStore::new_with_archive_references(source_cas)
        .map_err(|error| error.to_string())?;
    let sequence = previous
        .checkpoint
        .as_ref()
        .map_or(0, |checkpoint| checkpoint.event_sequence);
    let revision = previous
        .commands
        .last()
        .map_or(0, |command| command.authority_revision);
    let batch = state::ApplicationCommitBatch {
        schema: state::APPLICATION_COMMIT_SCHEMA.into(),
        previous_event_sequence: sequence,
        previous_checkpoint_digest: previous.checkpoint.as_ref().map(state::checkpoint_identity),
        checkpoint: original.checkpoint,
        events: original
            .events
            .into_iter()
            .filter(|event| event.sequence > sequence)
            .collect(),
        commands: original
            .commands
            .into_iter()
            .filter(|command| command.authority_revision > revision)
            .collect(),
    };
    let candidate = ContentApplicationBatch::from_source(
        profile,
        epoch,
        &source,
        previous,
        batch,
        original.recovery,
    )?;
    Ok((candidate, source))
}

/// Serves actual Raft/mTLS while owned local stdin requests drive only maintained core interfaces.
async fn serve(config: Config) -> Result<(), String> {
    let root = ReplicaContentStore::open_with_streams(
        &config.content_root,
        &config.replica.profile_identity(),
        config.replica.replica_id,
    )?;
    let (node, _server) = ReplicaNode::start_with_content(
        config.replica,
        &config.parent_sql,
        root.clone(),
        config.member_stores,
        ReplicaTransportLimits::default(),
    )
    .await?;
    emit(json!({"ready":true,"pid":std::process::id(),"identity":root.identity()}))?;
    let mut pending = None;
    let input = std::io::stdin();
    let mut input = input.lock();
    loop {
        let mut raw = Vec::new();
        let count = (&mut input)
            .take(16 * 1024 * 1024 + 1)
            .read_until(b'\n', &mut raw)
            .map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        if raw.len() > 16 * 1024 * 1024 {
            return Err("original process request exceeds its fixed bound".into());
        }
        let request: Request = serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
        let shutdown = matches!(request, Request::Shutdown);
        let result = match request {
            Request::Status => {
                let epoch = node.current_epoch();
                let quorum = match epoch {
                    Some(epoch) => node.check_epoch(epoch).await.is_ok(),
                    None => false,
                };
                Ok(json!({"epoch":epoch,"quorum":quorum,"identity":root.identity()}))
            }
            Request::Capture {
                source_sql,
                source_cas,
                operation_id,
            } => {
                let current = node.clone();
                let prepared = tokio::task::spawn_blocking(move || {
                    capture(
                        &source_sql,
                        &source_cas,
                        &current.accepted_view()?,
                        &current.profile_identity(),
                        current.current_epoch().ok_or("no actual current leader")?,
                    )
                })
                .await
                .map_err(|error| error.to_string())?;
                match prepared {
                    Err(error) => Err(error),
                    Ok((candidate, source)) => match node
                        .stage_content_from_source(&candidate, &source, &operation_id, 300000)
                        .await
                    {
                        Err(error) => Err(error),
                        Ok(target) => {
                            let identity = candidate.identity()?;
                            pending = Some(candidate);
                            Ok(json!({"target":target,"candidate_digest":identity}))
                        }
                    },
                }
            }
            Request::Commit => match &pending {
                Some(candidate) => node
                    .commit_content(candidate.clone())
                    .await
                    .map(|receipt| json!({"receipt":receipt})),
                None => Err("original retained candidate is unavailable".into()),
            },
            Request::Quorum => match node.accepted_content_target().await {
                Err(error) => Err(error),
                Ok(target) => node
                    .content_quorum(target.clone())
                    .await
                    .map(|members| json!({"target":target,"complete_members":members})),
            },
            Request::Observe { target } => match serde_json::from_value(target) {
                Err(error) => Err(error.to_string()),
                Ok(target) => node
                    .observe_content_target(target)
                    .await
                    .map(|actual| json!({"observation":actual})),
            },
            Request::Shutdown => Ok(json!({"shutdown_requested":true})),
        };
        emit(match result {
            Ok(result) => json!({"pid":std::process::id(),"result":result}),
            Err(error) => json!({"pid":std::process::id(),"error":error}),
        })?;
        if shutdown {
            break;
        }
    }
    node.shutdown().await?;
    Ok(())
}

/// Runs only an explicit bounded profile inspection or owned infrastructure service.
#[tokio::main(worker_threads = 1)]
async fn main() -> Result<(), String> {
    let mut arguments = std::env::args().skip(1);
    let mode = arguments
        .next()
        .ok_or("explicit profile/serve operation required")?;
    if mode == "schema" {
        if arguments.next().is_some() {
            return Err("schema observation accepts no storage or profile".into());
        }
        return emit(json!({"schema":state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA}));
    }
    let path = arguments
        .next()
        .ok_or("explicit closed configuration required")?;
    if arguments.next().is_some() {
        return Err("unexpected process arguments".into());
    }
    let mut raw = Vec::new();
    File::open(path)
        .map_err(|error| error.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    if raw.len() > 1024 * 1024 {
        return Err("original network configuration exceeds its fixed bound".into());
    }
    let config: Config = serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
    config.replica.validate()?;
    match mode.as_str() {
        "profile" => emit(json!({"profile":config.replica.profile_identity()})),
        "serve" => serve(config).await,
        _ => Err("unsupported actual network process operation".into()),
    }
}
