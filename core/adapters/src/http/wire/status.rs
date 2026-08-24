//! HTTP health DTO and explicit domain conversion.

use crate::http::HttpAdapterError;
use domain::{NODE_CONTRACT_VERSION_V0_1, NodeHealth, NodeId, NodeStatus, TimestampMs};
use serde::{Deserialize, Serialize};

/// Versioned status response from a Local EAIOS bridge.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct WireStatus {
    /// Semantic Node Contract version.
    schema_version: String,
    /// Node that produced the report.
    node_id: String,
    /// Local EAIOS-reported health token.
    health: String,
    /// Source-local observation timestamp, never compared directly with RoboGuide time.
    source_observed_at_ms: u64,
}

impl WireStatus {
    /// Converts a versioned response while enforcing the expected node identity.
    pub(crate) fn into_domain(
        self,
        expected_node: &NodeId,
    ) -> Result<NodeStatus, HttpAdapterError> {
        if self.schema_version != NODE_CONTRACT_VERSION_V0_1 {
            return Err(HttpAdapterError::protocol(format!(
                "unsupported node contract {}",
                self.schema_version
            )));
        }
        if self.node_id != expected_node.as_str() {
            return Err(HttpAdapterError::protocol(format!(
                "status node {} does not match expected {expected_node}",
                self.node_id
            )));
        }
        let health = match self.health.as_str() {
            "online" => NodeHealth::Online,
            "degraded" => NodeHealth::Degraded,
            "offline" => NodeHealth::Offline,
            "safe_stopped" => NodeHealth::SafeStopped,
            value => {
                return Err(HttpAdapterError::protocol(format!(
                    "unsupported node health {value}"
                )));
            }
        };
        Ok(NodeStatus::new(
            health,
            TimestampMs::new(self.source_observed_at_ms),
        ))
    }
}
