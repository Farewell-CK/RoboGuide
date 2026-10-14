//! Single-owned-child lifecycle and conservative atomic accepted-view materialization.

use crate::config::ProcessConfig;
use controller_replication::{ControllerEpoch, ReplicaNode, ReplicaView};
use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::{Child, Command};

/// Observable local status only; readers cannot use it to grant Control commitment or delivery.
#[derive(Serialize)]
struct Status {
    /// Immutable cluster/application identity.
    profile: String,
    /// Latest locally confirmed leader epoch, absent without current majority.
    epoch: Option<ControllerEpoch>,
    /// Only the child owned by this supervisor.
    child_pid: Option<u32>,
    /// Complete application revision already applied at this replica.
    revision: u64,
}

/// Owns exactly one child and the epoch under which it was originally started.
struct OwnedController {
    /// Issuing epoch never renewed in a living child.
    epoch: ControllerEpoch,
    /// Owned process handle; drop requests termination.
    child: Child,
}

/// Locks a private generated file without finding/killing any external process.
fn writer_lease(path: &Path) -> Result<File, String> {
    let lease = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)
        .map_err(|error| error.to_string())?;
    lease
        .try_lock()
        .map_err(|error| format!("replica-owned path is already in use: {error}"))?;
    Ok(lease)
}

/// Replaces only the inactive owned projection after fully validating and flushing a fresh database.
fn materialize(directory: &Path, view: &ReplicaView) -> Result<(), String> {
    let _lease = writer_lease(&directory.join("controller.sqlite3.writer.lock"))?;
    let temporary = directory.join("controller.materializing.sqlite3");
    for suffix in ["", "-wal", "-shm"] {
        let path = directory.join(format!("controller.materializing.sqlite3{suffix}"));
        if path.exists() {
            std::fs::remove_file(path).map_err(|error| error.to_string())?;
        }
    }
    {
        let log = state::SqliteEventLog::open(&temporary).map_err(|error| error.to_string())?;
        if let Some(checkpoint) = &view.checkpoint {
            log.install_replica_authority(
                &view.events,
                checkpoint,
                view.history_anchor
                    .as_ref()
                    .map(|anchor| anchor.base_sequence),
                &view.commands,
            )
            .map_err(|error| error.to_string())?;
        } else if !view.events.is_empty() {
            return Err("replica materialization lacks its accepted checkpoint".into());
        }
    }
    File::open(&temporary)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())?;
    for suffix in ["-wal", "-shm"] {
        let path = directory.join(format!("controller.sqlite3{suffix}"));
        if path.exists() {
            std::fs::remove_file(path).map_err(|error| error.to_string())?;
        }
    }
    std::fs::rename(&temporary, directory.join("controller.sqlite3"))
        .map_err(|error| error.to_string())?;
    File::open(directory)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())?;
    Ok(())
}

/// Starts only the validated binary with an immutable parent fence and explicit cleared policy environment.
fn start_controller(
    configuration: &ProcessConfig,
    node: &ReplicaNode,
    epoch: ControllerEpoch,
) -> Result<OwnedController, String> {
    let directory = &configuration.storage_directory;
    let output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join(format!("controller-term-{}.log", epoch.term)))
        .map_err(|error| error.to_string())?;
    let error_output = output.try_clone().map_err(|error| error.to_string())?;
    let mut command = Command::new(&configuration.controller_binary);
    command
        .env_clear()
        .envs(&configuration.controller_environment)
        .env(
            "ROBOGUIDE_REPLICA_ENDPOINT",
            node.local_address().to_string(),
        )
        .env("ROBOGUIDE_REPLICA_PROFILE", node.profile_identity())
        .env(
            "ROBOGUIDE_REPLICA_EPOCH",
            serde_json::to_string(&epoch).map_err(|error| error.to_string())?,
        )
        .arg(configuration.grpc_address.to_string())
        .arg(directory.join("controller.sqlite3"))
        .arg(configuration.http_address.to_string())
        .arg(configuration.artifact_address.to_string())
        .arg(directory.join("artifacts"))
        .arg(
            configuration
                .actor_placement_path
                .as_deref()
                .unwrap_or(Path::new("")),
        )
        .arg(
            configuration
                .physical_entity_registry_path
                .as_deref()
                .unwrap_or(Path::new("")),
        )
        .stdin(Stdio::null())
        .stdout(output)
        .stderr(error_output)
        .kill_on_drop(true);
    if let Some(secret) = &configuration.replica.shared_secret {
        command.env("ROBOGUIDE_REPLICA_SHARED_SECRET", secret);
    }
    if let Some(tls) = &configuration.replica.tls {
        command
            .env("ROBOGUIDE_REPLICA_TLS_CA", &tls.ca_cert_path)
            .env("ROBOGUIDE_REPLICA_TLS_CERT", &tls.cert_path)
            .env("ROBOGUIDE_REPLICA_TLS_KEY", &tls.key_path)
            .env("ROBOGUIDE_REPLICA_TLS_SERVER_NAME", &tls.server_name)
            .env(
                "ROBOGUIDE_REPLICA_TLS_PEER_DIGESTS",
                serde_json::to_string(&tls.peer_certificate_digests)
                    .map_err(|error| error.to_string())?,
            );
    }
    let child = command.spawn().map_err(|error| error.to_string())?;
    eprintln!(
        "Controller child {} started under epoch {epoch:?}",
        child.id().unwrap_or(0)
    );
    Ok(OwnedController { epoch, child })
}

/// Terminates and reaps only this supervisor's child; physical work and Node journals remain untouched.
async fn stop_controller(owned: &mut Option<OwnedController>) -> Result<(), String> {
    if let Some(mut controller) = owned.take() {
        if controller
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            controller
                .child
                .start_kill()
                .map_err(|error| error.to_string())?;
        }
        tokio::time::timeout(Duration::from_secs(5), controller.child.wait())
            .await
            .map_err(|_| "owned Controller exit timed out".to_string())?
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

/// Publishes bounded local status atomically; it is not an authority or failover API.
fn write_status(
    configuration: &ProcessConfig,
    node: &ReplicaNode,
    epoch: Option<ControllerEpoch>,
    owned: &Option<OwnedController>,
) -> Result<(), String> {
    let status = Status {
        profile: node.profile_identity(),
        epoch,
        child_pid: owned.as_ref().and_then(|controller| controller.child.id()),
        revision: node.accepted_revision()?,
    };
    let temporary = configuration.storage_directory.join("status.pending.json");
    std::fs::write(
        &temporary,
        serde_json::to_vec(&status).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    std::fs::rename(
        temporary,
        configuration.storage_directory.join("status.json"),
    )
    .map_err(|error| error.to_string())
}

/// Runs consensus continuously while only current quorum leadership retains one owned child.
pub(super) async fn run(configuration: ProcessConfig) -> Result<(), String> {
    std::fs::create_dir_all(&configuration.storage_directory).map_err(|error| error.to_string())?;
    let _lease = writer_lease(
        &configuration
            .storage_directory
            .join("supervisor.writer.lock"),
    )?;
    let (node, _server) = ReplicaNode::start(
        configuration.replica.clone(),
        &configuration.storage_directory.join("replica.sqlite3"),
    )
    .await?;
    let mut owned = None;
    let mut timer = tokio::time::interval(Duration::from_millis(200));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let result = loop {
        tokio::select! {
            result = tokio::signal::ctrl_c() => { break result.map_err(|error| error.to_string()); }
            _ = timer.tick() => {
                let current = match node.current_epoch() {
                    Some(epoch) => match node.check_epoch(epoch).await {
                        Ok(()) => Some(epoch),
                        Err(error) => { if owned.is_some() { eprintln!("Controller quorum fence: {error}"); } None }
                    },
                    None => None,
                };
                let child_dead = match owned.as_mut() {
                    Some(OwnedController { child, .. }) => child.try_wait().map_err(|error| error.to_string())?.is_some(),
                    None => false,
                };
                if child_dead || owned.as_ref().is_some_and(|controller| Some(controller.epoch) != current) {
                    eprintln!("Stopping owned Controller: exited={child_dead}, current_epoch={current:?}");
                    stop_controller(&mut owned).await?;
                }
                if owned.is_none() && let Some(epoch) = current {
                    configuration.validate()?;
                    materialize(&configuration.storage_directory, &node.accepted_view()?)?;
                    if node.check_epoch(epoch).await.is_ok() {
                        owned = Some(start_controller(&configuration, &node, epoch)?);
                    }
                }
                write_status(&configuration, &node, current, &owned)?;
            }
        }
    };
    stop_controller(&mut owned).await?;
    node.shutdown().await?;
    result
}
