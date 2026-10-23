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
    "TOKIO_WORKER_THREADS",
    "ROBOGUIDE_OPERATOR_CONNECTION_LIMIT",
    "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA",
    "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN",
    "ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS",
    "ROBOGUIDE_NODE_LEASE_DURATION_MS",
    "ROBOGUIDE_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS",
    "ROBOGUIDE_FACT_BATCH_LIMIT",
    "ROBOGUIDE_FACT_BATCH_WAIT_MS",
    "ROBOGUIDE_CHECKPOINT_STORAGE",
    "ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA",
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
    /// Optional private loopback metrics route; observations do not alter policy identity.
    #[serde(default)]
    pub metrics_address: Option<SocketAddr>,
    /// Local bounded RPC capacities; observation/resource settings never change policy identity.
    #[serde(default)]
    pub transport_limits: controller_replication::ReplicaTransportLimits,
    /// Explicit original child/content contract; unavailable to unchanged process v0.1.
    #[serde(default)]
    pub content: Option<ContentProcessConfig>,
}

/// Explicit original byte root and owned-child transport, independent of Nodes or device inventory.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ContentProcessConfig {
    /// Closed process/content interpretation.
    pub schema: String,
    /// Actual existing original member Root4; startup never silently creates or upgrades it.
    pub root: PathBuf,
    /// Complete initial actual root owners; committed member data stays authoritative after bootstrap.
    pub bootstrap_owners: BTreeMap<u64, String>,
    /// Actual same-host private original-child endpoint.
    pub socket: PathBuf,
    /// Explicit complete-operation budget, fixed in original child configuration and every first intent.
    pub child_budget_ms: u64,
    /// Original declared failure-detection observation window, never renewed within a check.
    pub quorum_observation_ms: u64,
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
        let mut identity = serde_json::json!({
            "schema":APPLICATION_SCHEMA,
            "controller_binary":file_digest(&self.controller_binary)?,
            "actor_placement": self.actor_placement_path.as_deref().map(file_digest).transpose()?,
            "physical_entity_registry":self.physical_entity_registry_path.as_deref().map(file_digest).transpose()?,
            "environment":self.controller_environment,
        });
        if let Some(content) = &self.content {
            identity
                .as_object_mut()
                .ok_or("original policy object unavailable")?
                .insert(
                    "owned_content".into(),
                    serde_json::json!({
                        "schema":content.schema,"parent":"roboguide.replica-storage/v0.8",
                        "child_budget_ms":content.child_budget_ms,
                        "quorum_observation_ms":content.quorum_observation_ms,
                    }),
                );
        }
        let bytes = serde_json::to_vec(&identity).map_err(|error| error.to_string())?;
        Ok(format!("{:x}", Sha256::digest(bytes)))
    }

    /// Rejects broad network/configuration scope before owning files or creating a Controller.
    pub fn validate(&self) -> Result<(), String> {
        self.replica.validate()?;
        self.transport_limits.validate()?;
        if self
            .controller_environment
            .get("ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN")
            .is_some_and(|value| !matches!(value.as_str(), "0" | "1"))
        {
            return Err(
                "local recovery administrator flag must be 0 or 1 before child spawn".into(),
            );
        }
        let process_schema = match &self.content {
            None => {
                if self.replica.streams_content() {
                    return Err(
                        "network content requires explicit process v0.2 content configuration"
                            .into(),
                    );
                }
                "roboguide.controller-replica-process/v0.1"
            }
            Some(content) => {
                if !self.replica.streams_content()
                    || content.schema != "roboguide.controller-content-process/v0.1"
                    || !content.root.is_absolute()
                    || !content.socket.is_absolute()
                    || content.socket.as_os_str().len() > 103
                    || !(100..=300000).contains(&content.child_budget_ms)
                    || !(100..=30000).contains(&content.quorum_observation_ms)
                    || content.quorum_observation_ms > content.child_budget_ms
                    || content
                        .bootstrap_owners
                        .keys()
                        .copied()
                        .collect::<BTreeSet<_>>()
                        != self.replica.members.keys().copied().collect()
                    || content
                        .bootstrap_owners
                        .values()
                        .collect::<BTreeSet<_>>()
                        .len()
                        != content.bootstrap_owners.len()
                    || self
                        .controller_environment
                        .get("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA")
                        .map(String::as_str)
                        != Some("1")
                    || self
                        .controller_environment
                        .get("ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA")
                        .map(String::as_str)
                        != Some("roboguide.artifact-storage/v3")
                {
                    return Err("original content process requires explicit Root4/parent8/SQL1/Artifact3 and bounded child transport".into());
                }
                for owner in content.bootstrap_owners.values() {
                    controller_replication::ReplicaMember::with_content_store(
                        "127.0.0.1:1",
                        owner.clone(),
                    )?;
                }
                "roboguide.controller-replica-process/v0.2"
            }
        };
        if self
            .controller_environment
            .get("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA")
            .is_some_and(|value| !matches!(value.as_str(), "legacy" | "1"))
        {
            return Err("Controller storage format must be explicitly legacy or 1".into());
        }
        if self
            .controller_environment
            .get("ROBOGUIDE_OPERATOR_CONNECTION_LIMIT")
            .is_some_and(|value| {
                value
                    .parse::<usize>()
                    .map_or(true, |limit| !(1..=1024).contains(&limit))
            })
        {
            return Err("Operator connection limit must be an integer in 1..1024".into());
        }
        if self
            .controller_environment
            .get("TOKIO_WORKER_THREADS")
            .is_some_and(|value| {
                value
                    .parse::<usize>()
                    .map_or(true, |threads| !(1..=64).contains(&threads))
            })
        {
            return Err(
                "Controller async worker count must be an explicit integer in 1..64".into(),
            );
        }
        if self.schema != process_schema
            || self.replica.application_schema != APPLICATION_SCHEMA
            || !self.controller_binary.is_absolute()
            || !self.storage_directory.is_absolute()
            || self.controller_environment.iter().any(|(key, value)| {
                !ALLOWED_ENVIRONMENT.contains(&key.as_str()) || value.len() > 128
            })
        {
            return Err("unsupported replica process paths/schema/environment".into());
        }
        if self
            .controller_environment
            .get("ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA")
            .is_some_and(|value| {
                !matches!(
                    value.as_str(),
                    "legacy"
                        | "roboguide.artifact-storage/v1"
                        | "roboguide.artifact-storage/v2"
                        | "roboguide.artifact-storage/v3"
                )
            })
        {
            return Err("unsupported explicit Controller Artifact storage schema".into());
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
        if self.metrics_address.is_some_and(|metrics| {
            !metrics.ip().is_loopback()
                || metrics.port() == 0
                || routes.contains(&metrics)
                || self
                    .replica
                    .members
                    .values()
                    .any(|member| member.port() == metrics.port())
        }) {
            return Err("replica metrics requires a distinct nonzero private loopback port".into());
        }
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

#[cfg(test)]
mod tests {
    //! Closed resource configuration must remain policy-bound and reject unbounded thread counts.

    use super::*;

    /// Declares configuration-only identities; no TLS handshake, Root, Node or child is created here.
    fn owned_profile() -> ProcessConfig {
        let mut configuration = profile("1");
        configuration.schema = "roboguide.controller-replica-process/v0.2".into();
        configuration.replica.schema = "roboguide.controller-replica/v0.4".into();
        configuration.replica.initial_voters = Some([1, 2, 3].into());
        configuration.replica.tls = Some(controller_replication::ReplicaTlsConfig {
            ca_cert_path: "/declared/ca.pem".into(),
            cert_path: "/declared/member.pem".into(),
            key_path: "/declared/member.pem".into(),
            server_name: "localhost".into(),
            peer_certificate_digests: (1..=3)
                .map(|id| (id, format!("sha256:{id:064x}")))
                .collect(),
        });
        configuration.content = Some(ContentProcessConfig {
            schema: "roboguide.controller-content-process/v0.1".into(),
            root: "/declared/root".into(),
            bootstrap_owners: (1..=3)
                .map(|id| (id, format!("replica-content:{id:064x}")))
                .collect(),
            socket: "/declared/child.sock".into(),
            child_budget_ms: 45000,
            quorum_observation_ms: 5000,
        });
        configuration.controller_environment.extend([
            ("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA".into(), "1".into()),
            (
                "ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA".into(),
                "roboguide.artifact-storage/v3".into(),
            ),
        ]);
        configuration.replica.application_policy_digest = configuration.policy_digest().unwrap();
        configuration
    }

    /// Explicit content semantics and observation budgets cannot be hidden in an older or frozen policy.
    #[test]
    fn owned_content_is_explicit_and_original_observation_budget_is_policy_bound() {
        let mut configuration = owned_profile();
        configuration.validate().unwrap();
        let original = configuration.policy_digest().unwrap();
        configuration.content.as_mut().unwrap().root = "/another/member/root".into();
        configuration.content.as_mut().unwrap().socket = "/another/child.sock".into();
        assert_eq!(configuration.policy_digest().unwrap(), original);
        configuration
            .content
            .as_mut()
            .unwrap()
            .quorum_observation_ms = 6000;
        assert!(configuration.validate().is_err());
        configuration.replica.application_policy_digest = configuration.policy_digest().unwrap();
        configuration.validate().unwrap();
        configuration.schema = "roboguide.controller-replica-process/v0.1".into();
        assert!(configuration.validate().is_err());
        configuration = owned_profile();
        configuration.content = None;
        assert!(configuration.validate().is_err());
        for (child, window) in [(45000, 99), (45000, 30001), (100, 101), (99, 100)] {
            let mut configuration = owned_profile();
            let content = configuration.content.as_mut().unwrap();
            content.child_budget_ms = child;
            content.quorum_observation_ms = window;
            configuration.replica.application_policy_digest =
                configuration.policy_digest().unwrap();
            assert!(
                configuration.validate().is_err(),
                "{child}/{window} bypassed bounds"
            );
        }
    }

    /// The actual child accepts only 0/1; invalid declarations refuse before repeated child spawns.
    #[test]
    fn recovery_administrator_flag_is_validated_before_spawn() {
        for value in ["0", "1", "true", "false", "", "2"] {
            let mut configuration = profile("1");
            configuration.controller_environment.insert(
                "ROBOGUIDE_OPERATOR_LOCAL_RECOVERY_ADMIN".into(),
                value.into(),
            );
            configuration.replica.application_policy_digest =
                configuration.policy_digest().unwrap();
            assert_eq!(configuration.validate().is_ok(), matches!(value, "0" | "1"));
        }
    }

    /// Local listener capacity remains bounded without changing original homogeneous cluster identity.
    #[test]
    fn replica_transport_limits_are_local_bounded_and_preserve_existing_policy_identity() {
        let mut configuration = profile("1");
        let policy = configuration.policy_digest().unwrap();
        let cluster = configuration.replica.profile_identity();
        configuration.transport_limits.inbound_connections = 9;
        configuration.transport_limits.large_frame_bytes = 1024 * 1024;
        configuration.validate().unwrap();
        assert_eq!(configuration.policy_digest().unwrap(), policy);
        assert_eq!(configuration.replica.profile_identity(), cluster);
        configuration.transport_limits.outbound_connections = 0;
        assert!(configuration.validate().is_err());
    }

    /// Uses an actual compiled file for policy hashing, without starting any process or Node.
    fn profile(workers: &str) -> ProcessConfig {
        let directory = tempfile::tempdir().unwrap();
        let mut profile: ProcessConfig = serde_json::from_value(serde_json::json!({
            "schema":"roboguide.controller-replica-process/v0.1",
            "replica":{"schema":"roboguide.controller-replica/v0.1","cluster_id":"worker-config-unit",
                "replica_id":1,"members":{"1":"127.0.0.1:10001","2":"127.0.0.1:10002","3":"127.0.0.1:10003"},
                "application_schema":APPLICATION_SCHEMA,"application_policy_digest":"pending"},
            "storage_directory":directory.path(),"controller_binary":std::env::current_exe().unwrap(),
            "grpc_address":"127.0.0.1:11001","http_address":"127.0.0.1:11002","artifact_address":"127.0.0.1:11003",
            "controller_environment":{"TOKIO_WORKER_THREADS":workers}
        })).unwrap();
        profile.replica.application_policy_digest = profile.policy_digest().unwrap();
        profile
    }

    /// Actual thread-resource configuration is bounded and changes the already-frozen policy identity.
    #[test]
    fn async_workers_are_explicit_bounded_and_application_policy_bound() {
        for value in ["0", "65", "-1", "invalid", "1.5"] {
            assert!(profile(value).validate().is_err());
        }
        let original = profile("1");
        original.validate().unwrap();
        let mut changed = original;
        changed
            .controller_environment
            .insert("TOKIO_WORKER_THREADS".into(), "2".into());
        assert!(
            changed.validate().is_err(),
            "changed worker resources bypassed the original policy digest"
        );
        changed.replica.application_policy_digest = changed.policy_digest().unwrap();
        changed.validate().unwrap();
        profile("64").validate().unwrap();
    }

    /// Transport capacity is deployment-owned, bounded and part of the original application policy.
    #[test]
    fn operator_connections_are_bounded_and_policy_bound() {
        for value in ["0", "1025", "-1", "invalid", "1.5", "2"] {
            let mut candidate = profile("1");
            candidate
                .controller_environment
                .insert("ROBOGUIDE_OPERATOR_CONNECTION_LIMIT".into(), value.into());
            assert!(candidate.validate().is_err(), "{value} bypassed policy");
            candidate.replica.application_policy_digest = candidate.policy_digest().unwrap();
            assert_eq!(candidate.validate().is_ok(), value == "2");
        }
    }

    /// A retained byte-store format is explicit, closed and part of the frozen deployment policy.
    #[test]
    fn artifact_storage_format_is_explicit_and_policy_bound() {
        for value in ["legacy", "roboguide.artifact-storage/v1", "unknown"] {
            let mut candidate = profile("1");
            candidate
                .controller_environment
                .insert("ROBOGUIDE_ARTIFACT_STORAGE_SCHEMA".into(), value.into());
            assert!(candidate.validate().is_err());
            candidate.replica.application_policy_digest = candidate.policy_digest().unwrap();
            assert_eq!(candidate.validate().is_ok(), value != "unknown");
        }
    }

    /// Storage generation is closed and cannot change beneath the original child policy digest.
    #[test]
    fn controller_storage_format_is_closed_and_policy_bound() {
        for value in ["legacy", "1", "0", "2", "unknown", ""] {
            let mut candidate = profile("1");
            candidate
                .controller_environment
                .insert("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA".into(), value.into());
            assert!(candidate.validate().is_err());
            candidate.replica.application_policy_digest = candidate.policy_digest().unwrap();
            assert_eq!(
                candidate.validate().is_ok(),
                matches!(value, "legacy" | "1")
            );
        }
    }
}
