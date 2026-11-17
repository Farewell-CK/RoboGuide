//! Read-only published-contract checks shared with Mission submission; no resource authority.

use super::{DeploymentFeasibility, TaskVerifierFeed};
use crate::*;
use serde::Serialize;
use sha2::{Digest, Sha256};

/// Bound recoverable diagnostics independently of the accepted plan's slot limits.
pub(crate) const MAX_PLAN_ISSUES: usize = 32;

/// Logical contract feedback, never a Node inventory or a placement decision.
#[derive(Clone, Debug, Serialize)]
pub(crate) struct PlanIssue {
    /// Stable reason suitable for bounded model recovery and offline analysis.
    pub(crate) code: &'static str,
    /// JSON pointer into the actual canonical draft.
    pub(crate) path: String,
    /// Contract explanation; no candidate identities are included.
    pub(crate) message: String,
}

impl PlanIssue {
    /// Binds a concrete published-contract error to its exact logical draft field.
    pub(crate) fn new(
        code: &'static str,
        path: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            code,
            path: path.into(),
            message: message.into(),
        }
    }
}

impl From<String> for PlanIssue {
    /// Preserves an unclassified legacy rejection without guessing a more specific cause.
    fn from(message: String) -> Self {
        Self::new("deployment.contract_incompatible", "/tasks", message)
    }
}

impl From<&str> for PlanIssue {
    /// Keeps existing static contract failures distinct from placement authority.
    fn from(message: &str) -> Self {
        message.to_owned().into()
    }
}

/// Reuses submission's source and deployment validators without mutating application state.
pub(crate) fn issues(
    controller: &ControllerState,
    plan: &domain::MissionPlan,
    tenant: &str,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Vec<PlanIssue> {
    let mut issues = Vec::new();
    if let Err(message) = super::semantic_verifier::validate_plan(controller, plan, tenant, feed) {
        if let Some(feed) = feed {
            issues.extend(feed.plan_issues(plan));
        }
        if issues.is_empty() {
            issues.push(PlanIssue {
                code: "verifier.source_unavailable",
                path: "/tasks".into(),
                message,
            });
        }
    }
    if let Some(deployment) = deployment {
        let group = domain::ExecutionGroupId::new(format!("group-{}", plan.goal().mission_id()))
            .expect("decoded Mission identity creates a valid Group identity");
        issues.extend(deployment.plan_issues(plan, &group));
    }
    issues
}

/// Combines a scoped Controller read with private static checks after releasing its live lock.
pub(crate) fn assess(
    mut issues: Vec<PlanIssue>,
    plan: &domain::MissionPlan,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
    node_preflight: Option<&node_service::NodePlanPreflight>,
    body: &[u8],
) -> serde_json::Value {
    if let Some(preflight) = node_preflight {
        issues.extend(
            preflight
                .issues(plan)
                .into_iter()
                .map(|issue| PlanIssue::new(issue.code, issue.path, issue.message)),
        );
    }
    let truncated = issues.len() > MAX_PLAN_ISSUES;
    issues.truncate(MAX_PLAN_ISSUES);
    let mut result = serde_json::json!({
        "schema_version": "roboguide.plan-validation/v0.2",
        "scope": "published-contracts",
        "mission_id": plan.goal().mission_id().as_str(),
        "plan_body_sha256": format!("sha256:{:x}", Sha256::digest(body)),
        "decision": if issues.is_empty() { "not_blocked" } else { "blocked" },
        "verifier_source_digest": feed.map(TaskVerifierFeed::source_digest),
        "deployment_digest": deployment.map(DeploymentFeasibility::digest),
        "world_identity": deployment.and_then(DeploymentFeasibility::planning_identity),
        "execution_lifetime": deployment.and_then(DeploymentFeasibility::published_lifetime),
        "execution_topology": deployment.and_then(DeploymentFeasibility::published_topology),
        "issues": issues,
        "issues_truncated": truncated,
    });
    if let Some(preflight) = node_preflight {
        result["schema_version"] = "roboguide.plan-validation/v0.3".into();
        result["node_preflight"] = serde_json::json!({
            "scope": "declared-node-configs",
            "source_digest": preflight.source_digest(),
        });
    }
    result
}

/// Loads only explicitly configured local declarations before the HTTP listener accepts requests.
pub(crate) fn configured_node_preflight() -> Result<Option<node_service::NodePlanPreflight>, String>
{
    match std::env::var("ROBOGUIDE_NODE_PREFLIGHT_CONFIGS") {
        Ok(value) => {
            let paths: Vec<PathBuf> = serde_json::from_str(&value)
                .map_err(|_| "ROBOGUIDE_NODE_PREFLIGHT_CONFIGS must be a JSON array of paths")?;
            node_service::NodePlanPreflight::load(&paths).map(Some)
        }
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(_) => Err("ROBOGUIDE_NODE_PREFLIGHT_CONFIGS must be UTF-8".into()),
    }
}
