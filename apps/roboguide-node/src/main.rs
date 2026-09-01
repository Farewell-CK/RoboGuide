#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Single, configuration-driven RoboGuide Node Service process.

use node_service::{
    GrpcDriver, HttpDriver, LocalDriver, LocalIntegrationEngine, McpDriver, NodeService,
    NodeServiceConfig, compile_extension_config_json,
};
use std::path::PathBuf;
use std::sync::Arc;

/// Validates an offline extension catalog or runs one immutable catalog with generic drivers.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let first = arguments.next();
    if matches!(first.as_deref(), Some("--validate" | "conformance")) {
        let path = arguments
            .next()
            .map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
        println!("{}", compile_extension_config_json(&path)?);
        return Ok(());
    }
    let path = first.map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
    let catalog = NodeServiceConfig::load_compiled(&path)?;
    let drivers: Vec<Arc<dyn LocalDriver>> = vec![
        Arc::new(HttpDriver::new()?),
        Arc::new(GrpcDriver::new()),
        Arc::new(McpDriver::new()?),
    ];
    let engine = LocalIntegrationEngine::new(catalog, drivers)?;
    NodeService::new(engine).run().await?;
    Ok(())
}
