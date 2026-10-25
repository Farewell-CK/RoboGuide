//! Original quantitative Node admission and restart accounting beside Local How locks.

use super::*;
use domain::{ResourceAllocationGrant, ResourceAllocationMode};

/// Reads bounded exact original grants and checks them against current configured resources and owner.
pub(super) fn invocation_grants(
    catalog: &CompiledLocalCatalog,
    invocation: &serde_json::Value,
) -> Result<Vec<ResourceAllocationGrant>, EngineError> {
    let grants: Vec<ResourceAllocationGrant> = invocation
        .get("resource_grants")
        .map(|value| serde_json::from_value(value.clone()))
        .transpose()
        .map_err(EngineError::Json)?
        .unwrap_or_default();
    if grants.len() > 64
        || !grants.is_empty()
            && catalog.node_contract_version()
                != integration::grpc::v0_4::CAPACITY_NODE_CONTRACT_VERSION
    {
        return Err(EngineError::Protocol(
            "quantitative grants exceed the negotiated Node contract".into(),
        ));
    }
    let resources: Vec<String> =
        serde_json::from_value(invocation.get("resource_ids").cloned().unwrap_or_default())
            .map_err(EngineError::Json)?;
    let mut granted = BTreeSet::new();
    for grant in &grants {
        grant.validate().map_err(EngineError::Protocol)?;
        let resource = catalog
            .resources()
            .get(grant.resource_id.as_str())
            .ok_or_else(|| {
                EngineError::Configuration(
                    "original allocated resource is absent from current configuration".into(),
                )
            })?;
        if resource.allocation_mode() != ResourceAllocationMode::Divisible
            || resource.resource_class() != Some(grant.resource_class.as_str())
            || resource.capacity() < grant.allocation_units
            || grant.node_id.as_str() != catalog.node_id()
            || invocation["mission_id"].as_str() != Some(grant.task_ref.mission_id().as_str())
            || invocation["task_id"].as_str() != Some(grant.task_ref.task_id().as_str())
            || invocation["group_id"].as_str() != Some(grant.group_id.as_str())
            || invocation["role_id"].as_str() != Some(grant.role_id.as_str())
            || !resources.iter().any(|id| id == grant.resource_id.as_str())
            || !granted.insert(grant.resource_id.as_str())
        {
            return Err(EngineError::Protocol("original resource grant changed Node, Task, Role, Group, class, selection or capacity".into()));
        }
        if invocation
            .get("memory_preparation")
            .and_then(|value| value.get("tenant_id"))
            .and_then(serde_json::Value::as_str)
            .is_some_and(|tenant| tenant != grant.tenant_id)
        {
            return Err(EngineError::Protocol(
                "resource grant changed original Memory tenant".into(),
            ));
        }
    }
    for id in resources {
        let resource = catalog.resources().get(&id).ok_or_else(|| {
            EngineError::Configuration("original selected resource is absent".into())
        })?;
        if resource.allocation_mode() == ResourceAllocationMode::Divisible
            && !granted.contains(id.as_str())
        {
            return Err(EngineError::Protocol(
                "shared resource has no exact original quantitative grant".into(),
            ));
        }
    }
    Ok(grants)
}

impl LocalIntegrationEngine {
    /// Rechecks the whole original live/uncertain journal; timers and restart never release units.
    pub(super) fn validate_capacity_admission(
        &self,
        candidate: Option<(&str, &serde_json::Value)>,
    ) -> Result<(), EngineError> {
        let mut grants = Vec::new();
        let mut live_identities = BTreeSet::new();
        for record in self.inner.journal.capacity_records()? {
            if candidate.is_some_and(|(id, _)| id == record.execution_id()) {
                continue;
            }
            let invocation = decode_invocation_json(record.spec().invocation_content())?;
            let original = invocation_grants(self.catalog(), &invocation)?;
            for grant in original {
                if !live_identities.insert((
                    grant.allocation_id.clone(),
                    grant.task_ref.clone(),
                    grant.role_id.clone(),
                )) {
                    return Err(EngineError::Protocol("another live or Unknown attempt already uses this original resource borrower".into()));
                }
                grants.push(grant);
            }
        }
        if let Some((_, invocation)) = candidate {
            for grant in invocation_grants(self.catalog(), invocation)? {
                if !live_identities.insert((
                    grant.allocation_id.clone(),
                    grant.task_ref.clone(),
                    grant.role_id.clone(),
                )) {
                    return Err(EngineError::Protocol("another live or Unknown attempt already uses this original resource borrower".into()));
                }
                grants.push(grant);
            }
        }
        let mut parents = BTreeMap::<String, (ResourceAllocationGrant, u64)>::new();
        for grant in grants {
            let (original, used) = parents
                .entry(grant.allocation_id.clone())
                .or_insert((grant.clone(), 0));
            if original.original_allocation_id() != grant.original_allocation_id() {
                return Err(EngineError::Protocol(
                    "resource allocation identity changed its original parent".into(),
                ));
            }
            *used += u64::from(grant.units);
            if *used > u64::from(original.allocation_units) {
                return Err(EngineError::Protocol(
                    "live resource borrowers exceed their original Context or Task allocation"
                        .into(),
                ));
            }
        }
        let mut pools = BTreeMap::<domain::ResourceId, u64>::new();
        for (grant, _) in parents.values() {
            let used = pools.entry(grant.resource_id.clone()).or_default();
            *used += u64::from(grant.allocation_units);
            let resource = self
                .catalog()
                .resources()
                .get(grant.resource_id.as_str())
                .expect("original declaration checked above");
            if *used > u64::from(resource.capacity()) {
                return Err(EngineError::Protocol(
                    "live original resource allocations exceed configured Node capacity".into(),
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Compiles arbitrary software declarations into an isolated test journal with no network calls.
    fn engine(directory: &std::path::Path) -> LocalIntegrationEngine {
        let mut config: crate::NodeServiceConfig =
            toml::from_str(include_str!("../../../../config/node.toml")).unwrap();
        config.schema = crate::CONFIG_SCHEMA_V0_14.into();
        config.state_directory = directory.to_path_buf();
        config.memory_providers.clear();
        config.artifacts = None;
        let resource = config
            .resources
            .iter_mut()
            .find(|resource| resource.kind == "compute")
            .unwrap();
        resource.capacity = 4;
        resource.allocation_mode = Some(ResourceAllocationMode::Divisible);
        resource.resource_class = Some("unit-test.compute/v1".into());
        let catalog =
            CompiledLocalCatalog::compile(config, std::path::Path::new("../../config")).unwrap();
        LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(crate::HttpDriver::new().unwrap()) as Arc<dyn LocalDriver>],
        )
        .unwrap()
    }

    /// Builds an exact synthetic original grant; its hash excludes mutable Context borrower identity.
    fn grant(
        engine: &LocalIntegrationEngine,
        task: &str,
        units: u32,
        generation: u64,
    ) -> ResourceAllocationGrant {
        let task_ref = TaskRef::new(
            MissionId::new("unit-mission").unwrap(),
            TaskId::new(task).unwrap(),
        );
        let mut grant = ResourceAllocationGrant {
            schema: "roboguide.resource-allocation-grant/v0.1".into(),
            allocation_id: String::new(),
            generation,
            resource_id: domain::ResourceId::new("compute-slot").unwrap(),
            resource_class: "unit-test.compute/v1".into(),
            node_id: NodeId::new(engine.catalog().node_id()).unwrap(),
            group_id: domain::ExecutionGroupId::new("unit-group").unwrap(),
            owner: domain::AllocationOwner::Task(task_ref.clone()),
            tenant_id: "unit-tenant".into(),
            task_ref,
            role_id: domain::RoleId::new("unit-role").unwrap(),
            units,
            allocation_units: units,
            admitted_capacity: 4,
        };
        grant.allocation_id = grant.original_allocation_id();
        grant
    }

    /// Constructs only the persisted authority fields used by the capacity gate.
    fn invocation(grant: &ResourceAllocationGrant) -> serde_json::Value {
        serde_json::json!({"mission_id":grant.task_ref.mission_id(),"task_id":grant.task_ref.task_id(),"group_id":grant.group_id,"role_id":grant.role_id,"resource_ids":[grant.resource_id],"resource_grants":[grant],"operation":"compute.work@v1","parameters":{}})
    }

    /// Journaled nonterminal original owners survive restart and prevent a third allocation.
    #[test]
    fn node_capacity_retains_original_unknown_attempts_without_repeating_dispatch() {
        let directory = tempfile::tempdir().unwrap();
        let engine = engine(directory.path());
        for (index, task) in ["first", "second"].iter().enumerate() {
            let invocation = invocation(&grant(&engine, task, 2, index as u64 + 1));
            engine
                .validate_capacity_admission(Some((task, &invocation)))
                .unwrap();
            let spec = ExecutionSpec::new(
                serde_json::to_vec(&invocation).unwrap(),
                "capacity-unit-original-workflow",
                vec!["compute-slot".into()],
            )
            .unwrap();
            engine.inner.journal.prepare_dispatch(task, &spec).unwrap();
            engine.inner.journal.authorize_local_dispatch(task).unwrap();
        }
        let third = invocation(&grant(&engine, "third", 1, 3));
        assert!(
            engine
                .validate_capacity_admission(Some(("third", &third)))
                .is_err()
        );
        let before = engine
            .inner
            .journal
            .replay_records()
            .unwrap()
            .into_iter()
            .map(|row| row.spec().invocation_content().to_vec())
            .collect::<Vec<_>>();
        drop(engine);
        let restarted = self::engine(directory.path());
        restarted.validate_capacity_admission(None).unwrap();
        assert!(
            restarted
                .validate_capacity_admission(Some(("third", &third)))
                .is_err()
        );
        let records = restarted.inner.journal.replay_records().unwrap();
        assert!(
            records
                .iter()
                .all(|row| row.status() == JournalStatus::ReconciliationRequired)
        );
        assert_eq!(
            records
                .iter()
                .map(|row| row.spec().invocation_content().to_vec())
                .collect::<Vec<_>>(),
            before
        );
    }

    /// Wrong original owner, changed semantic units and ungranted shared resource IDs fail independently.
    #[test]
    fn node_capacity_rejects_missing_and_rebound_original_grants() {
        let directory = tempfile::tempdir().unwrap();
        let engine = engine(directory.path());
        let original = invocation(&grant(&engine, "first", 2, 1));
        invocation_grants(engine.catalog(), &original).unwrap();
        for (path, value) in [
            ("/task_id", serde_json::json!("other")),
            (
                "/resource_grants/0/resource_class",
                serde_json::json!("foreign.units/v1"),
            ),
            ("/resource_grants", serde_json::json!([])),
        ] {
            let mut changed = original.clone();
            *changed.pointer_mut(path).unwrap() = value;
            assert!(invocation_grants(engine.catalog(), &changed).is_err());
        }
    }
}
