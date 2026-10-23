//! Composition selection keeps legacy metadata and explicit owned-content authority mutually exclusive.

use crate::{ControllerContentClient, ControllerContentClientConfig, ReplicaFenceClient};

/// Startup-fixed mutually exclusive application transports, each retaining its original contract.
#[derive(Clone)]
enum SelectedClient {
    /// Existing metadata-only child fence, unchanged for original deployments.
    Legacy(Box<ReplicaFenceClient>),
    /// Explicit original owned-process and complete content/recovery client.
    Content(Box<ControllerContentClient>),
}

/// One composition-owned application/command/watchdog port bound to the actual shared source writer.
#[derive(Clone)]
pub struct ApplicationReplicaClient {
    /// Startup-fixed contract; configuration cannot downgrade an accepted original content process.
    selected: SelectedClient,
}

impl ApplicationReplicaClient {
    /// Parses one closed content config or the original legacy environment, never silently combining them.
    pub fn from_environment(
        source: artifact_store::FileSystemArtifactStore,
    ) -> Result<Option<Self>, String> {
        let content = match std::env::var("ROBOGUIDE_REPLICA_CONTENT_CONFIG") {
            Ok(value) => Some(value),
            Err(std::env::VarError::NotPresent) => None,
            Err(_) => return Err("original content child configuration must be UTF-8".into()),
        };
        if content.is_some()
            && [
                "ROBOGUIDE_REPLICA_ENDPOINT",
                "ROBOGUIDE_REPLICA_PROFILE",
                "ROBOGUIDE_REPLICA_EPOCH",
                "ROBOGUIDE_REPLICA_SHARED_SECRET",
                "ROBOGUIDE_REPLICA_TRUST_DATABASE",
                "ROBOGUIDE_REPLICA_TLS_CA",
                "ROBOGUIDE_REPLICA_TLS_CERT",
                "ROBOGUIDE_REPLICA_TLS_KEY",
                "ROBOGUIDE_REPLICA_TLS_SERVER_NAME",
                "ROBOGUIDE_REPLICA_TLS_PEER_DIGESTS",
            ]
            .iter()
            .any(|name| std::env::var_os(name).is_some())
        {
            return Err(
                "original content config cannot inherit any legacy replica fence fields".into(),
            );
        }
        let legacy = ReplicaFenceClient::from_environment()?;
        let selected = match (content, legacy) {
            (None, None) => return Ok(None),
            (None, Some(client)) => SelectedClient::Legacy(Box::new(client)),
            (Some(_), Some(_)) => {
                return Err(
                    "original content and legacy application fences cannot be combined".into(),
                );
            }
            (Some(raw), None) => {
                if raw.len() > 64 * 1024 {
                    return Err("original child content configuration exceeds its bound".into());
                }
                let config: ControllerContentClientConfig =
                    serde_json::from_str(&raw).map_err(|error| error.to_string())?;
                let client = ControllerContentClient::new(config, source)?;
                client.wait_for_registration()?;
                SelectedClient::Content(Box::new(client))
            }
        };
        Ok(Some(Self { selected }))
    }

    /// Selects only the actual startup-frozen original application durability port.
    fn port(&self) -> &dyn state::ApplicationBatchReplicator {
        match &self.selected {
            SelectedClient::Legacy(client) => client.as_ref(),
            SelectedClient::Content(client) => client.as_ref(),
        }
    }

    /// Preserves full content-ready checking for explicit command-delivery gates.
    pub fn check(&self) -> Result<(), String> {
        match &self.selected {
            SelectedClient::Legacy(client) => client.check(),
            SelectedClient::Content(client) => client.check().map(|_| ()),
        }
    }

    /// Checks actual original process/infrastructure authority without claiming current content ready.
    pub fn check_process(&self) -> Result<(), String> {
        match &self.selected {
            SelectedClient::Legacy(client) => client.check(),
            SelectedClient::Content(client) => client.check_process().map(|_| ()),
        }
    }
}

impl state::ApplicationBatchReplicator for ApplicationReplicaClient {
    /// Retains the selected original issuing epoch without manufacturing another authority.
    fn controller_epoch(&self) -> Option<String> {
        self.port().controller_epoch()
    }
    /// Returns the exact original immutable selected application/network profile.
    fn profile_identity(&self) -> &str {
        self.port().profile_identity()
    }
    /// Requires original infrastructure authority before opening private application mutations.
    fn assert_write_authority(&self) -> Result<(), String> {
        self.port().assert_write_authority()
    }
    /// Delegates legacy confirmation; content profiles explicitly refuse incomplete recovery metadata.
    fn confirm_batch(&self, batch: state::ApplicationCommitBatch) -> Result<(), String> {
        self.port().confirm_batch(batch)
    }
    /// Exposes only the actual startup-selected complete recovery contract.
    fn requires_recovery_projection(&self) -> bool {
        self.port().requires_recovery_projection()
    }
    /// Retains the exact original complete source transaction and its independent recovery authority.
    fn confirm_recovery_batch(
        &self,
        batch: state::ApplicationCommitBatch,
        recovery: state::ReplicaRecoveryProjection,
    ) -> Result<(), String> {
        self.port().confirm_recovery_batch(batch, recovery)
    }
}

impl ports::CommandDeliveryFence for ApplicationReplicaClient {
    /// Dispatch uses the original selected full gate; process liveness cannot substitute for ready bytes.
    fn check_delivery(&self) -> Result<(), String> {
        ApplicationReplicaClient::check(self)
    }
    /// Keeps original epoch propagation into Node-side durable Execute/Cancel fencing.
    fn controller_epoch(&self) -> Option<String> {
        self.port().controller_epoch()
    }
}

#[cfg(test)]
mod tests {
    //! Delegating a port must preserve actual original Node-side epoch fencing without network effects.
    use super::*;
    /// The composition wrapper cannot silently fall back to the trait's standalone epoch default.
    #[test]
    fn selected_legacy_port_keeps_original_application_and_delivery_epoch() {
        let epoch = crate::ControllerEpoch {
            term: 17,
            replica_id: 3,
        };
        let legacy = ReplicaFenceClient::new(
            "127.0.0.1:23917".parse().unwrap(),
            "explicit-port-contract".into(),
            epoch,
        )
        .unwrap();
        let client = ApplicationReplicaClient {
            selected: SelectedClient::Legacy(Box::new(legacy)),
        };
        assert_eq!(
            ports::CommandDeliveryFence::controller_epoch(&client),
            state::ApplicationBatchReplicator::controller_epoch(&client)
        );
        assert_eq!(
            ports::CommandDeliveryFence::controller_epoch(&client),
            Some(serde_json::to_string(&epoch).unwrap())
        );
        assert!(!state::ApplicationBatchReplicator::requires_recovery_projection(&client));
        assert_eq!(
            state::ApplicationBatchReplicator::profile_identity(&client),
            "explicit-port-contract"
        );
    }
}
