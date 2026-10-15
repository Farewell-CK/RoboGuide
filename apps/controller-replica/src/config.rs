//! Closed startup configuration and byte-bound application policy identity.

use controller_replication::ReplicaConfig;
use serde::Deserialize;
use sha2::{Digest as _, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};

/// Application wrapper fences standalone/older binaries from a cluster's authority history.
pub(super) const APPLICATION_SCHEMA: &str = state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA;
/// Controller environment allowed by the current static software deployment.
const ALLOWED_ENVIRONMENT: &[&str] = &[
    "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN",
    "ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS",
    "ROBOGUIDE_NODE_LEASE_DURATION_MS",
    "ROBOGUIDE_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS",
    "ROBOGUIDE_FACT_BATCH_LIMIT",
    "ROBOGUIDE_FACT_BATCH_WAIT_MS",
    "ROBOGUIDE_CHECKPOINT_STORAGE",
    "ROBOGUIDE_MAX_ADMITTED_MISSIONS",
    "ROBOGUIDE_MAX_ADMITTED_ROLE_SLOTS",
];

/// Complete deployment configuration; paths are explicit and no production process is discovered.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ProcessConfig {
    /// Closed process configuration schema.
    pub schema: String,
    /// Static trusted-loopback consensus member configuration.
    pub replica: ReplicaConfig,
    /// Exclusive durable directory belonging to this supervisor.
    pub storage_directory: PathBuf,
    /// Exact integration-server binary, identical by bytes on all replicas.
    pub controller_binary: PathBuf,
    /// This member's public Node Protocol route.
    pub grpc_address: SocketAddr,
    /// This member's Controller HTTP route.
    pub http_address: SocketAddr,
    /// This member's independent Artifact API route.
    pub artifact_address: SocketAddr,
    /// Optional startup-fixed logical Actor placement; content is policy-bound.
    #[serde(default)]
    pub actor_placement_path: Option<PathBuf>,
    /// Optional live deployment topology; content is policy-bound, never checkpoint-owned.
    #[serde(default)]
    pub physical_entity_registry_path: Option<PathBuf>,
    /// Explicit policy environment; inherited Controller environment is cleared.
    #[serde(default)]
    pub controller_environment: BTreeMap<String, String>,
    /// Optional one-shot software leader transfer requested by the supervisor after startup.
    #[serde(default)]
    pub leader_transfer: Option<LeaderTransferConfig>,
}

/// Closed one-shot leader handoff command; it never asserts physical continuity.
#[derive(Debug, Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub(super) struct LeaderTransferConfig {
    /// Current pre-enrolled voter selected to receive the election trigger.
    pub target: u64,
    /// Stable operator operation identity for retries and status audit.
    pub transfer_id: String,
}

impl ProcessConfig {
    /// Binds actual binary/configuration bytes; per-member route/storage paths do not change semantics.
    pub fn policy_digest(&self) -> Result<String, String> {
        let file_digest = |path: &Path| -> Result<String, String> {
            let mut file = std::fs::File::open(path).map_err(|error| error.to_string())?;
            let mut digest = Sha256::new();
            let mut buffer = [0_u8; 64 * 1024];
            loop {
                let length = file.read(&mut buffer).map_err(|error| error.to_string())?;
                if length == 0 {
                    break;
                }
                digest.update(&buffer[..length]);
            }
            Ok(format!("{:x}", digest.finalize()))
        };
        let identity = serde_json::json!({
            "schema":APPLICATION_SCHEMA,
            "controller_binary":file_digest(&self.controller_binary)?,
            "actor_placement": self.actor_placement_path.as_deref().map(file_digest).transpose()?,
            "physical_entity_registry":self.physical_entity_registry_path.as_deref().map(file_digest).transpose()?,
            "environment":self.controller_environment,
        });
        let bytes = serde_json::to_vec(&identity).map_err(|error| error.to_string())?;
        Ok(format!("{:x}", Sha256::digest(bytes)))
    }

    /// Rejects broad network/configuration scope before owning files or creating a Controller.
    pub fn validate(&self) -> Result<(), String> {
        self.replica.validate()?;
        if self.schema != "roboguide.controller-replica-process/v0.1"
            || self.replica.application_schema != APPLICATION_SCHEMA
            || !self.controller_binary.is_absolute()
            || !self.storage_directory.is_absolute()
            || self.controller_environment.iter().any(|(key, value)| {
                !ALLOWED_ENVIRONMENT.contains(&key.as_str()) || value.len() > 128
            })
        {
            return Err("unsupported replica process paths/schema/environment".into());
        }
        if let Some(transfer) = &self.leader_transfer
            && (transfer.target == self.replica.replica_id
                || !self.replica.members.contains_key(&transfer.target)
                || transfer.transfer_id.trim().is_empty()
                || transfer.transfer_id.len() > 128)
        {
            return Err("leader transfer target or identity is invalid".into());
        }
        let routes = [self.grpc_address, self.http_address, self.artifact_address];
        let distinct = routes.into_iter().collect::<BTreeSet<_>>();
        if distinct.len() != 3
            || routes.iter().any(|address| {
                !address.ip().is_loopback()
                    || address.port() == 0
                    || self
                        .replica
                        .members
                        .values()
                        .any(|member| member.port() == address.port())
            })
        {
            return Err(
                "Controller routes must be distinct nonzero loopback ports outside replica RPC"
                    .into(),
            );
        }
        if self.replica.application_policy_digest != self.policy_digest()? {
            return Err(
                "actual Controller binary/policy differs from the frozen replica digest".into(),
            );
        }
        Ok(())
    }
}
