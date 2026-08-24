//! User-owned Node Service configuration.

use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::Path;

/// Complete configuration loaded when `roboguide-node` starts.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeServiceConfig {
    /// Stable node identity advertised to RoboGuide.
    pub node_id: String,
    /// Formal gRPC endpoint such as `http://127.0.0.1:50051`.
    pub server_endpoint: String,
    /// Delay before reconnect after transport loss.
    #[serde(default = "default_reconnect_delay_ms")]
    pub reconnect_delay_ms: u64,
    /// Selected Local EAIOS Adapter and its local-only configuration.
    pub adapter: AdapterConfig,
}

/// Adapter selection and opaque local-only settings.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct AdapterConfig {
    /// Adapter factory key, such as `fake`, `robonix`, or `ros2`.
    #[serde(rename = "type")]
    pub adapter_type: String,
    /// Adapter-owned configuration ignored by Node Service core.
    #[serde(flatten)]
    pub settings: BTreeMap<String, toml::Value>,
}

impl NodeServiceConfig {
    /// Loads and strictly validates one TOML configuration file.
    pub fn load(path: &Path) -> Result<Self, std::io::Error> {
        let source = std::fs::read_to_string(path)?;
        toml::from_str(&source)
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
    }
}

/// Returns the default reconnect backoff.
const fn default_reconnect_delay_ms() -> u64 {
    1_000
}
