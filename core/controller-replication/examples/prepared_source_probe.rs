//! Owned staged-file/reader/receive processes; this probe creates no consensus or execution authority.

use artifact_store::{
    ArtifactReplicationSource, FileSystemArtifactStore, PreparedArtifactSourceGuard, digest_bytes,
};
use controller_replication::{ArtifactContentKey, ContentQuorumTarget, ReplicaContentStore};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{BufRead, Read, Write};
use std::path::PathBuf;

/// Explicit actual local roots and original input, independent of devices and deployed capabilities.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    /// Existing actual original receiving Root4.
    receiver: PathBuf,
    /// Original receiving profile declaration, never authenticated consensus evidence.
    profile: String,
    /// Original receiving infrastructure identity.
    member: u64,
    /// Actual original managed source directory.
    source: PathBuf,
    /// Original actual bytes, independently retained by the calling checker.
    payload: PathBuf,
    /// Original source upload identity.
    upload_id: String,
    /// Explicitly reopen the original retained staging file after actual native death.
    resume: bool,
}

/// Closed bounded local operations, without SQL injection, arbitrary shell commands or fake outcomes.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Command {
    /// Establish a new actual explicit original prepared source scope.
    Register,
    /// Read the original retained reader; revoked readers cannot borrow a finalized lookup.
    Read,
    /// Copy actual bytes under the original independent durable receive command.
    Copy {
        /// Complete declared retention context, explicitly not a real election.
        target: Value,
        /// Original immutable receive identity, never retried as new work.
        command_id: String,
    },
    /// Irreversibly revoke the source while retaining the actual original reader.
    Revoke,
    /// Attempt a real source write; live reader scopes must continue to refuse it.
    Append,
    /// Publish only the original exact staged bytes.
    Finalize,
    /// End only this actual software process.
    Shutdown,
}

/// Flush bounded actual observations, always naming this original OS process.
fn emit(mut value: Value) -> Result<(), String> {
    value
        .as_object_mut()
        .ok_or("actual observation must be an object")?
        .insert("pid".into(), json!(std::process::id()));
    println!("{value}");
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Read one bounded original JSON command; EOF never invents a successful shutdown.
fn line(input: &mut impl BufRead) -> Result<Vec<u8>, String> {
    let mut raw = Vec::new();
    input
        .take(16 * 1024 * 1024 + 1)
        .read_until(b'\n', &mut raw)
        .map_err(|error| error.to_string())?;
    if raw.is_empty() || raw.len() > 16 * 1024 * 1024 {
        return Err("original process input ended or exceeded its bound".into());
    }
    Ok(raw)
}

/// Execute real SDK operations and preserve actual process death rather than synthesizing Drop proof.
fn run(config: Config) -> Result<(), String> {
    let file = std::fs::File::open(&config.payload).map_err(|error| error.to_string())?;
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    if !metadata.is_file() || metadata.len() > 8 * 1024 * 1024 {
        return Err("actual probe input exceeds its explicit eight-MiB bound".into());
    }
    let mut bytes = Vec::new();
    file.take(8 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() as u64 != metadata.len() {
        return Err("actual original probe input changed during bounded reading".into());
    }
    let source = FileSystemArtifactStore::new_with_archive_references(&config.source)
        .map_err(|error| error.to_string())?;
    let mut upload = if config.resume {
        source.resume_upload(&config.upload_id)
    } else {
        source.begin_upload(&config.upload_id)
    }
    .map_err(|error| error.to_string())?;
    if !config.resume {
        for chunk in bytes.chunks(64 * 1024) {
            upload
                .write_chunk(chunk)
                .map_err(|error| error.to_string())?;
        }
    }
    let key = ArtifactContentKey {
        source_owner: source
            .owner_id()
            .ok_or("actual source owner absent")?
            .into(),
        content_digest: digest_bytes(&bytes),
        byte_size: bytes.len() as u64,
    };
    let receiver =
        ReplicaContentStore::open_with_streams(&config.receiver, &config.profile, config.member)?;
    let mut guard: Option<PreparedArtifactSourceGuard> = None;
    let mut reader: Option<ArtifactReplicationSource> = None;
    let writer = source
        .writer_identity()
        .map_err(|error| error.to_string())?;
    emit(json!({"ready":true,"content":key,"writer":{
            "owner_id":writer.owner_id,"lease_device":writer.lease_device,
            "lease_inode":writer.lease_inode},"public_cas":source.contains(&key.content_digest)
        .map_err(|error|error.to_string())?}))?;
    let mut input = std::io::stdin().lock();
    loop {
        let command: Command =
            serde_json::from_slice(&line(&mut input)?).map_err(|error| error.to_string())?;
        let shutdown = matches!(command, Command::Shutdown);
        let result = (|| -> Result<Value, String> {
            match command {
                Command::Register => {
                    if guard.is_some() || reader.is_some() {
                        return Err("original actual source scope remains retained".into());
                    }
                    guard = Some(
                        upload
                            .prepared_replication_source(&key.content_digest, key.byte_size)
                            .map_err(|error| error.to_string())?,
                    );
                    reader = Some(
                        source
                            .open_replication_source(&key.content_digest, key.byte_size)
                            .map_err(|error| error.to_string())?,
                    );
                    Ok(
                        json!({"registered":true,"public_cas":source.contains(&key.content_digest)
                        .map_err(|error|error.to_string())?}),
                    )
                }
                Command::Read => {
                    let reader = reader
                        .as_mut()
                        .ok_or("actual original reader is unavailable")?;
                    let mut actual = Vec::new();
                    reader
                        .read_to_end(&mut actual)
                        .map_err(|error| error.to_string())?;
                    Ok(json!({"actual_digest":digest_bytes(&actual),"byte_size":actual.len()}))
                }
                Command::Copy { target, command_id } => {
                    let target: ContentQuorumTarget =
                        serde_json::from_value(target).map_err(|error| error.to_string())?;
                    let status =
                        receiver.copy_artifact_stream(&source, target, &key, &command_id, 30000)?;
                    Ok(
                        json!({"stream":status,"public_cas":source.contains(&key.content_digest)
                        .map_err(|error|error.to_string())?}),
                    )
                }
                Command::Revoke => {
                    drop(guard.take());
                    Ok(json!({"revoked":true,"actual_reader_retained":reader.is_some()}))
                }
                Command::Append => {
                    upload
                        .write_chunk(b"original-probe-tail")
                        .map_err(|error| error.to_string())?;
                    Ok(json!({"appended":true}))
                }
                Command::Finalize => {
                    upload
                        .finalize(&key.content_digest, key.byte_size)
                        .map_err(|error| error.to_string())?;
                    source
                        .verify_artifact(&key.content_digest, key.byte_size)
                        .map_err(|error| error.to_string())?;
                    Ok(json!({"public_cas":true}))
                }
                Command::Shutdown => Ok(json!({"shutdown_requested":true})),
            }
        })();
        emit(match result {
            Ok(result) => json!({"result":result}),
            Err(error) => json!({"error":error}),
        })?;
        if shutdown {
            break;
        }
    }
    Ok(())
}

/// Refuse unbounded config files and retain honest native failure status.
fn main() {
    let result = (|| -> Result<(), String> {
        let path = std::env::args()
            .nth(1)
            .ok_or("explicit original config file required")?;
        let mut raw = Vec::new();
        std::fs::File::open(path)
            .map_err(|error| error.to_string())?
            .take(1024 * 1024 + 1)
            .read_to_end(&mut raw)
            .map_err(|error| error.to_string())?;
        if raw.len() > 1024 * 1024 {
            return Err("original config exceeds its bound".into());
        }
        run(serde_json::from_slice(&raw).map_err(|error| error.to_string())?)
    })();
    if let Err(error) = result {
        let _ = emit(json!({"error":error}));
        std::process::exit(1);
    }
}
