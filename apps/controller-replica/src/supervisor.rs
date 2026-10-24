//! Single-owned-child lifecycle and conservative atomic accepted-view materialization.

use crate::config::ProcessConfig;
use crate::projection::materialize;
use controller_replication::{
    ControllerEpoch, ControllerProcessExit, ControllerProcessIdentity, ReplicaNode,
};
use serde::Serialize;
use serde_json::json;
use std::fs::{File, OpenOptions};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::{Child, Command};

/// Observable local status only; readers cannot use it to grant Control commitment or delivery.
#[derive(Serialize, serde::Deserialize)]
pub(super) struct Status {
    /// Actual supervisor's local status publication time, never a source/State freshness receipt.
    pub(super) observed_unix_ms: u64,
    /// Immutable cluster/application identity.
    profile: String,
    /// Latest locally confirmed leader epoch, absent without current majority.
    pub(super) epoch: Option<ControllerEpoch>,
    /// Only the child owned by this supervisor.
    pub(super) child_pid: Option<u32>,
    /// Complete application revision already applied at this replica.
    pub(super) revision: u64,
    /// Actual local transport permits and counters, unavailable in older observation files.
    #[serde(default)]
    pub(super) transport: Option<controller_replication::ReplicaTransportSnapshot>,
    /// Bounded actual consensus/storage failure; absence is not readiness or complete content proof.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) raft_error: Option<String>,
}

/// Owns exactly one child and the epoch under which it was originally started.
struct OwnedController {
    /// Issuing epoch never renewed in a living child.
    epoch: ControllerEpoch,
    /// Owned process handle; drop requests termination.
    child: Child,
    /// Complete original PID identity registered before the owned handle becomes live authority.
    process: ControllerProcessIdentity,
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

/// Starts only the validated binary with an immutable parent fence and explicit cleared policy environment.
async fn start_controller(
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
    let original = node.begin_controller_process(epoch).await?;
    if let Some(content) = &configuration.content {
        let client = controller_replication::ControllerContentClientConfig {
            socket: content.socket.clone(),
            profile: node.profile_identity(),
            epoch,
            instance: original.clone(),
            tls: configuration
                .replica
                .tls
                .clone()
                .ok_or("original content child requires actual TLS")?,
            trust_database: directory.join("replica.sqlite3"),
            budget_ms: content.child_budget_ms,
        };
        command.env(
            "ROBOGUIDE_REPLICA_CONTENT_CONFIG",
            serde_json::to_string(&client).map_err(|error| error.to_string())?,
        );
    } else {
        command
            .env(
                "ROBOGUIDE_REPLICA_ENDPOINT",
                node.local_address().to_string(),
            )
            .env("ROBOGUIDE_REPLICA_PROFILE", node.profile_identity())
            .env(
                "ROBOGUIDE_REPLICA_EPOCH",
                serde_json::to_string(&epoch).map_err(|error| error.to_string())?,
            );
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
        if configuration.replica.rotates_certificates() {
            command.env(
                "ROBOGUIDE_REPLICA_TRUST_DATABASE",
                configuration.storage_directory.join("replica.sqlite3"),
            );
        }
    }
    let mut child = command.spawn().map_err(|error| error.to_string())?;
    let process = match node.register_controller_process(
        epoch,
        original,
        child.id().ok_or("actual owned Controller PID is absent")?,
    ) {
        Ok(process) => process,
        Err(error) => {
            let _ = child.start_kill();
            let _ = child.wait().await;
            return Err(error);
        }
    };
    eprintln!(
        "Controller child {} started under epoch {epoch:?}",
        child.id().unwrap_or(0)
    );
    Ok(OwnedController {
        epoch,
        child,
        process,
    })
}

/// Terminates and reaps only this supervisor's child; physical work and Node journals remain untouched.
async fn stop_controller(
    owned: &mut Option<OwnedController>,
    node: &ReplicaNode,
) -> Result<(), String> {
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
        let status = tokio::time::timeout(Duration::from_secs(5), controller.child.wait())
            .await
            .map_err(|_| "owned Controller exit timed out".to_string())?
            .map_err(|error| error.to_string())?;
        #[cfg(unix)]
        let signal = {
            use std::os::unix::process::ExitStatusExt;
            status.signal()
        };
        #[cfg(not(unix))]
        let signal = None;
        node.record_controller_exit(ControllerProcessExit {
            process: controller.process,
            exit_code: status.code(),
            signal,
        })?;
    }
    Ok(())
}

/// Publishes bounded local status atomically; it is not an authority or failover API.
async fn write_status(
    configuration: &ProcessConfig,
    node: &ReplicaNode,
    epoch: Option<ControllerEpoch>,
    owned: &Option<OwnedController>,
) -> Result<(), String> {
    let node = node.clone();
    let directory = configuration.storage_directory.clone();
    let child_pid = owned.as_ref().and_then(|controller| controller.child.id());
    tokio::task::spawn_blocking(move || {
        let status = Status {
            observed_unix_ms: crate::metrics::unix_ms()?,
            profile: node.profile_identity(),
            epoch,
            child_pid,
            revision: node.accepted_revision()?,
            transport: Some(node.transport_snapshot()),
            raft_error: node.infrastructure_error(),
        };
        let temporary = directory.join("status.pending.json");
        std::fs::write(
            &temporary,
            serde_json::to_vec(&status).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        std::fs::rename(temporary, directory.join("status.json")).map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Runs consensus continuously while only current quorum leadership retains one owned child.
pub(super) async fn run(configuration: ProcessConfig) -> Result<(), String> {
    if configuration.content.is_some() {
        use std::os::unix::fs::{DirBuilderExt, MetadataExt};
        if !configuration.storage_directory.exists() {
            std::fs::DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(&configuration.storage_directory)
                .map_err(|error| error.to_string())?;
        }
        let actual = std::fs::symlink_metadata(&configuration.storage_directory)
            .map_err(|error| error.to_string())?;
        if !actual.is_dir()
            || actual.mode() & 0o077 != 0
            || configuration
                .storage_directory
                .canonicalize()
                .map_err(|error| error.to_string())?
                != configuration.storage_directory
        {
            return Err("original content supervisor requires an actual canonical private storage directory".into());
        }
    } else {
        std::fs::create_dir_all(&configuration.storage_directory)
            .map_err(|error| error.to_string())?;
    }
    let _lease = writer_lease(
        &configuration
            .storage_directory
            .join("supervisor.writer.lock"),
    )?;
    let (node, _server) = if let Some(content) = &configuration.content {
        let root = if configuration.schema == "roboguide.controller-replica-process/v0.6" {
            controller_replication::ReplicaContentStore::open_with_staging_gc(
                &content.root,
                &configuration.replica.profile_identity(),
                configuration.replica.replica_id,
                content.restore_binding_digest.as_deref(),
            )
        } else if configuration.schema == "roboguide.controller-replica-process/v0.5" {
            controller_replication::ReplicaContentStore::open_with_reclamation(
                &content.root,
                &configuration.replica.profile_identity(),
                configuration.replica.replica_id,
                content.restore_binding_digest.as_deref(),
            )
        } else if configuration.schema == "roboguide.controller-replica-process/v0.4" {
            controller_replication::ReplicaContentStore::open_with_snapshot_receives(
                &content.root,
                &configuration.replica.profile_identity(),
                configuration.replica.replica_id,
                content.restore_binding_digest.as_deref(),
            )
        } else {
            match content.restore_binding_digest.as_deref() {
                Some(digest) => controller_replication::ReplicaContentStore::open_restored_streams(
                    &content.root,
                    &configuration.replica.profile_identity(),
                    configuration.replica.replica_id,
                    digest,
                ),
                None => controller_replication::ReplicaContentStore::open_with_streams(
                    &content.root,
                    &configuration.replica.profile_identity(),
                    configuration.replica.replica_id,
                ),
            }
        }?;
        ReplicaNode::start_with_controller_content_window(
            configuration.replica.clone(),
            &configuration.storage_directory.join("replica.sqlite3"),
            root,
            content.bootstrap_owners.clone(),
            configuration.transport_limits.clone(),
            content.quorum_observation_ms,
        )
        .await?
    } else {
        ReplicaNode::start_with_transport_limits(
            configuration.replica.clone(),
            &configuration.storage_directory.join("replica.sqlite3"),
            configuration.transport_limits.clone(),
        )
        .await?
    };
    node.enable_controller_supervision()?;
    let _content_ingress = if let Some(content) = &configuration.content {
        Some(node.serve_controller_ingress(&content.socket).await?)
    } else {
        None
    };
    let mut owned = None;
    let metrics_task = crate::metrics::start(
        configuration.metrics_address,
        configuration.storage_directory.join("status.json"),
    )
    .await?;
    let mut leader_transfer_attempted = false;
    let shutdown_signal = tokio::signal::ctrl_c();
    tokio::pin!(shutdown_signal);
    let mut timer = tokio::time::interval(Duration::from_millis(200));
    timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let result = loop {
        tokio::select! {
            biased;
            result = &mut shutdown_signal => { break result.map_err(|error| error.to_string()); }
            _ = timer.tick() => {
                let current = match node.current_epoch() {
                    Some(epoch) => match node.check_epoch(epoch).await {
                        Ok(()) => Some(epoch),
                        Err(error) => { if owned.is_some() { eprintln!("Controller quorum fence: {error}"); } None }
                    },
                    None => None,
                };
                if let Some(result) = ready_shutdown(shutdown_signal.as_mut()).await {
                    break result.map_err(|error| error.to_string());
                }
                let child_exit = match owned.as_mut() {
                    Some(OwnedController { child, .. }) => child.try_wait().map_err(|error| error.to_string())?,
                    None => None,
                };
                let child_dead = child_exit.is_some();
                if child_dead || owned.as_ref().is_some_and(|controller| Some(controller.epoch) != current) {
                    eprintln!("Stopping owned Controller: exit_status={child_exit:?}, current_epoch={current:?}");
                    stop_controller(&mut owned, &node).await?;
                }
                let _transfer_reconciled = node.reconcile_leader_transfer().await?;
                if !leader_transfer_attempted
                    && let (Some(transfer), Some(epoch)) =
                        (configuration.leader_transfer.as_ref(), current)
                {
                    leader_transfer_attempted = true;
                    stop_controller(&mut owned, &node).await?;
                    let result = node
                        .request_leader_transfer(transfer.target, transfer.transfer_id.clone())
                        .await;
                    let record = match &result {
                        Ok(receipt) => json!({"state":"requested","receipt":receipt}),
                        Err(error) => json!({"state":"rejected","source_epoch":epoch,"error":error}),
                    };
                    std::fs::write(
                        configuration.storage_directory.join("leader-transfer.json"),
                        serde_json::to_vec(&record).map_err(|error| error.to_string())?,
                    )
                    .map_err(|error| error.to_string())?;
                    if let Err(error) = result {
                        eprintln!("Leader transfer rejected: {error}");
                    }
                    continue;
                }
                if owned.is_none()
                    && !node.leader_transfer_blocks_controller()?
                    && let Some(epoch) = current
                {
                    configuration.validate()?;
                    if configuration.content.is_some()&&node.accepted_revision()?>0 {
                        let ready=match node.accepted_content_target().await {
                            Ok(target)=>node.content_quorum(target).await,
                            Err(error)=>Err(error),
                        };
                        if let Err(error)=ready{
                            eprintln!("original complete content not ready before Controller spawn: {error}");
                            write_status(&configuration,&node,None,&owned).await?;
                            continue;
                        }
                    }
                    let projected_node = node.clone();
                    let directory = configuration.storage_directory.clone();
                    let format = match configuration.controller_environment.get("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA").map(String::as_str) {
                        None | Some("legacy") => state::ControllerStorageFormat::Legacy,
                        Some("1") => state::ControllerStorageFormat::V1,
                        _ => return Err("unsupported original Controller storage declaration".into()),
                    };
                    let schema = configuration.replica.application_schema.clone();
                    let profile = node.profile_identity();
                    tokio::task::spawn_blocking(move || {
                        let view = projected_node.accepted_view()?;
                        materialize(&directory, &view, format, &schema, &profile)
                    })
                        .await.map_err(|error| error.to_string())??;
                    if node.check_epoch(epoch).await.is_ok() {
                        if let Some(result) = ready_shutdown(shutdown_signal.as_mut()).await {
                            break result.map_err(|error| error.to_string());
                        }
                        owned = Some(start_controller(&configuration, &node, epoch).await?);
                    }
                }
                write_status(&configuration, &node, current, &owned).await?;
            }
        }
    };
    if let Some(task) = metrics_task {
        task.abort();
    }
    stop_controller(&mut owned, &node).await?;
    node.shutdown().await?;
    result
}

/// Observes the original latched OS signal after an await without replacing its subscription.
async fn ready_shutdown(
    mut signal: std::pin::Pin<&mut impl std::future::Future<Output = std::io::Result<()>>>,
) -> Option<std::io::Result<()>> {
    std::future::poll_fn(|context| {
        std::task::Poll::Ready(match signal.as_mut().poll(context) {
            std::task::Poll::Ready(result) => Some(result),
            std::task::Poll::Pending => None,
        })
    })
    .await
}
