//! Canonical SHA-256 parsing and streaming digest calculation.

use crate::path_validation::io_error;
use crate::types::ArtifactStoreError;
use sha2::{Digest, Sha256};
use std::io::Read;

/// Prefix used by the canonical digest representation.
pub(crate) const DIGEST_PREFIX: &str = "sha256:";
/// Number of hexadecimal characters in a SHA-256 digest.
const DIGEST_HEX_LENGTH: usize = 64;

/// Normalizes accepted digest forms to canonical `sha256:<lowercase hex>` text.
pub fn normalize_digest(value: &str) -> Result<String, ArtifactStoreError> {
    let raw = value.strip_prefix(DIGEST_PREFIX).unwrap_or(value);
    if raw.len() != DIGEST_HEX_LENGTH || !raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(ArtifactStoreError::InvalidDigest {
            value: value.to_string(),
        });
    }
    let lowercase = raw.to_ascii_lowercase();
    if raw != lowercase {
        return Err(ArtifactStoreError::InvalidDigest {
            value: value.to_string(),
        });
    }
    Ok(format!("{DIGEST_PREFIX}{lowercase}"))
}

/// Calculates a canonical SHA-256 digest for an in-memory chunk sequence.
pub fn digest_bytes(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    format!("{DIGEST_PREFIX}{digest:x}")
}

/// Calculates a canonical SHA-256 digest while reading all bytes from a source.
pub fn digest_reader<R: Read>(reader: &mut R) -> Result<String, ArtifactStoreError> {
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|error| io_error("digest artifact", error))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(digest_hasher(&hasher))
}

/// Converts an incremental SHA-256 state into the canonical digest string.
pub(crate) fn digest_hasher(hasher: &Sha256) -> String {
    let digest = hasher.clone().finalize();
    format!("{DIGEST_PREFIX}{digest:x}")
}
