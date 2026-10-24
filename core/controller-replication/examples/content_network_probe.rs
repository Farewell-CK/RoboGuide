//! Actual infrastructure network content processes; no embodied Nodes, devices or execution inventory.

use controller_replication::*;
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};

mod probe_warnings;

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
    /// Explicit new original-child generation; old invocations retain parent7 and no child authority.
    #[serde(default)]
    owned_child: bool,
    /// Actual private endpoint, required only by explicit parent8.
    #[serde(default)]
    child_socket: Option<PathBuf>,
    /// Explicit durable Snapshot receiving generation; the original Root4 mode remains unchanged.
    #[serde(default)]
    snapshot_receives: bool,
    /// Explicit Root7 native acceptance; older configurations keep their original storage meaning.
    #[serde(default)]
    reclamation: bool,
}

/// Closed trusted local process operations; no arbitrary SQL, shell or fabricated execution is accepted.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Enrolls only the declared actual infrastructure member as a non-voter through the core API.
    AddLearner {
        /// Existing configured real software replica, independent of embodied Nodes.
        member: u64,
    },
    /// Requests actual joint consensus after core admission; responses never imply device continuity.
    ChangeVoters {
        /// Complete desired infrastructure voter set, confined to the original profile.
        voters: BTreeSet<u64>,
        /// Explicit removed-member retention policy.
        retain_removed_as_learners: bool,
    },
    /// Publishes an actual full Snapshot and purges only its original applied cut through the maintained API.
    CompactSnapshot,
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
    /// Routes exact already-committed bytes from this actual member; never admits a new proposal.
    RouteCommitted {
        /// Exact current full accepted target selected by the actual quorum observation.
        target: Value,
        /// Original qualified bytes, with immutable source owner.
        content: ArtifactContentKey,
        /// Actual current committed receiving member.
        receiver: u64,
        /// Original nonrenewing route operation identity.
        command_id: String,
    },
    /// Reopens each exact target only through the actual original observation path.
    Observe {
        /// Explicit original complete target; parsing grants no authority.
        target: Value,
    },
    /// Spawns one actual owned source contract process after durable original pre-spawn intent.
    SpawnOwned {
        /// Explicit maintained actual source executable, independently frozen by the checker.
        binary: PathBuf,
        /// Exact actual child configuration file retained before spawn.
        configuration: PathBuf,
        /// Actual owned source CAS directory.
        source_cas: PathBuf,
        /// Actual owned source SQL file.
        source_sql: PathBuf,
        /// Explicit original complete source/metadata operation duration.
        budget_ms: u64,
    },
    /// Sends one closed source command to the actual original owned process.
    OwnedCommand {
        /// Maintained child protocol request; never shell or arbitrary SQL.
        request: Value,
    },
    /// Actually kills and reaps the original owned software process.
    StopOwned,
    /// Stops only this owned infrastructure service; no device stopping is inferred.
    Shutdown,
}

/// Uses the real authenticated product administration client and this original member's trust SQL.
fn administration(config: &ReplicaConfig, parent_sql: &Path) -> Result<ReplicaAdminClient, String> {
    ReplicaAdminClient::with_trust_database(config.clone(), config.replica_id, parent_sql)
}

/// Actual original source Child handle; only its own wait can produce its durable process exit.
struct OwnedSource {
    /// Actual owned process handle; taken only by its original stop/wait path.
    child: Option<std::process::Child>,
    /// Independently registered original identity.
    process: ControllerProcessIdentity,
    /// Original supervisor incarnation and actual writer retained through native wait publication.
    node: ReplicaNode,
}

impl OwnedSource {
    /// Reaps this exact actual child and publishes only its original OS outcome.
    fn stop(&mut self) -> Result<Value, String> {
        use std::os::unix::process::ExitStatusExt;
        let Some(mut child) = self.child.take() else {
            return Err("original source already reaped".into());
        };
        if child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_none()
        {
            child.kill().map_err(|error| error.to_string())?;
        }
        let result = child.wait().map_err(|error| error.to_string())?;
        let outcome = ControllerProcessExit {
            process: self.process.clone(),
            exit_code: result.code(),
            signal: result.signal(),
        };
        self.node.record_controller_exit(outcome.clone())?;
        Ok(json!({"original_exit":outcome}))
    }

    /// Reads one bounded response belonging to this actual original PID.
    fn receive(&mut self) -> Result<Value, String> {
        let child = self.child.as_mut().ok_or("actual original source ended")?;
        let output = child.stdout.as_mut().ok_or("actual source output absent")?;
        let mut raw = Vec::new();
        std::io::BufReader::new(output)
            .take(16 * 1024 * 1024 + 1)
            .read_until(b'\n', &mut raw)
            .map_err(|error| error.to_string())?;
        if raw.is_empty() || raw.len() > 16 * 1024 * 1024 {
            return Err("actual source response ended or exceeded bound".into());
        }
        let value: Value = serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
        if value["pid"] != self.process.pid {
            return Err("actual source response PID differs".into());
        }
        Ok(value)
    }

    /// Sends exactly one actual original child request and never repeats an uncertain command.
    fn ask(&mut self, request: Value) -> Result<Value, String> {
        let child = self.child.as_mut().ok_or("actual original source ended")?;
        let input = child
            .stdin
            .as_mut()
            .ok_or("actual original source input absent")?;
        let bytes = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
        if bytes.len() > 1024 * 1024 {
            return Err("actual source request exceeds bound".into());
        }
        input.write_all(&bytes).map_err(|error| error.to_string())?;
        input.write_all(b"\n").map_err(|error| error.to_string())?;
        input.flush().map_err(|error| error.to_string())?;
        self.receive()
    }
}

impl Drop for OwnedSource {
    /// Cleanup retains actual native wait evidence and never fabricates physical stopping.
    fn drop(&mut self) {
        if self.child.is_some()
            && let Err(error) = self.stop()
        {
            eprintln!("actual original source cleanup failed: {error}");
        }
    }
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
    let root = if config.reclamation {
        ReplicaContentStore::open_with_reclamation(
            &config.content_root,
            &config.replica.profile_identity(),
            config.replica.replica_id,
            None,
        )
    } else if config.snapshot_receives {
        ReplicaContentStore::open_with_snapshot_receives(
            &config.content_root,
            &config.replica.profile_identity(),
            config.replica.replica_id,
            None,
        )
    } else {
        ReplicaContentStore::open_with_streams(
            &config.content_root,
            &config.replica.profile_identity(),
            config.replica.replica_id,
        )
    }?;
    let (node, _server) = if config.owned_child {
        ReplicaNode::start_with_controller_content(
            config.replica.clone(),
            &config.parent_sql,
            root.clone(),
            config.member_stores,
            ReplicaTransportLimits::default(),
        )
        .await?
    } else {
        ReplicaNode::start_with_content(
            config.replica.clone(),
            &config.parent_sql,
            root.clone(),
            config.member_stores,
            ReplicaTransportLimits::default(),
        )
        .await?
    };
    let _ingress = if config.owned_child {
        node.enable_controller_supervision()?;
        Some(
            node.serve_controller_ingress(
                config
                    .child_socket
                    .as_deref()
                    .ok_or("explicit original-child socket absent")?,
            )
            .await?,
        )
    } else {
        None
    };
    emit(json!({"ready":true,"pid":std::process::id(),"identity":root.identity()}))?;
    let mut pending = None;
    let mut owned: Option<OwnedSource> = None;
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
            Request::AddLearner { member } => administration(&config.replica, &config.parent_sql)?
                .add_learner(member)
                .await
                .map(|()| json!({"member":member})),
            Request::ChangeVoters {
                voters,
                retain_removed_as_learners,
            } => administration(&config.replica, &config.parent_sql)?
                .change_membership(voters.clone(), retain_removed_as_learners)
                .await
                .map(|()| json!({"voters":voters})),
            Request::CompactSnapshot => node.compact_content_snapshot(15000).await,
            Request::RouteCommitted {
                target,
                content,
                receiver,
                command_id,
            } => node
                .route_committed_content_to(
                    receiver,
                    serde_json::from_value(target).map_err(|error| error.to_string())?,
                    content,
                    command_id,
                    15000,
                )
                .await
                .map(|original| json!({"original":original})),
            Request::Status => {
                let epoch = node.current_epoch();
                let quorum = match epoch {
                    Some(epoch) => node.check_epoch(epoch).await.is_ok(),
                    None => false,
                };
                let readiness = node.readiness().await;
                Ok(
                    json!({"epoch":epoch,"quorum":quorum,"identity":root.identity(),"readiness":readiness}),
                )
            }
            Request::Capture {
                source_sql,
                source_cas,
                operation_id,
            } => {
                if config.owned_child {
                    return Err("owned-child probe requires its actual source process".into());
                }
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
            Request::SpawnOwned {
                binary,
                configuration,
                source_cas,
                source_sql,
                budget_ms,
            } => {
                if !config.owned_child || owned.is_some() {
                    Err("actual original source slot unavailable".into())
                } else {
                    let epoch = node.current_epoch().ok_or("actual leader absent")?;
                    let instance = node.begin_controller_process(epoch).await?;
                    let client = ControllerContentClientConfig {
                        socket: config
                            .child_socket
                            .clone()
                            .ok_or("original socket absent")?,
                        profile: node.profile_identity(),
                        epoch,
                        instance: instance.clone(),
                        tls: config
                            .replica
                            .tls
                            .clone()
                            .ok_or("actual original TLS absent")?,
                        trust_database: config.parent_sql.clone(),
                        budget_ms,
                    };
                    let data =
                        json!({"client":client,"source_cas":source_cas,"source_sql":source_sql});
                    std::fs::write(
                        &configuration,
                        serde_json::to_vec(&data).map_err(|error| error.to_string())?,
                    )
                    .map_err(|error| error.to_string())?;
                    let mut child = std::process::Command::new(binary)
                        .arg(&configuration)
                        .stdin(std::process::Stdio::piped())
                        .stdout(std::process::Stdio::piped())
                        .stderr(std::process::Stdio::inherit())
                        .spawn()
                        .map_err(|error| error.to_string())?;
                    let process =
                        match node.register_controller_process(epoch, instance, child.id()) {
                            Ok(process) => process,
                            Err(error) => {
                                let _ = child.kill();
                                let _ = child.wait();
                                return Err(error);
                            }
                        };
                    owned = Some(OwnedSource {
                        child: Some(child),
                        process: process.clone(),
                        node: node.clone(),
                    });
                    let ready = owned.as_mut().ok_or("actual source absent")?.receive()?;
                    Ok(json!({"process":process,"configuration":configuration,"child_ready":ready}))
                }
            }
            Request::OwnedCommand { request } => owned
                .as_mut()
                .ok_or("actual original source absent")?
                .ask(request),
            Request::StopOwned => {
                let mut original = owned.take().ok_or("actual original source absent")?;
                original.stop()
            }
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
    if let Some(mut original) = owned.take() {
        let _ = original.stop()?;
    }
    node.shutdown().await?;
    Ok(())
}

/// Runs only an explicit bounded profile inspection or owned infrastructure service.
#[tokio::main(worker_threads = 1)]
async fn main() -> Result<(), String> {
    probe_warnings::enable()?;
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
        "snapshot-upgrade" => {
            if !config.snapshot_receives {
                return Err("Snapshot upgrade requires explicit Root6 configuration".into());
            }
            let root = ReplicaContentStore::upgrade_snapshot_receives(
                &config.content_root,
                &config.replica.profile_identity(),
                config.replica.replica_id,
                None,
            )?;
            emit(
                json!({"identity":root.identity(),"layout":REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA}),
            )
        }
        "profile" => emit(json!({"profile":config.replica.profile_identity()})),
        "serve" => serve(config).await,
        _ => Err("unsupported actual network process operation".into()),
    }
}
