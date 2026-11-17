//! Read-only plan compatibility against explicitly supplied private Node configurations.

use crate::{CompiledLocalCatalog, CompiledOperation, CompiledResource, NodeServiceConfig};
use domain::{MissionPlan, ResourceRequirement};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Maximum explicitly supplied Node configurations; this is not a discovered inventory.
const MAX_CONFIGS: usize = 64;
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
    /// Explicit source coverage; legacy TOML-only receipts never acquire broader meaning.
    complete_sources: bool,
}

impl NodePlanPreflight {
    /// Reads bounded exact sources and compiles them without opening journals, sockets or workers.
    pub fn load(paths: &[PathBuf]) -> Result<Self, String> {
        Self::load_with_conformance(paths, false, false)
    }

    /// Retains every original `--validate` conformance gate when a CLI also supplies a plan.
    pub fn load_conformant(paths: &[PathBuf]) -> Result<Self, String> {
        Self::load_with_conformance(paths, true, false)
    }

    /// Explicit complete-source profile binding raw TOML, descriptor snapshots and resolution base.
    pub fn load_complete(paths: &[PathBuf], conformant: bool) -> Result<Self, String> {
        Self::load_with_conformance(paths, conformant, true)
    }

    /// Compiles one exact source snapshot and optionally applies the existing conformance policy.
    fn load_with_conformance(
        paths: &[PathBuf],
        conformant: bool,
        complete_sources: bool,
    ) -> Result<Self, String> {
        if paths.is_empty() || paths.len() > MAX_CONFIGS {
            return Err("Node preflight requires 1..64 explicit configuration paths".into());
        }
        let mut sources = BTreeMap::new();
        let mut complete_identity = BTreeMap::new();
        let mut descriptors = BTreeMap::new();
        let mut budget = crate::source_io::SourceBudget::new();
        for path in paths {
            let bytes = budget
                .read(path, crate::MAX_CONFIG_SOURCE_BYTES)
                .map_err(|_| "Node preflight configuration cannot be read within source bounds")?;
            let source = std::str::from_utf8(&bytes)
                .map_err(|_| "Node preflight configuration must be UTF-8")?;
            let config: NodeServiceConfig = toml::from_str(source)
                .map_err(|_| "Node preflight configuration cannot be decoded")?;
            let directory = path
                .parent()
                .filter(|path| !path.as_os_str().is_empty())
                .unwrap_or_else(|| std::path::Path::new("."));
            let directory = if complete_sources {
                directory
                    .canonicalize()
                    .map_err(|_| "Node preflight resolution base cannot be resolved")?
            } else {
                directory.to_path_buf()
            };
            if complete_sources && directory.to_str().is_none() {
                return Err("Node preflight resolution base must be UTF-8".into());
            }
            // A shared exact-path snapshot prevents catalogs from compiling different bytes.
            let catalog = CompiledLocalCatalog::compile_sources(
                config,
                &directory,
                &mut budget,
                &mut descriptors,
            )
            .map_err(|_| "Node preflight configuration failed production compilation")?;
            if conformant {
                crate::conformance::validate_extension_catalog(&catalog)
                    .map_err(|_| "Node preflight configuration failed extension conformance")?;
            }
            let node_id = catalog.node_id().to_string();
            let digest = format!("sha256:{:x}", Sha256::digest(&bytes));
            if complete_sources {
                let descriptor_identity = catalog
                    .connections()
                    .values()
                    .filter_map(|connection| {
                        if let crate::CompiledConnection::Grpc {
                            id,
                            descriptor_set: Some(path),
                            ..
                        } = connection
                        {
                            let bytes = &descriptors[path];
                            Some((
                                id,
                                path,
                                bytes.len(),
                                format!("sha256:{:x}", Sha256::digest(bytes)),
                            ))
                        } else {
                            None
                        }
                    })
                    .collect::<Vec<_>>();
                complete_identity.insert(
                    node_id.clone(),
                    serde_json::json!({
                        "toml": digest, "resolution_base": directory,
                        "descriptors": descriptor_identity,
                    }),
                );
            }
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
            Sha256::digest(if complete_sources {
                serde_json::to_vec(&("complete-sources/v0.1", &complete_identity))
                    .expect("complete identities serialize")
            } else {
                serde_json::to_vec(&identity).expect("source identities serialize")
            })
        );
        budget
            .check()
            .map_err(|_| "Node preflight compilation budget expired")?;
        Ok(Self {
            complete_sources,
            catalogs: sources.into_values().map(|(_, catalog)| catalog).collect(),
            digest,
        })
    }

    /// Returns exact source provenance, without exposing source files or their private contents.
    pub fn source_digest(&self) -> &str {
        &self.digest
    }

    /// Returns the explicitly negotiated source scope, never a live Node attestation.
    pub fn source_scope(&self) -> &'static str {
        if self.complete_sources {
            "declared-node-complete-sources"
        } else {
            "declared-node-configs"
        }
    }

    /// Identifies the explicit complete snapshot profile without changing legacy receipt semantics.
    pub fn complete_sources(&self) -> bool {
        self.complete_sources
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
                let mut has_operation = false;
                let mut has_parameters = false;
                let compatible = self.catalogs.iter().any(|catalog| {
                    let Some(op) = catalog.operations().get(&operation) else {
                        return false;
                    };
                    has_operation = true;
                    if !operation_sources_exist(catalog, op, &invocation) {
                        return false;
                    }
                    has_parameters = true;
                    resources_compatible(catalog, op, &role.resource_requirements())
                });
                let issue = if !has_operation {
                    Some(NodePlanIssue {
                        code: "node_preflight.operation_unsupported",
                        path: format!("{path}/execution_intent/operation"),
                        message: "No supplied Node configuration implements this exact operation.",
                    })
                } else if !has_parameters {
                    Some(NodePlanIssue {
                        code: "node_preflight.parameters_incompatible",
                        path: format!("{path}/execution_intent/parameters"),
                        message: "Invocation fields, pure conversions or Artifact directives are incompatible with every supplied operation workflow.",
                    })
                } else if !compatible {
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
            "schema_version": if self.complete_sources { "roboguide.node-plan-preflight/v0.2" } else { "roboguide.node-plan-preflight/v0.1" },
            "scope": self.source_scope(),
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
    if crate::engine::validate_artifact_invocation(catalog, operation, invocation).is_err() {
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
