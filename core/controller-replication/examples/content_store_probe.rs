//! Exercise actual member-local content files/processes; never create embodied Nodes or quorum facts.

use artifact_store::FileSystemArtifactStore;
use controller_replication::{ArtifactContentKey, ReplicaContentStore};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// Closed actual member storage declaration; it contains no device/capability inventory.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Store {
    /// Explicit owned actual filesystem root.
    root: PathBuf,
    /// Explicit frozen actual infrastructure profile digest.
    profile: String,
    /// Explicit actual infrastructure member, never an embodied Node identity.
    member: u64,
}

/// Explicit probe operations; no arbitrary shell, SQL or product execution is accepted.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Creates actual immutable original Artifact bytes from an explicit real payload file.
    Source {
        /// Declared fresh actual source CAS.
        root: PathBuf,
        /// Actual owned regular payload input.
        payload: PathBuf,
    },
    /// Creates only the explicitly declared fresh infrastructure content store.
    Create {
        /// Actual member/root/profile inputs.
        store: Store,
    },
    /// Actually copies bytes under an original immutable command; duplicate IDs only observe.
    Copy {
        /// Actual target member/root/profile.
        store: Store,
        /// Actual original source CAS root.
        source: PathBuf,
        /// Exact original immutable content.
        content: ArtifactContentKey,
        /// Original receiving command.
        command_id: String,
        /// Frozen original producer identity.
        authority: String,
        /// Nonrenewing actual receive duration.
        budget_ms: u64,
    },
    /// Independently reads original SQL history and actual target CAS bytes.
    Inspect {
        /// Actual member/root/profile inputs.
        store: Store,
        /// Exact original content to inspect.
        content: ArtifactContentKey,
        /// Bounded original copy IDs; inspection never copies.
        copies: Vec<String>,
        /// Bounded original reader IDs; inspection never reopens readers.
        readers: Vec<String>,
    },
    /// Opens an actual managed reader, optionally holding its original file/leases until stdin.
    Read {
        /// Actual member/root/profile inputs.
        store: Store,
        /// Exact original immutable content.
        content: ArtifactContentKey,
        /// Original one-shot reader identity.
        reader_id: String,
        /// Original producer identity, not semantic read permission.
        authority: String,
        /// Nonrenewing actual reader duration.
        budget_ms: u64,
        /// Explicit prefix length; absent reads the complete actual file.
        prefix: Option<u64>,
        /// Hold this actual original managed handle after reading the declared prefix.
        hold: bool,
    },
    /// Separately retires an original uncertain copier after actual process absence.
    RetireCopy {
        /// Actual member/root/profile.
        store: Store,
        /// Exact original receiving operation.
        command_id: String,
    },
    /// Separately retires an original uncertain reader after actual process absence.
    RetireReader {
        /// Actual member/root/profile.
        store: Store,
        /// Exact original reader operation.
        reader_id: String,
    },
    /// Holds only an actual store clone to independently check original writer leases.
    Hold {
        /// Actual member/root/profile.
        store: Store,
    },
    /// Checks that both ordinary Artifact constructors reject the actual private infrastructure root.
    ArtifactFence {
        /// Actual original infrastructure storage root.
        root: PathBuf,
    },
}

/// Opens exactly the actual original member storage; no loss silently creates replacement authority.
fn open(store: &Store) -> Result<ReplicaContentStore, String> {
    ReplicaContentStore::open(&store.root, &store.profile, store.member)
}

/// Prints bounded structured evidence immediately so an independent parent can observe real PID/I/O.
fn emit(value: &Value) -> Result<(), String> {
    println!(
        "{}",
        serde_json::to_string(value).map_err(|error| error.to_string())?
    );
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Waits only for the owned parent to finish this probe; a killed process has no invented close receipt.
fn hold() -> Result<(), String> {
    let mut input = String::new();
    std::io::stdin()
        .take(32)
        .read_to_string(&mut input)
        .map_err(|error| error.to_string())?;
    if input.trim() != "finish" {
        return Err("original probe received no explicit finish".into());
    }
    Ok(())
}

/// Streams one actual owned regular input to the maintained immutable Artifact writer.
fn source(root: &Path, payload: &Path) -> Result<Value, String> {
    if root.exists() {
        return Err("source creation requires an explicit fresh root".into());
    }
    let mut file = std::fs::File::open(payload).map_err(|error| error.to_string())?;
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    if !metadata.is_file() || metadata.len() > 1024 * 1024 * 1024 {
        return Err("source payload exceeds its actual regular-file bound".into());
    }
    let store = FileSystemArtifactStore::new_with_archive_references(root)
        .map_err(|error| error.to_string())?;
    let mut upload = store
        .begin_upload("actual-content-process-source")
        .map_err(|error| error.to_string())?;
    let mut hash = Sha256::new();
    let mut size = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        size += count as u64;
        if size > metadata.len() {
            return Err("actual source file grew during publication".into());
        }
        hash.update(&buffer[..count]);
        upload
            .write_chunk(&buffer[..count])
            .map_err(|error| error.to_string())?;
    }
    let digest = format!("sha256:{:x}", hash.finalize());
    if size != metadata.len() {
        return Err("actual source file changed size".into());
    }
    upload
        .finalize(&digest, size)
        .map_err(|error| error.to_string())?;
    Ok(
        json!({"content":ArtifactContentKey{source_owner:store.owner_id().ok_or("source owner absent")?.into(),content_digest:digest,byte_size:size}}),
    )
}

/// Runs maintained storage operations only; observations never grant application, quorum or device facts.
fn run(request: Request) -> Result<Value, String> {
    match request {
        Request::Source { root, payload } => source(&root, &payload),
        Request::Create { store } => Ok(
            json!({"identity":ReplicaContentStore::create(&store.root,&store.profile,store.member)?.identity()}),
        ),
        Request::Copy {
            store,
            source,
            content,
            command_id,
            authority,
            budget_ms,
        } => {
            let target = open(&store)?;
            let source = FileSystemArtifactStore::new_with_archive_references(source)
                .map_err(|error| error.to_string())?;
            Ok(
                json!({"copy":target.copy_from_artifact(&source,&content,&command_id,&authority,budget_ms)?}),
            )
        }
        Request::Inspect {
            store,
            content,
            copies,
            readers,
        } => {
            if copies.len() > 32 || readers.len() > 32 {
                return Err("inspection exceeds its explicit operation bound".into());
            }
            let store = open(&store)?;
            let copies = copies
                .into_iter()
                .map(|id| {
                    store
                        .copy_status(&id)
                        .map(|status| json!({"id":id,"status":status}))
                })
                .collect::<Result<Vec<_>, _>>()?;
            let readers = readers
                .into_iter()
                .map(|id| {
                    store
                        .reader_status(&id)
                        .map(|status| json!({"id":id,"status":status}))
                })
                .collect::<Result<Vec<_>, _>>()?;
            Ok(
                json!({"identity":store.identity(),"copies":copies,"readers":readers,"references":store.active_references(&content)?,"actual_bytes":store.verify_content(&content).is_ok()}),
            )
        }
        Request::Read {
            store,
            content,
            reader_id,
            authority,
            budget_ms,
            prefix,
            hold: wait,
        } => {
            let store = open(&store)?;
            let mut reader = store.open_reader(&content, &reader_id, &authority, budget_ms)?;
            let mut sink = std::io::sink();
            let count = if let Some(prefix) = prefix {
                std::io::copy(&mut reader.by_ref().take(prefix), &mut sink)
            } else {
                std::io::copy(&mut reader, &mut sink)
            }
            .map_err(|error| error.to_string())?;
            if wait {
                emit(
                    &json!({"ready":true,"pid":std::process::id(),"reader":store.reader_status(&reader_id)?,"references":store.active_references(&content)?,"bytes_read":count}),
                )?;
                drop(store);
                hold()?;
            }
            Ok(json!({"reader_receipt":reader.finish()?}))
        }
        Request::RetireCopy { store, command_id } => {
            Ok(json!({"copy":open(&store)?.retire_ended_copy(&command_id)?}))
        }
        Request::RetireReader { store, reader_id } => {
            Ok(json!({"reader":open(&store)?.retire_ended_reader(&reader_id)?}))
        }
        Request::Hold { store } => {
            let store = open(&store)?;
            let retained = store.clone();
            emit(&json!({"ready":true,"identity":retained.identity(),"pid":std::process::id()}))?;
            drop(store);
            hold()?;
            drop(retained);
            Ok(json!({"closed":true}))
        }
        Request::ArtifactFence { root } => Ok(
            json!({"default_refused":FileSystemArtifactStore::new(&root).is_err(),"archive_refused":FileSystemArtifactStore::new_with_archive_references(&root).is_err()}),
        ),
    }
}

/// Reads one explicit bounded JSON request file; no ambient operation or default device is created.
fn main() {
    let result = (|| -> Result<Value, String> {
        let arguments = std::env::args().skip(1).collect::<Vec<_>>();
        if arguments.len() != 1 {
            return Err("usage: content_store_probe REQUEST_JSON".into());
        }
        let file = std::fs::File::open(&arguments[0]).map_err(|error| error.to_string())?;
        if !file
            .metadata()
            .map_err(|error| error.to_string())?
            .is_file()
        {
            return Err("request must be an actual regular file".into());
        }
        let mut raw = Vec::new();
        file.take(1024 * 1024 + 1)
            .read_to_end(&mut raw)
            .map_err(|error| error.to_string())?;
        if raw.len() > 1024 * 1024 {
            return Err("request exceeds its bound".into());
        }
        run(serde_json::from_slice(&raw).map_err(|error| error.to_string())?)
    })();
    match result {
        Ok(result) => {
            if emit(&json!({"result":result})).is_err() {
                std::process::exit(2);
            }
        }
        Err(error) => {
            let _ = emit(&json!({"error":error}));
            std::process::exit(2);
        }
    }
}
