#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Single, configuration-driven RoboGuide Node Service process.

use node_service::{
    GrpcDriver, HttpDriver, LocalDriver, LocalIntegrationEngine, McpDriver, NodeService,
    NodeServiceConfig,
};
use std::path::PathBuf;
use std::sync::Arc;

/// Loads one immutable local catalog, installs generic drivers, and reconnects forever.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args()
        .nth(1)
        .map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
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
