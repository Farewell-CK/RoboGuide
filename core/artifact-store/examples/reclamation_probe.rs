//! Actual read-only GC boundary observer; it neither creates a backend nor grants content access.

use artifact_store::{
    ARTIFACT_GC_LAYOUT_SCHEMA, ArtifactReadOnlyStore, ArtifactStoreError, digest_reader,
};
use std::io::Read;
use std::path::Path;

/// Inspects one existing v5 owner under a shared original lease and bounds every actual byte read.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 3 {
        return Err("usage: reclamation_probe <root> <digest> <size>".into());
    }
    let size = args[2].parse::<u64>()?;
    if size > 16 * 1024 * 1024 {
        return Err("original read observation exceeds its byte bound".into());
    }
    let store = ArtifactReadOnlyStore::open(Path::new(&args[0]))?;
    if store.storage_schema() != ARTIFACT_GC_LAYOUT_SCHEMA {
        return Err("explicit existing storage v5 is required".into());
    }
    let readable = match store.open_artifact(&args[1]) {
        Ok(file) => {
            if file.metadata()?.len() != size {
                return Err("actual content length differs".into());
            }
            let mut bounded = file.take(size + 1);
            if digest_reader(&mut bounded)? != args[1] {
                return Err("actual content identity differs".into());
            }
            true
        }
        Err(ArtifactStoreError::ArtifactNotFound { .. }) => false,
        Err(error) => return Err(error.into()),
    };
    println!(
        "{}",
        serde_json::json!({"schema":"roboguide.artifact-gc-read-observation/v0.1",
        "owner_id":store.owner_id(),"storage_schema":store.storage_schema(),"readable":readable})
    );
    Ok(())
}
