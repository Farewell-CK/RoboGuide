//! Actual complete State capture/materialization probe; no service, quorum or Node is created here.

use serde_json::{Value, json};
use state::{CompleteReplicaState, SqliteEventLog};
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// Owns this explicitly declared original canonical sibling plus actual database-inode lease.
/// External complete profiles still declare/drain every other original writer; no global coverage is inferred.
struct Leases {
    /// Actual declared canonical writer sidecar.
    _sibling: File,
    /// Actual SQL inode guard, independent of an alias name in another maintained probe.
    database: File,
}

/// Opens only a real regular leaf and refuses symlink substitution rather than discovering an authority.
fn regular(path: &Path, create: bool) -> Result<File, String> {
    if !create
        && !std::fs::symlink_metadata(path)
            .map_err(|error| error.to_string())?
            .is_file()
    {
        return Err("original probe requires an actual regular file".into());
    }
    OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(create)
        .open(path)
        .map_err(|error| error.to_string())
}

/// Acquires this actual declared source/target before SQLite access; existing accepted files are not replaced.
fn leases(database: &Path, create: bool) -> Result<Leases, String> {
    let canonical = if create {
        database
            .parent()
            .ok_or("target parent absent")?
            .canonicalize()
            .map_err(|error| error.to_string())?
            .join(database.file_name().ok_or("target file absent")?)
    } else {
        database.canonicalize().map_err(|error| error.to_string())?
    };
    let mut sidecar = canonical.as_os_str().to_os_string();
    sidecar.push(".writer.lock");
    let sibling = regular(Path::new(&sidecar), create)?;
    sibling
        .try_lock()
        .map_err(|error| format!("original declared State writer is still active: {error}"))?;
    let database = regular(&canonical, create)?;
    database
        .try_lock()
        .map_err(|error| format!("original actual State SQL inode is still active: {error}"))?;
    Ok(Leases {
        _sibling: sibling,
        database,
    })
}

/// Writes one immutable complete capture, syncing its actual bytes and parent before reporting success.
fn publish(path: &Path, original: &CompleteReplicaState) -> Result<(), String> {
    let raw = serde_json::to_vec(original).map_err(|error| error.to_string())?;
    let mut output = regular(path, true)?;
    output
        .write_all(&raw)
        .and_then(|()| output.sync_all())
        .map_err(|error| error.to_string())?;
    File::open(path.parent().ok_or("capture parent absent")?)
        .and_then(|file| file.sync_all())
        .map_err(|error| error.to_string())
}

/// Reads a closed bounded complete source; a corrupt/absent projection creates no target database.
fn read(path: &Path) -> Result<CompleteReplicaState, String> {
    let file = File::open(path).map_err(|error| error.to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("capture is not an actual regular file".into());
    }
    let mut raw = Vec::new();
    file.take(256 * 1024 * 1024 + 1)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    if raw.len() > 256 * 1024 * 1024 {
        return Err("capture exceeds complete source bound".into());
    }
    let original: CompleteReplicaState =
        serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
    original.validate().map_err(|error| error.to_string())?;
    Ok(original)
}

/// Reports only actual metadata identity/counts; payloads, permissions and hardware are never inferred.
fn summary(original: &CompleteReplicaState) -> Result<Value, String> {
    Ok(
        json!({"pid":std::process::id(),"identity":original.identity().map_err(|error|error.to_string())?,"archive_authority_id":original.recovery.metadata.get("archive_authority_id"),"events":original.events.len(),"commands":original.commands.len(),"original_archive_operations":original.recovery.archive_operations.len(),"original_archive_aborts":original.recovery.archive_aborts.len(),"history_floor":original.recovery.history_floor().map_err(|error|error.to_string())?,"nodes":0,"devices":0}),
    )
}

/// Executes only an explicit original-source export, inspection or fresh inactive metadata import.
fn run(arguments: &[String]) -> Result<Value, String> {
    match arguments {
        [action,source,destination] if action == "export" => {
            let _leases = leases(Path::new(source),false)?;
            let original = SqliteEventLog::read_complete_replica_state(source).map_err(|error|error.to_string())?;
            publish(&PathBuf::from(destination),&original)?;
            summary(&original)
        }
        [action,source,destination] if action == "import" => {
            let original = read(Path::new(source))?;
            let path = Path::new(destination);
            let owned = leases(path,true)?;
            {
                let target = SqliteEventLog::open(path).map_err(|error|error.to_string())?;
                target.install_complete_replica_state(&original).map_err(|error|error.to_string())?;
            }
            owned.database.sync_all().map_err(|error|error.to_string())?;
            File::open(path.parent().ok_or("target parent absent")?).and_then(|file|file.sync_all()).map_err(|error|error.to_string())?;
            let captured = SqliteEventLog::read_complete_replica_state(path).map_err(|error|error.to_string())?;
            if captured != original { return Err("actual materialized original rows differ".into()); }
            summary(&captured)
        }
        [action,source] if action == "inspect" => {
            let _leases = leases(Path::new(source),false)?;
            summary(&SqliteEventLog::read_complete_replica_state(source).map_err(|error|error.to_string())?)
        }
        _ => Err("usage: replica_state_probe export SOURCE_SQL CAPTURE_JSON | import CAPTURE_JSON NEW_SQL | inspect ORIGINAL_SQL".into()),
    }
}

/// Emits bounded structured actual process evidence; errors never become a passed or current-use flag.
fn main() {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}
