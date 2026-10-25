//! Retained typed production provenance for exact published scoped Memory children.

use crate::*;

/// One immutable original publication, independent of current availability or retired CAS bytes.
pub(crate) struct PublicationEdge {
    /// Exact published receiving child.
    pub(crate) child: domain::MemoryArtifactManifest,
    /// Exact original source, global or previously published scoped child.
    pub(crate) parent: domain::MemoryArtifactManifest,
    /// Complete original production provenance, unchanged throughout the chain.
    pub(crate) root: domain::MapArtifactManifest,
}

/// Verifies the complete retained provenance DAG without acquiring use or requiring released bytes.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    let mut edges = BTreeMap::new();
    for edge in super::memory_handoff::typed_publication_edges(controller)
        .into_iter()
        .chain(super::plan_revision::typed_publication_edges(controller))
    {
        let key = edge.child.selector().to_string();
        let domain::MemoryScope::ExecutionGroup(group) = edge.child.scope() else {
            return Err("typed publication child lacks its original exact Group scope".into());
        };
        domain::validate_scoped_map_manifest(&edge.root, &edge.child, group)?;
        if edge.parent.created_at() > edge.child.created_at() || edges.insert(key, edge).is_some() {
            return Err(
                "typed publication ancestry is ambiguous or regresses original time".into(),
            );
        }
    }
    for edge in edges.values() {
        let root = domain::map_memory_manifest(&edge.root)?;
        let mut current = edge;
        let mut visited = BTreeSet::new();
        while current.parent != root {
            if !visited.insert(current.child.selector().to_string()) {
                return Err("typed publication ancestry contains a cycle".into());
            }
            let parent = edges
                .get(&current.parent.selector().to_string())
                .ok_or("typed publication lacks its original scoped parent publication")?;
            if parent.child != current.parent || parent.root != edge.root {
                return Err("typed publication changed its exact parent or original root".into());
            }
            current = parent;
        }
    }
    Ok(())
}

/// Permits only exact retained ancestors of this actual prepared child during scoped CAS exchange.
/// The caller still rechecks the current child import, physical participant, policy and budget.
pub(crate) fn permits_ancestor_reference(
    controller: &ControllerState,
    protected: &domain::MemoryArtifactManifest,
    import_id: &str,
) -> Result<bool, String> {
    let Some(import) = controller.memory_imports.get(import_id) else {
        return Ok(false);
    };
    let Some(preparation) = controller
        .memory_preparation
        .records
        .get(&import.request.execution_id)
        .and_then(|record| record.preparation.as_ref())
    else {
        return Ok(false);
    };
    if preparation.schema != "roboguide.memory-preparation/v0.3"
        || import.request.preparation_id.as_deref() != Some(preparation.preparation_id.as_str())
    {
        return Ok(false);
    }
    let Some(input) = preparation.inputs.iter().find(|input| {
        input.import_id == import_id
            && input.manifest == import.request.manifest
            && input.typed_map == import.request.typed_map
    }) else {
        return Ok(false);
    };
    let Some(root) = &input.typed_map else {
        return Ok(false);
    };
    let edges = super::memory_handoff::typed_publication_edges(controller)
        .into_iter()
        .chain(super::plan_revision::typed_publication_edges(controller))
        .collect::<Vec<_>>();
    let mut current = &input.manifest;
    let mut visited = BTreeSet::new();
    while visited.insert(current.selector().clone()) {
        let mut candidates = edges
            .iter()
            .filter(|edge| &edge.child == current && &edge.root == root);
        let Some(edge) = candidates.next() else {
            return Ok(false);
        };
        if candidates.next().is_some()
            || original_typed_source(controller, current, None)?.as_ref() != Some(root)
        {
            return Err(
                "scoped ancestor permission lacks unique current retained publication".into(),
            );
        }
        if &edge.parent == protected {
            return Ok(true);
        }
        current = &edge.parent;
    }
    Err("scoped ancestor permission contains a retained publication cycle".into())
}

/// Requires the exact current source attempt's original preparation beside immutable root lineage.
/// Legacy scoped imports retain generic provenance; only their actual published origin supplies a root.
pub(crate) fn validate_source_reference(
    controller: &ControllerState,
    map: &domain::MapArtifactManifest,
    parent: &domain::MemoryArtifactManifest,
    group: &domain::ExecutionGroupId,
    source_ids: Option<&[String]>,
) -> Result<(), String> {
    validate_source_preparation(controller, map, parent, group, source_ids, true)
}

/// Retains original source context linkage on restore without demanding a living retired attempt.
pub(crate) fn validate_historical_source_reference(
    controller: &ControllerState,
    map: &domain::MapArtifactManifest,
    parent: &domain::MemoryArtifactManifest,
    group: &domain::ExecutionGroupId,
    source_ids: Option<&[String]>,
) -> Result<(), String> {
    validate_source_preparation(controller, map, parent, group, source_ids, false)
}

/// Separates immutable historical context proof from current ownership checks at new admission.
fn validate_source_preparation(
    controller: &ControllerState,
    map: &domain::MapArtifactManifest,
    parent: &domain::MemoryArtifactManifest,
    group: &domain::ExecutionGroupId,
    source_ids: Option<&[String]>,
    current: bool,
) -> Result<(), String> {
    let root = domain::map_memory_manifest(map)?;
    let scoped = parent != &root;
    if scoped && original_typed_source(controller, parent, None)?.as_ref() != Some(map) {
        return Err("source scoped parent has no exact retained original typed publication".into());
    }
    if !controller
        .memory_preparation
        .records
        .values()
        .any(|record| {
            record.preparation.as_ref().is_some_and(|preparation| {
                &preparation.group_id == group
                    && source_ids.is_none_or(|ids| ids.contains(&preparation.execution_id))
                    && (!current
                        || controller.bridge.current_task_attempt_id(
                            group,
                            &preparation.task_ref,
                            &preparation.role_id,
                        ) == Some(preparation.execution_id.as_str()))
                    && preparation.inputs.iter().any(|input| {
                        input.manifest == *parent
                            && (input.typed_map.as_ref() == Some(map)
                                || scoped && input.typed_map.is_none())
                    })
            })
        })
    {
        return Err("typed scope transfer lacks the exact current source preparation".into());
    }
    Ok(())
}

/// Refuses a downgrade of a newly declared scoped lifecycle to an old generic context.
pub(crate) fn requires_scoped_preparation(
    controller: &ControllerState,
    manifest: &domain::MemoryArtifactManifest,
) -> bool {
    super::memory_handoff::requires_scoped_preparation(controller, manifest)
        || super::plan_revision::requires_scoped_preparation(controller, manifest)
}

/// Resolves only an exact Catalog source or an actually published original handoff/revision child.
/// This returns metadata; current Control permissions, attempts, import and use remain independent.
pub(crate) fn original_typed_source(
    controller: &ControllerState,
    manifest: &domain::MemoryArtifactManifest,
    catalog_map: Option<domain::MapArtifactManifest>,
) -> Result<Option<domain::MapArtifactManifest>, String> {
    if let Some(map) = catalog_map {
        if domain::map_memory_manifest(&map)? != *manifest {
            return Err("Catalog typed source differs from its immutable projection".into());
        }
        return Ok(Some(map));
    }
    let handoff = super::memory_handoff::scoped_typed_source(controller, manifest)?;
    let revision = super::plan_revision::scoped_typed_source(controller, manifest)?;
    let original = match (handoff, revision) {
        (Some(first), Some(second)) if first != second => {
            return Err("scoped child has conflicting original typed roots".into());
        }
        (Some(map), _) | (_, Some(map)) => Some(map),
        (None, None) => None,
    };
    if let Some(map) = &original {
        let domain::MemoryScope::ExecutionGroup(group) = manifest.scope() else {
            return Err("retained typed child lost its exact receiving Group".into());
        };
        domain::validate_scoped_map_manifest(map, manifest, group)?;
    }
    Ok(original)
}
