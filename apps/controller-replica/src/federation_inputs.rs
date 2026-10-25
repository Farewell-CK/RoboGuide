//! Explicit bounded Federation deployment inputs; application v60 retains all business authority.

use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::io::Read as _;
use std::path::{Path, PathBuf};

/// Closed opt-in child configuration, distinct from inherited environment or a domain registry.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct FederationInputs {
    /// Exact explicit deployment interpretation; no default grants exist.
    schema: String,
    /// Immutable declared local application domain.
    domain: String,
    /// Existing bounded application authorization policy JSON.
    authorization: PathBuf,
    /// Existing root peer profile; absent for participant-only processes.
    #[serde(default)]
    peers: Option<PathBuf>,
}

/// Reads one actual explicit regular file within its original deployment bound.
fn document(path: &Path) -> Result<(Vec<u8>, Value), String> {
    if !path.is_absolute() || path.as_os_str().len() > 4096 {
        return Err("Federation process inputs require explicit bounded absolute paths".into());
    }
    let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("Federation process input is not a regular file".into());
    }
    let mut raw = Vec::new();
    file.take(1024 * 1024 + 1)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    if raw.is_empty() || raw.len() > 1024 * 1024 {
        return Err("Federation process input exceeds its original byte bound".into());
    }
    let parsed = serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
    Ok((raw, parsed))
}

impl FederationInputs {
    /// Recognizes only locally supported generations which already own Federation authority.
    pub(super) fn supports(schema: &str) -> bool {
        controller_replication::supports_application_generation(schema)
            && schema
                .strip_prefix("roboguide.controller-checkpoint/v")
                .and_then(|version| version.parse::<u32>().ok())
                .is_some_and(|version| version >= 60)
    }
    /// Binds actual initial policy bytes without exporting grants or private credential contents.
    pub(super) fn identity(&self) -> Result<Value, String> {
        if self.schema != "roboguide.federation-process-inputs/v0.1"
            || self.domain.is_empty()
            || self.domain.len() > 128
            || self.domain.trim() != self.domain
            || self
                .domain
                .chars()
                .any(|value| value.is_control() || matches!(value, '/' | '?' | '#' | '*'))
        {
            return Err("invalid explicit Federation process inputs".into());
        }
        let (authorization, policy) = document(&self.authorization)?;
        if !matches!(
            policy["schema_version"].as_str(),
            Some(
                "roboguide.federation-authorization/v0.1"
                    | "roboguide.federation-authorization/v0.2"
            )
        ) || policy["local_domain"].as_str() != Some(self.domain.as_str())
        {
            return Err(
                "Federation process authorization changed its declared domain/schema".into(),
            );
        }
        let peers = self.peers.as_deref().map(document).transpose()?;
        if peers.as_ref().is_some_and(|(_, profile)| {
            profile["schema_version"] != "roboguide.federation-peers/v0.1"
                || profile["local_domain"].as_str() != Some(self.domain.as_str())
        }) {
            return Err(
                "Federation process peer profile changed its declared domain/schema".into(),
            );
        }
        Ok(serde_json::json!({
            "schema":self.schema,"domain":self.domain,
            "authorization_sha256":format!("{:x}",Sha256::digest(authorization)),
            "peers_sha256":peers.map(|(raw,_)|format!("{:x}",Sha256::digest(raw)))
        }))
    }

    /// Installs only this explicit declaration after the original child environment is cleared.
    pub(super) fn configure(&self, command: &mut tokio::process::Command) {
        command
            .env("ROBOGUIDE_CONTROL_DOMAIN", &self.domain)
            .env("ROBOGUIDE_FEDERATION_DOMAIN_GRANTS", &self.authorization);
        if let Some(peers) = &self.peers {
            command.env("ROBOGUIDE_FEDERATION_PEERS", peers);
        }
    }
}
