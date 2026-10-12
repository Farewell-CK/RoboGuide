//! Shared source-document identities; no eligibility, placement or execution authority.

use sha2::{Digest, Sha256};

/// Check one canonical lowercase SHA-256 digest without a prefix.
pub(super) fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

/// Check the digest syntax used for source artifacts and Control attribution.
pub(super) fn valid_prefixed_sha256(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(valid_sha256)
}

/// Replace parsed floating values by exact IEEE-754 bits before hashing.
fn canonical_digest_value(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Number(number) if number.is_f64() => {
            let bits = number.as_f64().expect("floating number has f64").to_bits();
            *value = serde_json::json!({"$f64_bits": format!("{bits:016x}")});
        }
        serde_json::Value::Array(values) => {
            for item in values {
                canonical_digest_value(item);
            }
        }
        serde_json::Value::Object(values) => {
            for item in values.values_mut() {
                canonical_digest_value(item);
            }
        }
        _ => {}
    }
}

/// Compute the v0.3 language-neutral content identity of an evidence body.
pub(super) fn content_digest(body: &serde_json::Value) -> Result<String, String> {
    let mut canonical = body.clone();
    canonical_digest_value(&mut canonical);
    let encoded = serde_json::to_vec(&canonical).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
}
