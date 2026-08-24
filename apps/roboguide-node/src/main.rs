#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Long-running RoboGuide Node Service process.

use node_service::{FakeAdapter, NodeService, NodeServiceConfig};
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Loads configuration, constructs the selected adapter, and reconnects forever.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
    let config = NodeServiceConfig::load(&path)?;
    if config.adapter.adapter_type != "fake" {
        return Err(format!(
            "adapter type {} is not installed",
            config.adapter.adapter_type
        )
        .into());
    }
    let runtime_name = text_setting(&config.adapter.settings, "runtime_name")?;
    let runtime_version = text_setting(&config.adapter.settings, "runtime_version")?;
    let adapter = FakeAdapter::new(runtime_name, runtime_version, BTreeMap::new());
    NodeService::new(config, adapter).run().await?;
    Ok(())
}

/// Reads one required text value owned by the selected adapter factory.
fn text_setting(
    settings: &BTreeMap<String, toml::Value>,
    key: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    settings
        .get(key)
        .and_then(toml::Value::as_str)
        .map(ToString::to_string)
        .ok_or_else(|| format!("adapter setting {key} must be text").into())
}
