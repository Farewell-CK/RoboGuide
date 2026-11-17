//! Read-only plan compatibility against explicitly supplied private Node configurations.

use crate::{CompiledLocalCatalog, CompiledOperation, CompiledResource, NodeServiceConfig};
use domain::{MissionPlan, ResourceRequirement};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Read;
use std::path::PathBuf;

/// Maximum explicitly supplied Node configurations; this is not a discovered inventory.
const MAX_CONFIGS: usize = 64;
/// Maximum source bytes admitted from one private configuration.
const MAX_CONFIG_BYTES: u64 = 1024 * 1024;
/// Maximum diagnostics plus one sentinel for the caller's truncation projection.
const MAX_ISSUES: usize = 33;

/// Logical plan diagnostic with no private Node, owner, resource, route or file identities.
#[derive(Debug, Clone, Serialize)]
pub struct NodePlanIssue {
    /// Stable classification of a statically established incompatibility.
    pub code: &'static str,
    /// JSON Pointer into the exact logical plan.
    pub path: String,
    /// Explanation that never discloses the private deployment inventory.
    pub message: &'static str,
}

/// Startup-frozen declarations used only for offline compatibility, never Control eligibility.
#[derive(Debug)]
pub struct NodePlanPreflight {
    /// Deterministically ordered, fully compiled private catalogs.
    catalogs: Vec<CompiledLocalCatalog>,
    /// Hash of exact authored source bytes qualified by unique configured Node identity.
    digest: String,
}

impl NodePlanPreflight {
    /// Reads bounded exact sources and compiles them without opening journals, sockets or workers.
    pub fn load(paths: &[PathBuf]) -> Result<Self, String> {
        Self::load_with_conformance(paths, false)
    }

    /// Retains every original `--validate` conformance gate when a CLI also supplies a plan.
    pub fn load_conformant(paths: &[PathBuf]) -> Result<Self, String> {
        Self::load_with_conformance(paths, true)
    }

    /// Compiles one exact source snapshot and optionally applies the existing conformance policy.
    fn load_with_conformance(paths: &[PathBuf], conformant: bool) -> Result<Self, String> {
        if paths.is_empty() || paths.len() > MAX_CONFIGS {
            return Err("Node preflight requires 1..64 explicit configuration paths".into());
        }
        let mut sources = BTreeMap::new();
        for path in paths {
            let mut bytes = Vec::new();
            std::fs::File::open(path)
                .and_then(|file| file.take(MAX_CONFIG_BYTES + 1).read_to_end(&mut bytes))
                .map_err(|_| "Node preflight configuration cannot be read")?;
            if bytes.len() as u64 > MAX_CONFIG_BYTES {
                return Err("Node preflight configuration exceeds its byte bound".into());
            }
            let source = std::str::from_utf8(&bytes)
                .map_err(|_| "Node preflight configuration must be UTF-8")?;
            let config: NodeServiceConfig = toml::from_str(source)
                .map_err(|_| "Node preflight configuration cannot be decoded")?;
            let catalog = CompiledLocalCatalog::compile(
                config,
                path.parent().unwrap_or_else(|| std::path::Path::new(".")),
            )
            .map_err(|_| "Node preflight configuration failed production compilation")?;
            if conformant {
                crate::conformance::validate_extension_catalog(&catalog)
                    .map_err(|_| "Node preflight configuration failed extension conformance")?;
            }
            let node_id = catalog.node_id().to_string();
            let digest = format!("sha256:{:x}", Sha256::digest(&bytes));
            if sources.insert(node_id, (digest, catalog)).is_some() {
                return Err("Node preflight configuration identities must be unique".into());
            }
        }
        let identity = sources
            .iter()
            .map(|(node, (digest, _))| (node, digest))
            .collect::<Vec<_>>();
        let digest = format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&identity).expect("source identities serialize"))
        );
        Ok(Self {
            catalogs: sources.into_values().map(|(_, catalog)| catalog).collect(),
            digest,
        })
    }

    /// Returns exact source provenance, without exposing source files or their private contents.
    pub fn source_digest(&self) -> &str {
        &self.digest
    }

    /// Checks each operation, concrete parameter source and resource contract on the same catalog.
    pub fn issues(&self, plan: &MissionPlan) -> Vec<NodePlanIssue> {
        let mut issues = Vec::new();
        for (task_index, task) in plan.task_graph().tasks().iter().enumerate() {
            for (role_index, role) in task.requirement().roles().iter().enumerate() {
                let Some(intent) = task.execution_intent(role.role_id()) else {
                    continue;
                };
                let operation = intent.operation().to_string();
                let candidates = self
                    .catalogs
                    .iter()
                    .filter_map(|catalog| {
                        catalog.operations().get(&operation).map(|op| (catalog, op))
                    })
                    .collect::<Vec<_>>();
                let path = format!("/tasks/{task_index}/roles/{role_index}");
                let parameters = intent
                    .parameters()
                    .iter()
                    .map(|(key, value)| {
                        let value = match value {
                            domain::ExecutionValue::Bool(value) => serde_json::json!(value),
                            domain::ExecutionValue::Integer(value) => serde_json::json!(value),
                            domain::ExecutionValue::Float(value) => serde_json::json!(value),
                            domain::ExecutionValue::String(value) => serde_json::json!(value),
                        };
                        (key.clone(), value)
                    })
                    .collect::<serde_json::Map<_, _>>();
                let invocation = serde_json::json!({
                    "mission_id": plan.goal().mission_id().as_str(),
                    "task_id": task.requirement().task_ref().task_id().as_str(),
                    "group_id": format!("group-{}", plan.goal().mission_id()),
                    "role_id": role.role_id().as_str(),
                    "operation": operation,
                    "objective": intent.objective(),
                    "parameters": parameters,
                    // Only array shape is known; no Control-selected resource ID is invented.
                    "resource_ids": vec![Value::Null; role.resource_requirements().len()],
                    "resource_grants": vec![Value::Null; role.resource_requirements().iter()
                        .filter(|demand| demand.allocation_mode() == domain::ResourceAllocationMode::Divisible).count()],
                });
                let parameters = candidates
                    .iter()
                    .filter(|(catalog, op)| operation_sources_exist(catalog, op, &invocation))
                    .collect::<Vec<_>>();
                let issue = if candidates.is_empty() {
                    Some(NodePlanIssue {
                        code: "node_preflight.operation_unsupported",
                        path: format!("{path}/execution_intent/operation"),
                        message: "No supplied Node configuration implements this exact operation.",
                    })
                } else if parameters.is_empty() {
                    Some(NodePlanIssue {
                        code: "node_preflight.parameters_incompatible",
                        path: format!("{path}/execution_intent/parameters"),
                        message: "Invocation fields, pure conversions or Artifact directives are incompatible with every supplied operation workflow.",
                    })
                } else if !parameters.iter().any(|(catalog, op)| {
                    resources_compatible(catalog, op, &role.resource_requirements())
                }) {
                    Some(NodePlanIssue {
                        code: "node_preflight.resources_incompatible",
                        path: format!("{path}/requirements/resources"),
                        message: "Resource demands cannot cover the same operation's required private resources, kinds, capacities, allocation modes and unit classes.",
                    })
                } else {
                    None
                };
                if let Some(issue) = issue {
                    issues.push(issue);
                    if issues.len() == MAX_ISSUES {
                        return issues;
                    }
                }
            }
        }
        issues
    }

    /// Produces a standalone offline report bound to the original plan bytes and frozen sources.
    pub fn assess(&self, plan: &MissionPlan, body: &[u8]) -> Value {
        let mut issues = self.issues(plan);
        let truncated = issues.len() > 32;
        issues.truncate(32);
        serde_json::json!({
            "schema_version": "roboguide.node-plan-preflight/v0.1",
            "scope": "declared-node-configs",
            "source_digest": self.source_digest(),
            "plan_body_sha256": format!("sha256:{:x}", Sha256::digest(body)),
            "decision": if issues.is_empty() { "not_blocked" } else { "blocked" },
            "issues": issues,
            "issues_truncated": truncated,
            "offline_compile": true,
            "runtime_probes_executed": false,
            "hardware_probes_executed": false,
        })
    }
}

/// Checks all phases without rendering requests or guessing local-response contents.
fn operation_sources_exist(
    catalog: &CompiledLocalCatalog,
    operation: &CompiledOperation,
    invocation: &Value,
) -> bool {
    // The original runtime digest constructor performs pure action/slot/map/revision checks.
    if crate::engine::workflow_digest(catalog, operation, invocation).is_err() {
        return false;
    }
    if let Some(slot) = invocation
        .pointer("/parameters/artifact_slot")
        .and_then(Value::as_str)
    {
        if let Some(provider) = catalog
            .artifact_service()
            .and_then(|service| service.output_bindings().get(slot))
            .and_then(|binding| binding.producer_provider_id.as_ref())
            && catalog
                .memory_providers()
                .get(provider)
                .is_none_or(|provider| provider.owner() != operation.owner())
        {
            return false;
        }
        for pointer in operation
            .workflow()
            .execute()
            .iter()
            .flat_map(crate::CompiledWorkflowStep::source_pointers)
        {
            if let Some(id) = pointer
                .strip_prefix("/artifacts/inputs/")
                .or_else(|| pointer.strip_prefix("/artifacts/outputs/"))
                && id.replace("~1", "/").replace("~0", "~") != slot
            {
                return false;
            }
        }
    }
    let mut invocation = invocation.clone();
    if catalog.node_contract_version() == integration::grpc::v0_4::LEGACY_NODE_CONTRACT_VERSION {
        invocation["capability_contract"] = invocation["operation"].clone();
        invocation
            .as_object_mut()
            .expect("invocation is an object")
            .remove("operation");
        invocation
            .as_object_mut()
            .expect("invocation is an object")
            .remove("objective");
    }
    let workflow = operation.workflow();
    workflow
        .execute()
        .iter()
        .chain(workflow.status())
        .chain(workflow.cancel())
        .all(|step| step.invocation_sources_exist(&invocation))
}

/// Requires an injective assignment covering every demand and every mandatory private ID.
fn resources_compatible(
    catalog: &CompiledLocalCatalog,
    operation: &CompiledOperation,
    demands: &[ResourceRequirement],
) -> bool {
    let mut resources = catalog.resources().values().collect::<Vec<_>>();
    resources.sort_by_key(|resource| !operation.required_resources().contains(resource.id()));
    let mut assignments = vec![None; demands.len()];
    for (index, resource) in resources.iter().enumerate() {
        let mut seen = vec![false; demands.len()];
        let matched = assign_resource(index, &resources, demands, &mut assignments, &mut seen);
        if !matched && operation.required_resources().contains(resource.id()) {
            return false;
        }
    }
    assignments.iter().all(Option::is_some)
}

/// Augments one resource-to-demand matching while preserving every previously covered resource.
fn assign_resource(
    index: usize,
    resources: &[&CompiledResource],
    demands: &[ResourceRequirement],
    assignments: &mut [Option<usize>],
    seen: &mut [bool],
) -> bool {
    for (demand_index, demand) in demands.iter().enumerate() {
        if seen[demand_index] || !resource_matches(resources[index], demand) {
            continue;
        }
        seen[demand_index] = true;
        if assignments[demand_index]
            .is_none_or(|previous| assign_resource(previous, resources, demands, assignments, seen))
        {
            assignments[demand_index] = Some(index);
            return true;
        }
    }
    false
}

/// Mirrors declared Control resource compatibility without selecting or committing an actual ID.
fn resource_matches(resource: &CompiledResource, demand: &ResourceRequirement) -> bool {
    let kind = match demand.kind() {
        domain::ResourceKind::Compute => "compute",
        domain::ResourceKind::Space => "space",
        domain::ResourceKind::Time => "time",
    };
    resource.kind() == kind
        && resource.capacity() >= demand.units()
        && resource.allocation_mode() == demand.allocation_mode()
        && resource.resource_class() == demand.resource_class()
}

#[cfg(test)]
#[path = "preflight_tests.rs"]
mod tests;
