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
}

/// Closed local process operations, with no transport, resource or Task execution authority.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
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
    let content =
        ReplicaContentStore::create(&member.root.join("content"), &member.profile, member.member)?;
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
    let content =
        ReplicaContentStore::open(&member.root.join("content"), &member.profile, member.member)?;
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

/// Emits one bounded actual process observation before an independent parent may kill this incarnation.
fn emit(value: &Value) -> Result<(), String> {
    println!("{value}");
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Exercises actual independent Snapshot publication/install/read ownership over real original bytes.
async fn run(request: Request) -> Result<Value, String> {
    match request {
        Request::Inspect { member } => {
            let (store, content) = open(&member)?;
            observation(&member, &store, &content)
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
