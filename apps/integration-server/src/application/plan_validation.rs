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
        if let Err(message) = deployment.restrictions_for_plan(plan, &group) {
            issues.push(PlanIssue {
                code: "deployment.contract_incompatible",
                path: "/tasks".into(),
                message,
            });
        }
    }
    issues
}

/// Binds a bounded query result to exact bytes and startup-frozen source descriptors.
pub(crate) fn assess(
    controller: &ControllerState,
    plan: &domain::MissionPlan,
    tenant: &str,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
    body: &[u8],
) -> serde_json::Value {
    let mut issues = issues(controller, plan, tenant, deployment, feed);
    let truncated = issues.len() > MAX_PLAN_ISSUES;
    issues.truncate(MAX_PLAN_ISSUES);
    serde_json::json!({
        "schema_version": "roboguide.plan-validation/v0.1",
        "scope": "published-contracts",
        "mission_id": plan.goal().mission_id().as_str(),
        "plan_body_sha256": format!("sha256:{:x}", Sha256::digest(body)),
        "decision": if issues.is_empty() { "not_blocked" } else { "blocked" },
        "verifier_source_digest": feed.map(TaskVerifierFeed::source_digest),
        "deployment_digest": deployment.map(DeploymentFeasibility::digest),
        "world_identity": deployment.and_then(DeploymentFeasibility::planning_identity),
        "execution_lifetime": deployment.and_then(DeploymentFeasibility::published_lifetime),
        "issues": issues,
        "issues_truncated": truncated,
    })
}
