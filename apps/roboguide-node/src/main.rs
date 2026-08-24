#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Long-running RoboGuide Node Service process.

use node_service::{
    FakeAdapter, NodeService, NodeServiceConfig, RobonixAdapter, RobonixCommandClient,
};
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Loads configuration, constructs the selected adapter, and reconnects forever.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
    let config = NodeServiceConfig::load(&path)?;
    match config.adapter.adapter_type.as_str() {
        "fake" => {
            let runtime_name = text_setting(&config.adapter.settings, "runtime_name")?;
            let runtime_version = text_setting(&config.adapter.settings, "runtime_version")?;
            NodeService::new(
                config,
                FakeAdapter::new(runtime_name, runtime_version, BTreeMap::new()),
            )
            .run()
            .await?;
        }
        "robonix" => {
            let python = PathBuf::from(text_setting(&config.adapter.settings, "python")?);
            let bridge = PathBuf::from(text_setting(&config.adapter.settings, "bridge_script")?);
            let atlas = text_setting(&config.adapter.settings, "atlas_endpoint")?;
            NodeService::new(
                config,
                RobonixAdapter::new(RobonixCommandClient::new(python, bridge, atlas)),
            )
            .run()
            .await?;
        }
        other => return Err(format!("adapter type {other} is not installed").into()),
    }
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
