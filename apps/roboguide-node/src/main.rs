#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Single, configuration-driven RoboGuide Node Service process.

use node_service::{
    GrpcDriver, HttpDriver, LocalDriver, LocalIntegrationEngine, McpDriver, NodePlanPreflight,
    NodeService, NodeServiceConfig, compile_extension_config_json,
};
use std::path::PathBuf;
use std::sync::Arc;

/// Validates an offline extension catalog or runs one immutable catalog with generic drivers.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let mut first = arguments.next();
    let mut observations = Vec::new();
    while first.as_deref().and_then(std::ffi::OsStr::to_str)
        == Some("--observe-unadmitted-cancellation")
    {
        let identity = arguments
            .next()
            .ok_or("exact cancellation identity required")?
            .into_string()
            .map_err(|_| "invalid exact cancellation observation")?;
        if identity.trim().is_empty() || identity.len() > 1024 || observations.len() >= 16 {
            return Err("invalid exact cancellation observation".into());
        }
        observations.push(identity);
        first = arguments.next();
    }
    if matches!(
        first.as_deref().and_then(std::ffi::OsStr::to_str),
        Some("--validate" | "conformance")
    ) {
        let path = arguments
            .next()
            .map_or_else(|| PathBuf::from("config/node.toml"), PathBuf::from);
        let mut paths = vec![path.clone()];
        let mut plan_path = None;
        let mut complete_sources = false;
        while let Some(flag) = arguments.next() {
            match flag.to_str() {
                Some("--source-profile") if !complete_sources => {
                    if arguments
                        .next()
                        .as_deref()
                        .and_then(std::ffi::OsStr::to_str)
                        != Some("complete-sources/v0.1")
                    {
                        return Err("unsupported Node preflight source profile".into());
                    }
                    complete_sources = true;
                }
                Some("--plan") if plan_path.is_none() => {
                    plan_path = Some(arguments.next().ok_or("--plan requires a plan path")?);
                }
                Some("--node-config") => paths.push(PathBuf::from(
                    arguments
                        .next()
                        .ok_or("--node-config requires a config path")?,
                )),
                _ => return Err("expected one --plan and optional --node-config paths".into()),
            }
        }
        if let Some(plan_path) = plan_path {
            let body = node_service::read_plan_source(std::path::Path::new(&plan_path))?;
            let plan = orchestration::decode_mission_plan(std::str::from_utf8(&body)?)?;
            let preflight = if complete_sources {
                NodePlanPreflight::load_complete(&paths, true)?
            } else {
                NodePlanPreflight::load_conformant(&paths)?
            };
            let assessment = preflight.assess(&plan, &body);
            println!("{}", serde_json::to_string_pretty(&assessment)?);
            if assessment["decision"] == "blocked" {
                return Err("Node plan preflight found incompatible plan fields".into());
            }
        } else {
            if paths.len() > 1 || complete_sources {
                return Err("--node-config and --source-profile require --plan".into());
            }
            println!("{}", compile_extension_config_json(&path)?);
        }
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
