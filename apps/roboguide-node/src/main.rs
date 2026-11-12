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
    let mut first = arguments.next();
    let mut observations = Vec::new();
    while first.as_deref() == Some("--observe-unadmitted-cancellation") {
        let identity = arguments
            .next()
            .ok_or("exact cancellation identity required")?;
        if identity.trim().is_empty() || identity.len() > 1024 || observations.len() >= 16 {
            return Err("invalid exact cancellation observation".into());
        }
        observations.push(identity);
        first = arguments.next();
    }
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
    let mut service = NodeService::new(engine);
    for identity in observations {
        service = service.with_unadmitted_cancellation_observation(identity)?;
    }
    service.run().await?;
    Ok(())
}
