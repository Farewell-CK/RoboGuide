#![deny(missing_docs, clippy::missing_docs_in_private_items)]
#![forbid(unsafe_code)]

//! Actual infrastructure-only replica listener, with no Controller children or embodied Nodes.

use controller_replication::{ReplicaConfig, ReplicaNode, ReplicaTransportLimits};
use serde::Deserialize;
use std::path::PathBuf;
use std::time::Duration;

/// Explicit local checker controls; no production configuration or authority is modified.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeControls {
    /// Actual local capacities used by the maintained public startup API.
    limits: ReplicaTransportLimits,
    /// An explicitly created file requests dropping this process's owned listener.
    stop_listener_path: PathBuf,
}

/// Runs one declared infrastructure member on one actual async worker until its owned process exits.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if !(3..=4).contains(&arguments.len()) {
        return Err(
            "usage: replica_transport_probe <config.json> <SQL> <observation.json> [controls.json]"
                .into(),
        );
    }
    let configuration: ReplicaConfig = serde_json::from_slice(&std::fs::read(&arguments[0])?)?;
    let controls: Option<ProbeControls> = arguments
        .get(3)
        .map(|path| {
            std::fs::read(path)
                .and_then(|bytes| serde_json::from_slice(&bytes).map_err(std::io::Error::other))
        })
        .transpose()?;
    let (node, server) = ReplicaNode::start_with_transport_limits(
        configuration,
        &PathBuf::from(&arguments[1]),
        controls
            .as_ref()
            .map(|controls| controls.limits.clone())
            .unwrap_or_default(),
    )
    .await?;
    let mut server = Some(server);
    let output = PathBuf::from(&arguments[2]);
    loop {
        if controls
            .as_ref()
            .is_some_and(|controls| controls.stop_listener_path.exists())
        {
            drop(server.take());
        }
        let observation = serde_json::json!({
            "pid":std::process::id(), "profile":node.profile_identity(),
            "epoch":node.current_epoch(), "revision":node.accepted_revision()?,
            "nodes":0, "devices":0, "controller_children":0,
            "transport":node.transport_snapshot(), "listener_owned":server.is_some(),
        });
        let temporary = output.with_extension("pending");
        std::fs::write(&temporary, serde_json::to_vec(&observation)?)?;
        std::fs::rename(temporary, &output)?;
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}
