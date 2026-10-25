//! Explicit parent11 overlay; old binaries refuse it even before the first Archive operation.

use crate::store::{read_meta, write_meta};
use rusqlite::{Connection, TransactionBehavior};

/// Independent physical writer generation; application/Node/checkpoint generations do not change.
pub(crate) const VERSION: i64 = 11;
/// Mandatory closed physical marker.
pub(crate) const SCHEMA: &str = "roboguide.replica-storage/v0.11";
/// Original immediate parent interpretation, retained through application upgrade and cold restore.
const BASE_KEY: &str = "coordinated_archive_parent_base";
/// Mandatory zero-operation capability declaration.
const CONTRACT_KEY: &str = "coordinated_archive_contract";
/// Explicit maintenance protocol, never inferred from a directory or an accepted checkpoint.
const CONTRACT: &str = "roboguide.coordinated-content-archive/v0.1";

/// Reads exact physical generation without replacing an absent or malformed marker.
pub(crate) fn physical(connection: &Connection) -> Result<i64, String> {
    connection
        .query_row("PRAGMA user_version", [], |r| r.get(0))
        .map_err(|e| e.to_string())
}

/// Checks the complete outer fence and returns only its immutable original base interpretation.
pub(crate) fn effective(connection: &Connection) -> Result<i64, String> {
    let version = physical(connection)?;
    if version != VERSION {
        for key in [BASE_KEY, CONTRACT_KEY] {
            let table: bool = connection
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='replica_v1_meta')",
                    [],
                    |r| r.get(0),
                )
                .map_err(|e| e.to_string())?;
            if table
                && read_meta::<serde_json::Value>(connection, key)
                    .map_err(|e| e.to_string())?
                    .is_some()
            {
                return Err("old parent contains hidden coordinated Archive authority".into());
            }
        }
        return Ok(version);
    }
    let marker: (i64, String) = connection
        .query_row(
            "SELECT version,schema FROM replica_storage_format",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .map_err(|e| e.to_string())?;
    let base: i64 = read_meta(connection, BASE_KEY)
        .map_err(|e| e.to_string())?
        .ok_or("coordinated Archive original base is absent")?;
    if marker != (VERSION, SCHEMA.into())
        || !matches!(base, 8..=10)
        || read_meta::<String>(connection, CONTRACT_KEY)
            .map_err(|e| e.to_string())?
            .as_deref()
            != Some(CONTRACT)
    {
        return Err("coordinated Archive parent marker/base/contract differs".into());
    }
    Ok(base)
}

/// Adds the explicit closed fence only under actual already-acquired original parent/inode leases.
pub(crate) fn install(connection: &mut Connection) -> Result<(), String> {
    let version = effective(connection)?;
    if physical(connection)? == VERSION {
        return Ok(());
    }
    if !matches!(version, 8..=10) {
        return Err("coordinated Archive requires original owned parent8/9/10".into());
    }
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|e| e.to_string())?;
    write_meta(&transaction, BASE_KEY, &version).map_err(|e| e.to_string())?;
    write_meta(&transaction, CONTRACT_KEY, &CONTRACT).map_err(|e| e.to_string())?;
    transaction.execute_batch("DROP TABLE replica_storage_format; CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=11),schema TEXT NOT NULL); INSERT INTO replica_storage_format VALUES(11,'roboguide.replica-storage/v0.11'); PRAGMA user_version=11;").map_err(|e|e.to_string())?;
    transaction.commit().map_err(|e| e.to_string())
}

/// Preserves parent11 while an explicitly authorized application upgrade adds its original parent10.
pub(crate) fn advance_upgrade_base(connection: &Connection) -> Result<bool, String> {
    if physical(connection)? != VERSION {
        return Ok(false);
    }
    effective(connection)?;
    write_meta(connection, BASE_KEY, &10_i64).map_err(|e| e.to_string())?;
    Ok(true)
}
