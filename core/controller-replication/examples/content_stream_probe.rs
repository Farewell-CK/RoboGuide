//! Actual original bounded storage streams; declared retention targets never prove consensus or Nodes.

use controller_replication::{ArtifactContentKey, ContentQuorumTarget, ReplicaContentStore};
use serde::Deserialize;
use serde_json::{Value, json};
use std::io::{Read, Write};
use std::path::PathBuf;

/// Closed actual infrastructure store declaration, independent of devices and capabilities.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Store {
    /// Actual original private filesystem root.
    root: PathBuf,
    /// Original declared infrastructure profile digest.
    profile: String,
    /// Infrastructure member identity, never an embodied Node.
    member: u64,
}

/// Closed operations over real owned files and native processes, without arbitrary SQL/shell execution.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Creates only a fresh explicitly opted-in stream root.
    Create {
        /// Exact declared actual receiver.
        store: Store,
    },
    /// Explicitly drains/upgrades an original producer root without rewriting existing evidence.
    Upgrade {
        /// Original actual producer root.
        store: Store,
    },
    /// Executes the maintained original local stream API from an actual managed Artifact source.
    Copy {
        /// Original existing actual receiver.
        store: Store,
        /// Actual existing owned source CAS.
        source: PathBuf,
        /// Complete original qualified input.
        content: ArtifactContentKey,
        /// Declared retention context, explicitly no authenticated consensus authority.
        target: Box<Value>,
        /// Original immutable operation identity.
        command_id: String,
        /// Original actual process/kernel effect duration.
        budget_ms: u64,
        /// Hold the actual store after publication for an independent native response-loss kill.
        hold_after: bool,
    },
    /// Reads retained original SQL and independently verifies actual full qualified bytes.
    Inspect {
        /// Original existing actual receiver.
        store: Store,
        /// Complete original content identity.
        content: ArtifactContentKey,
        /// Bounded original operation IDs, never new receive commands.
        commands: Vec<String>,
    },
    /// Holds the original real managed root until its owned parent closes stdin.
    Hold {
        /// Original existing actual receiver.
        store: Store,
    },
}

/// Opens only the explicit accepted stream generation; missing authority never initializes a root.
fn open(store: &Store) -> Result<ReplicaContentStore, String> {
    ReplicaContentStore::open_with_streams(&store.root, &store.profile, store.member)
}

/// Emits bounded structured actual observations immediately, without interpreting stdout as authority.
fn emit(value: Value) -> Result<(), String> {
    println!(
        "{}",
        serde_json::to_string(&value).map_err(|error| error.to_string())?
    );
    std::io::stdout().flush().map_err(|error| error.to_string())
}

/// Waits only inside this actual owned process; SIGKILL never fabricates a close or completion receipt.
fn hold() -> Result<(), String> {
    let mut input = String::new();
    std::io::stdin()
        .take(32)
        .read_to_string(&mut input)
        .map_err(|error| error.to_string())?;
    if input.trim() != "finish" {
        return Err("actual original stream probe received no finish".into());
    }
    Ok(())
}

/// Executes actual storage calls only; local copies create no network, quorum, use or device proof.
fn run(request: Request) -> Result<Value, String> {
    match request {
        Request::Create { store } => Ok(json!({
            "identity":ReplicaContentStore::create_with_streams(
                &store.root,&store.profile,store.member
            )?.identity()
        })),
        Request::Upgrade { store } => Ok(json!({
            "identity":ReplicaContentStore::upgrade_stream_journal(
                &store.root,&store.profile,store.member
            )?.identity()
        })),
        Request::Copy {
            store,
            source,
            content,
            target,
            command_id,
            budget_ms,
            hold_after,
        } => {
            let receiver = open(&store)?;
            let source =
                artifact_store::FileSystemArtifactStore::new_with_archive_references(source)
                    .map_err(|error| error.to_string())?;
            let original = receiver.copy_artifact_stream(
                &source,
                serde_json::from_value::<ContentQuorumTarget>(*target)
                    .map_err(|error| error.to_string())?,
                &content,
                &command_id,
                budget_ms,
            )?;
            if hold_after {
                emit(json!({"pid":std::process::id(),"ready":true,"original":original}))?;
                hold()?;
            }
            Ok(json!({"stream":original}))
        }
        Request::Inspect {
            store,
            content,
            commands,
        } => {
            if commands.len() > 32 {
                return Err("original stream inspection exceeds its explicit bound".into());
            }
            let store = open(&store)?;
            let originals = commands
                .iter()
                .map(|command| store.stream_status(command))
                .collect::<Result<Vec<_>, _>>()?;
            Ok(json!({
                "identity":store.identity(),"streams":originals,
                "actual_bytes":store.verify_content(&content).is_ok(),
                "references":store.active_references(&content)?
            }))
        }
        Request::Hold { store } => {
            let store = open(&store)?;
            emit(json!({"pid":std::process::id(),"ready":true,"identity":store.identity()}))?;
            hold()?;
            Ok(json!({"identity":store.identity()}))
        }
    }
}

/// Parses one bounded immutable request file and retains honest native failure codes.
fn main() {
    let result = (|| -> Result<Value, String> {
        let path = std::env::args()
            .nth(1)
            .ok_or("explicit original request file required")?;
        let mut raw = Vec::new();
        std::fs::File::open(path)
            .map_err(|error| error.to_string())?
            .take(16 * 1024 * 1024 + 1)
            .read_to_end(&mut raw)
            .map_err(|error| error.to_string())?;
        if raw.len() > 16 * 1024 * 1024 {
            return Err("original request exceeds its bound".into());
        }
        run(serde_json::from_slice(&raw).map_err(|error| error.to_string())?)
    })();
    match result {
        Ok(result) => {
            if emit(json!({"result":result})).is_err() {
                std::process::exit(1);
            }
        }
        Err(error) => {
            let _ = emit(json!({"error":error}));
            std::process::exit(1);
        }
    }
}
