//! Durable HTTP admission identity, beside the existing Orchestration authority.

use crate::*;
use sha2::{Digest, Sha256};

/// Exact request-body identity saved atomically with successful Mission acceptance.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MissionAdmission {
    /// Accepted Mission identity, never inferred from a later same-name lookup.
    mission_id: domain::MissionId,
    /// Group created by the existing Orchestrator transaction.
    group_id: domain::ExecutionGroupId,
    /// Digest of all bytes decoded into the submitted complete plan.
    accepted_request_body_sha256: String,
    /// Controller-local admission time; not compared to the sender clock.
    admitted_at_ms: u64,
}

impl MissionAdmission {
    /// Acknowledges an unchanged accepted plan without new admission, dispatch or clock updates.
    pub(crate) fn replay(
        controller: &ControllerState,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
        tenant: &str,
    ) -> Result<Option<serde_json::Value>, String> {
        let mission_id = plan.goal().mission_id();
        let Some(execution) = controller.orchestrator.execution(mission_id) else {
            return Ok(None);
        };
        if controller.bridge.control().mission_tenant(mission_id) != Some(tenant) {
            return Err("Mission tenant is immutable or unavailable".into());
        }
        if execution.plan() != plan || execution.group_id() != group_id {
            return Err(format!(
                "Mission {mission_id} already exists with a different plan or Group"
            ));
        }
        if let Some(receipt) = controller.mission_admissions.get(mission_id.as_str()) {
            receipt.validate(controller, mission_id.as_str())?;
        }
        // Preserve the POST acceptance shape; GET remains the current lifecycle projection.
        Ok(Some(serde_json::json!({
            "mission_id": mission_id.as_str(),
            "group_id": group_id.as_str(),
            "status": "Running",
        })))
    }

    /// Records the actual HTTP body without reserializing or interpreting Mission semantics.
    pub(crate) fn new(
        mission_id: domain::MissionId,
        group_id: domain::ExecutionGroupId,
        body: &[u8],
        now: domain::TimestampMs,
    ) -> Self {
        Self {
            mission_id,
            group_id,
            accepted_request_body_sha256: format!("sha256:{:x}", Sha256::digest(body)),
            admitted_at_ms: now.as_millis(),
        }
    }

    /// Verifies the stored receipt against current Orchestration identity on restore and reads.
    pub(crate) fn validate(&self, controller: &ControllerState, key: &str) -> Result<(), String> {
        let execution = controller
            .orchestrator
            .execution(&self.mission_id)
            .ok_or_else(|| "admission receipt has no accepted Mission".to_string())?;
        let digest = self.accepted_request_body_sha256.as_bytes();
        if key != self.mission_id.as_str()
            || execution.original_group_id() != &self.group_id
            || digest.len() != 71
            || !digest.starts_with(b"sha256:")
            || !digest[7..]
                .iter()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
        {
            return Err("admission receipt identity or digest is inconsistent".into());
        }
        Ok(())
    }

    /// Projects a versioned read-only receipt; it never authorizes a new POST or physical action.
    pub(crate) fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "schema_version": "roboguide.controller-mission-admission/v0.1",
            "mission_id": self.mission_id.as_str(),
            "group_id": self.group_id.as_str(),
            "accepted_request_body_sha256": self.accepted_request_body_sha256,
            "admitted_at_ms": self.admitted_at_ms,
        })
    }
}
