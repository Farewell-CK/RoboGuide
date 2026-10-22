//! Actual-process maintenance fixture for explicit Artifact formats and immutable byte checks.

use artifact_store::{FileSystemArtifactStore, digest_bytes};
use std::io::Read;
use std::path::PathBuf;

/// Opens only the explicitly selected root and optionally stores one bounded real input file.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if !(2..=3).contains(&arguments.len()) {
        return Err("usage: layout_probe <legacy|retained|references> <root> [input-file]".into());
    }
    let root = PathBuf::from(&arguments[1]);
    let store = match arguments[0].as_str() {
        "legacy" => FileSystemArtifactStore::new(&root)?,
        "retained" => FileSystemArtifactStore::new_with_retention(&root)?,
        "references" => FileSystemArtifactStore::new_with_reference_journal(&root)?,
        _ => return Err("explicit artifact layout must be legacy, retained or references".into()),
    };
    let artifact = if let Some(input) = arguments.get(2) {
        let mut bytes = Vec::new();
        std::fs::File::open(input)?
            .take(16 * 1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > 16 * 1024 * 1024 {
            return Err("layout probe input exceeds its actual byte bound".into());
        }
        let digest = digest_bytes(&bytes);
        let mut upload = store.begin_upload("layout-probe")?;
        for chunk in bytes.chunks(64 * 1024) {
            upload.write_chunk(chunk)?;
        }
        let saved = upload.finalize(&digest, bytes.len() as u64)?;
        Some(serde_json::json!({"content_digest": saved.digest(), "byte_size": saved.size()}))
    } else {
        None
    };
    println!(
        "{}",
        serde_json::json!({
            "schema": "roboguide.artifact-layout-process-receipt/v0.1",
            "owner_id": store.owner_id(), "artifact": artifact,
        })
    );
    Ok(())
}
