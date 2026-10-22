//! Actual Raft storage/Snapshot/content processes; direct storage operations never assert network quorum.

use controller_replication::*;
use openraft::storage::RaftStateMachine;
use openraft::{CommittedLeaderId, Entry, EntryPayload, LogId, RaftSnapshotBuilder};
use serde::Deserialize;
use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// Explicit actual local infrastructure storage declaration; no devices/capabilities are defined.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Member {
    /// Actual private root, fresh only for exercise and never automatically reset on inspect.
    root: PathBuf,
    /// Explicit original infrastructure profile.
    profile: String,
    /// Declared infrastructure member identity, independent of any embodied Node.
    member: u64,
    /// Explicit new producer generation; absent retains the original v2 reader-only storage profile.
    #[serde(default)]
    producer_journal: bool,
}

/// Closed actual local storage operations; requests cannot execute a shell or invent Node/device facts.
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum Operation {
    /// Copy exact original candidate bytes from the actual source writer into the receiving backend.
    CopyOriginal {
        /// Actual original source CAS, checked against every qualified immutable input owner.
        source_cas: PathBuf,
        /// Exact original candidate carrier captured from maintained Controller SQL/CAS.
        entry: PathBuf,
    },
    /// Append one exact original entry through the maintained journal/flush implementation.
    Append {
        /// Complete original command captured from actual maintained Controller SQL/CAS.
        entry: PathBuf,
    },
    /// Apply one original entry through actual owned blocking storage and original receipt gates.
    Apply {
        /// Complete original command, never a second Task/Execute operation.
        entry: PathBuf,
    },
    /// Publish one explicit original Snapshot invocation and export only its actual retained metadata bytes.
    Build {
        /// Immutable explicit original storage invocation ID.
        operation_id: String,
        /// New owned output file for actual Snapshot metadata transfer.
        output: PathBuf,
    },
    /// Install one actual original Snapshot using independent receiver-owned producer scopes.
    Install {
        /// Immutable original receiver storage operation ID.
        operation_id: String,
        /// Actual original published metadata pointers.
        meta: openraft::SnapshotMeta<u64, openraft::BasicNode>,
        /// Actual bounded original Snapshot metadata bytes.
        input: PathBuf,
    },
    /// Remove only actual original parent log suffix through the maintained storage trait.
    Truncate {
        /// Exact original consensus log position; this direct call claims no election.
        log_id: LogId<u64>,
    },
    /// Read actual current producer sources and record independent original retirement evidence.
    Reconcile,
    /// Hold two actual independent Snapshot readers across replacement for a parent's native death check.
    HoldSnapshots {
        /// Explicit original prefix for two distinct managed Snapshot operations.
        prefix: String,
    },
    /// Independently observe actual original reader-process absence without creating a byte receipt.
    RetireReader {
        /// Exact original reader journal ID, independently found in the actual SQL evidence.
        reader_id: String,
    },
}

/// Closed local process operations, with no transport, resource or Task execution authority.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Capture actual original Controller authority and actual copy bytes into a fresh producer-profile member.
    Prepare {
        /// Actual stopped original Controller SQL; original sidecar/inode leases are mandatory.
        source_sql: PathBuf,
        /// Actual original CAS, held by its true writer for the whole copy lifetime.
        source_cas: PathBuf,
        /// Explicit fresh infrastructure storage, with producer_journal=true.
        member: Member,
        /// New owned file for the exact original captured entry; never overwritten.
        entry_output: PathBuf,
    },
    /// Create an explicitly fresh empty infrastructure receiver, with no implied content/Node authority.
    Create {
        /// Actual private storage declaration.
        member: Member,
    },
    /// Execute one exact local storage operation under actual original root/SQL writers.
    Operate {
        /// Actual existing original member, never implicitly created.
        member: Member,
        /// Closed bounded storage invocation.
        operation: Box<Operation>,
    },
    /// Explicitly migrate only original reader-layout storage, preserving unavailable legacy producer pins.
    UpgradeRoot {
        /// Original actual root and member; parent SQL v4 never silently becomes v5.
        member: Member,
    },
    /// Consume an actual existing original Controller SQL/CAS source and exercise storage producers.
    Exercise {
        /// Actual original source metadata, produced by the maintained Controller rather than this probe.
        source_sql: PathBuf,
        /// Actual original source CAS, owned by this managed process for its complete read lifetime.
        source_cas: PathBuf,
        /// First actual declared infrastructure storage.
        first: Member,
        /// Independent target backend whose own bytes must precede Snapshot install.
        second: Member,
        /// Hold actual Snapshot readers for a parent's independent SIGKILL/exit observation.
        hold: bool,
    },
    /// Reopen only original actual storage and inspect current verified bytes plus independent pins.
    Inspect {
        /// Actual original member, never discovered or bootstrapped.
        member: Member,
    },
}

/// Holds actual original canonical sidecar and SQL inode before source snapshot capture.
fn source_leases(path: &Path) -> Result<(File, File), String> {
    let canonical = path.canonicalize().map_err(|error| error.to_string())?;
    let mut sibling = canonical.as_os_str().to_os_string();
    sibling.push(".writer.lock");
    let sibling = OpenOptions::new()
        .read(true)
        .write(true)
        .open(Path::new(&sibling))
        .map_err(|error| error.to_string())?;
    sibling
        .try_lock()
        .map_err(|error| format!("original Controller source writer is active: {error}"))?;
    let database = OpenOptions::new()
        .read(true)
        .write(true)
        .open(canonical)
        .map_err(|error| error.to_string())?;
    database
        .try_lock()
        .map_err(|error| format!("original Controller source inode is active: {error}"))?;
    Ok((sibling, database))
}

/// Creates only the explicitly fresh actual storage; partial/accepted existing roots are refused.
fn create(member: &Member) -> Result<(SqliteRaftStore, ReplicaContentStore), String> {
    std::fs::create_dir(&member.root).map_err(|error| error.to_string())?;
    let content = if member.producer_journal {
        ReplicaContentStore::create_with_producers(
            &member.root.join("content"),
            &member.profile,
            member.member,
        )?
    } else {
        ReplicaContentStore::create(&member.root.join("content"), &member.profile, member.member)?
    };
    let store = SqliteRaftStore::open_with_content(
        &member.root.join("replica.sqlite3"),
        &member.profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )?;
    Ok((store, content))
}

/// Reopens only original roots and mandatory heads; source loss never becomes fresh authority.
fn open(member: &Member) -> Result<(SqliteRaftStore, ReplicaContentStore), String> {
    let content = if member.producer_journal {
        ReplicaContentStore::open_with_producers(
            &member.root.join("content"),
            &member.profile,
            member.member,
        )?
    } else {
        ReplicaContentStore::open(&member.root.join("content"), &member.profile, member.member)?
    };
    let store = SqliteRaftStore::open_with_content(
        &member.root.join("replica.sqlite3"),
        &member.profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )?;
    Ok((store, content))
}

/// Reports actual current original-view identity and pins, independently hashing each required CAS file.
fn observation(
    member: &Member,
    store: &SqliteRaftStore,
    content: &ReplicaContentStore,
) -> Result<Value, String> {
    let view = store.accepted_view()?;
    let state = view
        .content
        .as_ref()
        .ok_or("complete original application is unavailable")?;
    let pins = state
        .inputs
        .iter()
        .map(|key| {
            content
                .active_references(key)
                .map(|pins| json!({"content":key,"references":pins}))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(
        json!({"identity":content.identity(),"member":member.member,"revision":view.revision,"batch_digest":state.batch_digest,"original_recovery_digest":state.recovery.identity().map_err(|error|error.to_string())?,"checkpoint_digest":view.checkpoint.as_ref().map(state::checkpoint_identity),"actual_bytes":store.verify_accepted_content().is_ok(),"inputs":state.inputs,"pins":pins}),
    )
}

/// Enters the maintained exact storage apply trait; no election, transport or voting receipt is inferred.
async fn apply(
    store: &mut SqliteRaftStore,
    candidate: ContentApplicationBatch,
    index: u64,
) -> Result<(), String> {
    let member = candidate.epoch.replica_id;
    let term = candidate.epoch.term;
    let replies = store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(term, member), index),
            payload: EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application: Box::new(candidate),
            }),
        }])
        .await
        .map_err(|error| error.to_string())?;
    if replies.len() != 1 || !replies[0].accepted {
        return Err(format!(
            "actual original storage candidate refused: {replies:?}"
        ));
    }
    Ok(())
}

/// Requires original complete bytes before its independent target backend may report availability.
fn copy(
    cas: &artifact_store::FileSystemArtifactStore,
    content: &ReplicaContentStore,
    candidate: &ContentApplicationBatch,
    purpose: &str,
) -> Result<(), String> {
    let identity = candidate.identity()?;
    for (index, key) in candidate.inputs.iter().enumerate() {
        let receipt = content.copy_from_artifact(
            cas,
            key,
            &format!("{purpose}-{index}"),
            &identity,
            30000,
        )?;
        if receipt.receipt.is_none() {
            return Err("actual original content copy remains Unknown".into());
        }
    }
    Ok(())
}

/// Reads actual bounded metadata bytes; payload bytes remain in independent qualified CAS files.
fn metadata_bytes(path: &Path) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(|error| error.to_string())?
        .take(256 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > 256 * 1024 * 1024 {
        return Err("actual storage metadata exceeded its bound".into());
    }
    Ok(bytes)
}

/// Writes one new exact owned carrier file and fsyncs it without overwriting any historical evidence.
fn publish_metadata(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| error.to_string())?;
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|error| error.to_string())?;
    File::open(
        path.parent()
            .ok_or("actual metadata output parent is unavailable")?,
    )
    .and_then(|file| file.sync_all())
    .map_err(|error| error.to_string())
}

/// Reads complete original local producer history and every independently retained qualified input.
fn producer_observation(
    store: &SqliteRaftStore,
    content: &ReplicaContentStore,
) -> Result<Value, String> {
    let originals = store.content_producer_history()?;
    let view = store.accepted_view()?;
    let mut keys = std::collections::BTreeSet::<ArtifactContentKey>::new();
    for original in &originals {
        let inputs: Vec<ArtifactContentKey> =
            serde_json::from_value(original.original["intent"]["inputs"].clone())
                .map_err(|error| error.to_string())?;
        keys.extend(inputs);
    }
    let pins = keys
        .into_iter()
        .map(|key| {
            content
                .active_references(&key)
                .map(|pins| json!({"content":key,"references":pins}))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(
        json!({"identity":content.identity(),"revision":view.revision,"checkpoint_digest":view.checkpoint.as_ref().map(state::checkpoint_identity),
        "actual_bytes":view.content.as_ref().map(|_| store.verify_accepted_content().is_ok()),"producers":originals,"pins":pins,
        "nodes":0,"devices":0,"models":0,"simulators":0,"scope":"direct actual SQL/CAS producer storage; no network quorum or device authority"}),
    )
}

/// Captures only real original Controller authority and CAS under their actual writers, with no Row fabrication.
fn prepare_original(
    source_sql: &Path,
    source_cas: &Path,
    member: &Member,
    entry_output: &Path,
) -> Result<Value, String> {
    if !member.producer_journal {
        return Err("original preparation requires the explicit producer profile".into());
    }
    let _leases = source_leases(source_sql)?;
    let original = state::SqliteEventLog::read_complete_replica_state(source_sql)
        .map_err(|error| error.to_string())?;
    if original
        .recovery
        .history_floor()
        .map_err(|error| error.to_string())?
        != 0
    {
        return Err("original preparation requires complete uncompacted source history".into());
    }
    let cas = artifact_store::FileSystemArtifactStore::new_with_archive_references(source_cas)
        .map_err(|error| error.to_string())?;
    let candidate = ContentApplicationBatch::from_source(
        &member.profile,
        ControllerEpoch {
            term: 1,
            replica_id: member.member,
        },
        &cas,
        &ReplicaView::default(),
        state::ApplicationCommitBatch {
            schema: state::APPLICATION_COMMIT_SCHEMA.into(),
            previous_event_sequence: 0,
            previous_checkpoint_digest: None,
            checkpoint: original.checkpoint,
            events: original.events,
            commands: original.commands,
        },
        original.recovery,
    )?;
    if candidate.inputs.is_empty() {
        return Err("actual original source has no content obligations".into());
    }
    let (store, content) = create(member)?;
    copy(&cas, &content, &candidate, "actual-original-producer-copy")?;
    let inputs = candidate.inputs.clone();
    let entry: Entry<ControllerRaftConfig> = Entry {
        log_id: LogId::new(CommittedLeaderId::new(1, member.member), 1),
        payload: EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: Box::new(candidate),
        }),
    };
    let bytes = serde_json::to_vec(&entry).map_err(|error| error.to_string())?;
    publish_metadata(entry_output, &bytes)?;
    Ok(
        json!({"entry":entry_output,"entry_byte_size":bytes.len(),"inputs":inputs,"storage":producer_observation(&store,&content)?}),
    )
}

/// Emits one bounded actual process observation before an independent parent may kill this incarnation.
fn emit(value: &Value) -> Result<(), String> {
    println!("{value}");
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Exercises actual independent Snapshot publication/install/read ownership over real original bytes.
async fn run(request: Request) -> Result<Value, String> {
    match request {
        Request::Prepare {
            source_sql,
            source_cas,
            member,
            entry_output,
        } => prepare_original(&source_sql, &source_cas, &member, &entry_output),
        Request::Create { member } => {
            if !member.producer_journal {
                return Err(
                    "original receiver creation requires its explicit producer profile".into(),
                );
            }
            let (store, content) = create(&member)?;
            producer_observation(&store, &content)
        }
        Request::UpgradeRoot { member } => {
            let content = ReplicaContentStore::upgrade_producer_journal(
                &member.root.join("content"),
                &member.profile,
                member.member,
            )?;
            Ok(
                json!({"identity":content.identity(),"root_upgraded":true,"parent_upgrade_performed":false,
                "legacy_original_budget_and_source":"unavailable; raw pins/history retained","nodes":0,"devices":0}),
            )
        }
        Request::Operate { member, operation } => {
            if !member.producer_journal {
                return Err(
                    "original producer operations require the explicit producer profile".into(),
                );
            }
            let (mut store, content) = open(&member)?;
            let effect = match *operation {
                Operation::CopyOriginal { source_cas, entry } => {
                    let entry: Entry<ControllerRaftConfig> =
                        serde_json::from_slice(&metadata_bytes(&entry)?)
                            .map_err(|error| error.to_string())?;
                    let EntryPayload::Normal(ReplicaCommand::ContentApplication {
                        content_application: candidate,
                    }) = entry.payload
                    else {
                        return Err("actual original entry has no complete candidate".into());
                    };
                    let cas = artifact_store::FileSystemArtifactStore::new_with_archive_references(
                        &source_cas,
                    )
                    .map_err(|error| error.to_string())?;
                    copy(&cas, &content, &candidate, "actual-original-receiver-copy")?;
                    Value::Null
                }
                Operation::Append { entry } => {
                    let entry: Entry<ControllerRaftConfig> =
                        serde_json::from_slice(&metadata_bytes(&entry)?)
                            .map_err(|error| error.to_string())?;
                    store
                        .persist_log_entries(&[entry])
                        .map_err(|error| error.to_string())?;
                    Value::Null
                }
                Operation::Apply { entry } => {
                    let entry: Entry<ControllerRaftConfig> =
                        serde_json::from_slice(&metadata_bytes(&entry)?)
                            .map_err(|error| error.to_string())?;
                    json!(
                        store
                            .apply([entry])
                            .await
                            .map_err(|error| error.to_string())?
                    )
                }
                Operation::Build {
                    operation_id,
                    output,
                } => {
                    let snapshot = store
                        .build_content_snapshot_for(&operation_id)
                        .map_err(|error| error.to_string())?;
                    let bytes = snapshot.snapshot.into_inner();
                    publish_metadata(&output, &bytes)?;
                    json!({"meta":snapshot.meta,"output":output,"byte_size":bytes.len()})
                }
                Operation::Install {
                    operation_id,
                    meta,
                    input,
                } => {
                    store
                        .install_content_snapshot_for(&operation_id, &meta, metadata_bytes(&input)?)
                        .map_err(|error| error.to_string())?;
                    Value::Null
                }
                Operation::Truncate { log_id } => {
                    openraft::storage::RaftLogStorage::truncate(&mut store, log_id)
                        .await
                        .map_err(|error| error.to_string())?;
                    Value::Null
                }
                Operation::Reconcile => json!(store.reconcile_content_producers()?),
                Operation::HoldSnapshots { prefix } => {
                    let old = store
                        .build_content_snapshot_for(&format!("{prefix}-old"))
                        .map_err(|error| error.to_string())?;
                    let current = store
                        .build_content_snapshot_for(&format!("{prefix}-current"))
                        .map_err(|error| error.to_string())?;
                    emit(
                        &json!({"ready":true,"pid":std::process::id(),"old_snapshot_id":old.meta.snapshot_id,"current_snapshot_id":current.meta.snapshot_id,"nodes":0,"devices":0}),
                    )?;
                    let mut finish = String::new();
                    std::io::stdin()
                        .take(32)
                        .read_to_string(&mut finish)
                        .map_err(|error| error.to_string())?;
                    if finish.trim() != "finish" {
                        return Err(
                            "actual original Snapshot readers lack their finish request".into()
                        );
                    }
                    drop(old);
                    drop(current);
                    Value::Null
                }
                Operation::RetireReader { reader_id } => {
                    json!(content.retire_ended_reader(&reader_id)?)
                }
            };
            Ok(json!({"effect":effect,"storage":producer_observation(&store,&content)?}))
        }
        Request::Inspect { member } => {
            let (store, content) = open(&member)?;
            if member.producer_journal {
                producer_observation(&store, &content)
            } else {
                observation(&member, &store, &content)
            }
        }
        Request::Exercise {
            source_sql,
            source_cas,
            first,
            second,
            hold,
        } => {
            if first.profile != second.profile || first.member == second.member {
                return Err(
                    "explicit independent members require one exact original profile".into(),
                );
            }
            let _leases = source_leases(&source_sql)?;
            let original = state::SqliteEventLog::read_complete_replica_state(&source_sql)
                .map_err(|error| error.to_string())?;
            if original
                .recovery
                .history_floor()
                .map_err(|error| error.to_string())?
                != 0
            {
                return Err(
                    "storage producer exercise requires a complete original uncompacted source"
                        .into(),
                );
            }
            let cas =
                artifact_store::FileSystemArtifactStore::new_with_archive_references(&source_cas)
                    .map_err(|error| error.to_string())?;
            let batch = state::ApplicationCommitBatch {
                schema: state::APPLICATION_COMMIT_SCHEMA.into(),
                previous_event_sequence: 0,
                previous_checkpoint_digest: None,
                checkpoint: original.checkpoint,
                events: original.events,
                commands: original.commands,
            };
            let candidate = ContentApplicationBatch::from_source(
                &first.profile,
                ControllerEpoch {
                    term: 1,
                    replica_id: first.member,
                },
                &cas,
                &ReplicaView::default(),
                batch,
                original.recovery,
            )?;
            if candidate.inputs.is_empty() {
                return Err(
                    "actual original producer source has no real content obligations".into(),
                );
            }
            let (mut source, source_content) = create(&first)?;
            apply(&mut source, candidate.clone(), 1).await?;
            if source.verify_accepted_content().is_ok() || source.build_snapshot().await.is_ok() {
                return Err("metadata alone published original unavailable bytes".into());
            }
            copy(&cas, &source_content, &candidate, "actual-source-copy")?;
            let old_snapshot = source
                .build_snapshot()
                .await
                .map_err(|error| error.to_string())?;
            let old_meta = old_snapshot.meta.clone();
            let old_bytes = source
                .get_current_snapshot()
                .await
                .map_err(|error| error.to_string())?
                .ok_or("published original Snapshot disappeared")?
                .snapshot
                .into_inner();
            let previous = source.accepted_view()?;
            let same = state::ApplicationCommitBatch {
                schema: state::APPLICATION_COMMIT_SCHEMA.into(),
                previous_event_sequence: candidate.batch.checkpoint.event_sequence,
                previous_checkpoint_digest: Some(state::checkpoint_identity(
                    &candidate.batch.checkpoint,
                )),
                checkpoint: candidate.batch.checkpoint.clone(),
                events: Vec::new(),
                commands: Vec::new(),
            };
            let next = ContentApplicationBatch::from_source(
                &first.profile,
                candidate.epoch,
                &cas,
                &previous,
                same,
                candidate.recovery.clone(),
            )?;
            apply(&mut source, next, 2).await?;
            let current_snapshot = source
                .build_snapshot()
                .await
                .map_err(|error| error.to_string())?;
            if current_snapshot.meta.snapshot_id == old_meta.snapshot_id {
                return Err(
                    "actual Snapshot replacement did not change its original identity".into(),
                );
            }
            let latest = source
                .get_current_snapshot()
                .await
                .map_err(|error| error.to_string())?
                .ok_or("current original Snapshot disappeared")?;
            let current_meta = latest.meta;
            let current_bytes = latest.snapshot.into_inner();
            let (mut target, target_content) = create(&second)?;
            if target
                .install_snapshot(
                    &current_meta,
                    Box::new(ReplicaSnapshotData::new(current_bytes.clone())),
                )
                .await
                .is_ok()
            {
                return Err(
                    "independent target installed a Snapshot without its actual required bytes"
                        .into(),
                );
            }
            copy(&cas, &target_content, &candidate, "actual-target-copy")?;
            target
                .install_snapshot(
                    &current_meta,
                    Box::new(ReplicaSnapshotData::new(current_bytes)),
                )
                .await
                .map_err(|error| error.to_string())?;
            if target
                .install_snapshot(&old_meta, Box::new(ReplicaSnapshotData::new(old_bytes)))
                .await
                .is_ok()
            {
                return Err("old Snapshot regressed already accepted original authority".into());
            }
            let a = observation(&first, &source, &source_content)?;
            let b = observation(&second, &target, &target_content)?;
            if a["batch_digest"] != b["batch_digest"]
                || a["original_recovery_digest"] != b["original_recovery_digest"]
                || a["identity"]["store_owner"] == b["identity"]["store_owner"]
            {
                return Err(
                    "actual independent receivers lost exact original namespace/metadata identity"
                        .into(),
                );
            }
            let result = json!({"pid":std::process::id(),"ready":true,"scope":"direct actual Raft storage/Snapshot; zero network quorum claims","first":a,"second":b,"missing_bytes_refused":true,"stale_snapshot_refused":true,"actual_old_snapshot_id":old_meta.snapshot_id,"actual_new_snapshot_id":current_meta.snapshot_id,"nodes":0,"devices":0,"models":0,"simulators":0});
            emit(&result)?;
            if hold {
                let mut finish = String::new();
                std::io::stdin()
                    .take(32)
                    .read_to_string(&mut finish)
                    .map_err(|error| error.to_string())?;
                if finish.trim() != "finish" {
                    return Err("actual Snapshot scope has no original finish request".into());
                }
            }
            drop(old_snapshot);
            drop(current_snapshot);
            Ok(
                json!({"closed":true,"first":observation(&first,&source,&source_content)?,"second":observation(&second,&target,&target_content)?}),
            )
        }
    }
}

/// Reads one closed bounded request file before any actual process/storage effect.
#[tokio::main(flavor = "current_thread")]
async fn main() {
    let request = (|| -> Result<Request, String> {
        let args = std::env::args().skip(1).collect::<Vec<_>>();
        if args.len() != 1 {
            return Err("usage: content_snapshot_probe REQUEST_JSON".into());
        }
        let mut bytes = Vec::new();
        File::open(&args[0])
            .map_err(|error| error.to_string())?
            .take(1024 * 1024 + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| error.to_string())?;
        if bytes.len() > 1024 * 1024 {
            return Err("request exceeds its fixed bound".into());
        }
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    })();
    let result = match request {
        Ok(request) => run(request).await,
        Err(error) => Err(error),
    };
    match result {
        Ok(value) => {
            if emit(&json!({"result":value})).is_err() {
                std::process::exit(2);
            }
        }
        Err(error) => {
            let _ = emit(&json!({"error":error}));
            std::process::exit(2);
        }
    }
}
