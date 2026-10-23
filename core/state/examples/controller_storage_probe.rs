//! Explicit actual Controller storage lifetime probe; creates no Node or application authority.

use state::{CheckpointStoragePolicy, ControllerStorageFormat, SqliteEventLog};
use std::io::{Read, Write};

/// Holds an actual SQL clone or explicitly migrates storage without creating application authority.
fn run(arguments: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    match arguments {
        [operation, path] if operation == "observe" => {
            let connection = rusqlite::Connection::open_with_flags(
                path,
                rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
            )?;
            let count: u64 =
                connection.query_row("SELECT COUNT(*) FROM events", [], |row| row.get(0))?;
            println!(
                "{}",
                serde_json::json!({"pid":std::process::id(),"readonly_events":count})
            );
            std::io::stdout().flush()?;
            std::io::stdin().read_exact(&mut [0_u8; 1])?;
            let final_count: u64 =
                connection.query_row("SELECT COUNT(*) FROM events", [], |row| row.get(0))?;
            println!(
                "{}",
                serde_json::json!({"readonly_events_after":final_count})
            );
        }
        [operation, path] if operation == "empty" || operation == "upgrade" => {
            let log = SqliteEventLog::open_controller_with_checkpoint_policy(
                path,
                CheckpointStoragePolicy::Full,
                ControllerStorageFormat::V1,
            )?;
            if operation == "empty" && (!log.is_empty()? || log.load_checkpoint()?.is_some()) {
                return Err("empty probe refuses existing authority".into());
            }
            println!(
                "{}",
                serde_json::json!({"pid":std::process::id(),"events":log.len()?,"checkpoint_schema":log.load_checkpoint()?.map(|value|value.schema),"storage":"roboguide.controller-storage/v1"})
            );
        }
        [operation, path, format] if operation == "hold-clone" => {
            let format = match format.as_str() {
                "legacy" => ControllerStorageFormat::Legacy,
                "1" => ControllerStorageFormat::V1,
                _ => return Err("explicit storage format must be legacy or 1".into()),
            };
            let original = SqliteEventLog::open_controller_with_checkpoint_policy(
                path,
                CheckpointStoragePolicy::Full,
                format,
            )?;
            let clone = original.clone();
            drop(original);
            println!(
                "{}",
                serde_json::json!({"pid":std::process::id(),"original_dropped":true,"actual_clone_held":true})
            );
            std::io::stdout().flush()?;
            std::io::stdin().read_exact(&mut [0_u8; 1])?;
            drop(clone);
        }
        _ => {
            return Err(
                "usage: controller_storage_probe empty NEW_SQL | upgrade ORIGINAL_SQL | observe SQL | hold-clone SQL legacy|1".into(),
            );
        }
    }
    Ok(())
}

/// Reports actual errors without claiming Node registration, process stopping or quorum.
fn main() {
    if let Err(error) = run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        eprintln!("{error}");
        std::process::exit(2);
    }
}
