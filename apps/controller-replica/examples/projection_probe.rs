#![deny(missing_docs, clippy::missing_docs_in_private_items)]
#![forbid(unsafe_code)]

//! Invokes the actual inactive projection composition only; never creates quorum or Node facts.

#[path = "../src/projection.rs"]
mod projection;

/// Reads only a bounded regular local data file; validation still precedes target writer admission.
fn read_json<T: serde::de::DeserializeOwned>(
    path: &std::ffi::OsStr,
) -> Result<T, Box<dyn std::error::Error>> {
    use std::io::Read as _;
    let file = std::fs::File::open(path)?;
    if !file.metadata()?.is_file() || file.metadata()?.len() > 256 * 1024 * 1024 {
        return Err("projection input requires a bounded regular file".into());
    }
    let mut bytes = Vec::new();
    file.take(256 * 1024 * 1024 + 1).read_to_end(&mut bytes)?;
    if bytes.len() > 256 * 1024 * 1024 {
        return Err("projection input grew beyond bounds".into());
    }
    Ok(serde_json::from_slice(&bytes)?)
}

/// Imports an explicitly provided view through the actual composition, with no execution authority.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() == 4 && arguments[0] == "--complete-original" {
        let original = read_json(&arguments[2])?;
        projection::install_original(
            std::path::Path::new(&arguments[1]),
            &original,
            format(&arguments[3])?,
        )?;
        println!(
            "{}",
            serde_json::json!({"local_projection_installed":true,"quorum_authority":false,"execution_authority":false})
        );
        return Ok(());
    }
    if arguments.len() != 5 {
        return Err("usage: projection_probe <owned-directory> <view.json> <legacy|1> <application-schema> <profile>".into());
    }
    let view = read_json(&arguments[1])?;
    let format = format(&arguments[2])?;
    projection::materialize(
        std::path::Path::new(&arguments[0]),
        &view,
        format,
        arguments[3].to_str().ok_or("schema is not UTF-8")?,
        arguments[4].to_str().ok_or("profile is not UTF-8")?,
    )?;
    println!(
        "{}",
        serde_json::json!({"local_projection_installed":true,"quorum_authority":false,"execution_authority":false})
    );
    Ok(())
}

/// Requires the original explicit storage declaration, never choosing or downgrading it from the bytes.
fn format(
    value: &std::ffi::OsStr,
) -> Result<state::ControllerStorageFormat, Box<dyn std::error::Error>> {
    Ok(match value.to_str() {
        Some("legacy") => state::ControllerStorageFormat::Legacy,
        Some("1") => state::ControllerStorageFormat::V1,
        _ => return Err("projection requires explicit legacy or 1 storage".into()),
    })
}
