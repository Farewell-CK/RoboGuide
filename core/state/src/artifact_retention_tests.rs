//! Contract counterexamples for original ownership, irreversible release and committed SQL reads.

use super::*;
use domain::{
    ArtifactCatalogReference, ArtifactCatalogRetirement, ArtifactContentDependency,
    ArtifactDependencyKind, MemoryId, MemoryRevisionId, MemorySelector,
};
use ports::EventSink;

/// Declares one unit-contract identity; it never enters Node registration or deployment evidence.
fn original(name: &str, owner: char) -> ArtifactCatalogReference {
    let selector = MemorySelector::new(
        MemoryId::new(name).unwrap(),
        MemoryRevisionId::new("v1").unwrap(),
    );
    ArtifactCatalogReference {
        schema: "roboguide.artifact-catalog-reference/v0.1".into(),
        selector: selector.clone(),
        tenant_id: "unit-tenant".into(),
        principal_id: "unit-publisher".into(),
        authority_digest: format!("sha256:{}", "a".repeat(64)),
        generation: 1,
        admitted_at_ms: 20,
        dependencies: vec![ArtifactContentDependency {
            kind: ArtifactDependencyKind::Catalog,
            store_owner_id: format!("artifact-store:{}", owner.to_string().repeat(64)),
            provider_id: "unit-semantic-provider".into(),
            selector,
            manifest_digest: format!("sha256:{}", "b".repeat(64)),
            typed_map: None,
            content_digest: format!("sha256:{}", "c".repeat(64)),
            byte_size: 7,
        }],
        retirement: None,
    }
}

/// Retains exact original acquisition while adding a distinct explicit proof-bearing command.
fn retired(record: &ArtifactCatalogReference, command: &str) -> ArtifactCatalogReference {
    let mut result = record.clone();
    result.retirement = Some(ArtifactCatalogRetirement {
        command_id: command.into(),
        principal_id: "unit-current-administrator".into(),
        proof_digest: format!("sha256:{}", "d".repeat(64)),
        retired_at_ms: 30,
    });
    result
}

/// Parent retirement leaves its child's independent lineage and a foreign equal-hash owner intact.
#[test]
fn child_and_foreign_owners_do_not_share_retirement() {
    let parent = original("parent", '1');
    let foreign = original("foreign", '2');
    let mut child = original("child", '1');
    let mut dependency = parent.dependencies[0].clone();
    dependency.kind = ArtifactDependencyKind::Lineage;
    child.dependencies.push(dependency);
    let mut projection = ArtifactRetentionProjection::default();
    for record in [&parent, &foreign, &child] {
        projection.apply(record).unwrap();
    }
    projection
        .apply(&retired(&parent, "retire-parent"))
        .unwrap();
    let owner = &parent.dependencies[0].store_owner_id;
    let digest = &parent.dependencies[0].content_digest;
    assert!(projection.retains(owner, digest));
    projection.apply(&retired(&child, "retire-child")).unwrap();
    assert!(!projection.retains(owner, digest));
    assert!(projection.retains(&foreign.dependencies[0].store_owner_id, digest));
    assert_eq!(projection.records().count(), 3);
    assert!(projection.is_retired(&parent.selector));
}

/// New time, tenant, source, generation, orphan retirement and command reuse cannot create authority.
#[test]
fn original_authority_and_retirement_cannot_be_replaced() {
    let record = original("original", '1');
    let mut projection = ArtifactRetentionProjection::default();
    assert!(projection.apply(&retired(&record, "orphan")).is_err());
    projection.apply(&record).unwrap();
    let mut changed = record.clone();
    changed.admitted_at_ms += 1;
    assert!(projection.apply(&changed).is_err());
    changed = record.clone();
    changed.tenant_id = "replacement".into();
    assert!(projection.apply(&changed).is_err());
    changed = record.clone();
    changed.dependencies[0].store_owner_id = format!("artifact-store:{}", "2".repeat(64));
    assert!(projection.apply(&changed).is_err());
    changed = record.clone();
    changed.generation += 1;
    assert!(projection.apply(&changed).is_err());
    let release = retired(&record, "original-release");
    projection.apply(&release).unwrap();
    projection.apply(&release).unwrap();
    assert!(projection.apply(&record).is_err());
    let another = original("another", '1');
    projection.apply(&another).unwrap();
    assert!(
        projection
            .apply(&retired(&another, "original-release"))
            .is_err()
    );
    assert_eq!(projection.record(&record.selector), Some(&release));
}

/// Equal discovery metadata cannot hide a changed original map frame or canonical provenance bytes.
#[test]
fn typed_parent_frame_remains_part_of_original_retention_authority() {
    let mut record = original("unit-map", '1');
    let selector = domain::MapRevisionSelector::new(
        domain::MapId::new("unit-map").unwrap(),
        domain::MapRevisionId::new("v1").unwrap(),
    );
    let map = domain::MapArtifactManifest::new(
        domain::MapArtifactRef::new(
            selector,
            domain::ContentDigest::new(&record.dependencies[0].content_digest).unwrap(),
            7,
        ),
        "application/octet-stream",
        "unit-format",
        domain::NodeId::new("unit-producer").unwrap(),
        Some(domain::LocalSystemId::new("unit-local-system").unwrap()),
        domain::MissionId::new("unit-origin").unwrap(),
        Some("unit-original-attempt".into()),
        None,
        "original-frame",
        "enu",
        domain::SpatialAnchorId::new("unit-anchor").unwrap(),
        Some(1e-9),
        domain::TimestampMs::new(1),
        None,
    )
    .unwrap();
    let generic = domain::map_memory_manifest(&map).unwrap();
    let dependency = &mut record.dependencies[0];
    dependency.provider_id = generic.provider_id().into();
    dependency.manifest_digest = domain::memory_manifest_digest(&generic).unwrap();
    dependency.typed_map = Some(domain::ArtifactTypedMapProvenance {
        canonical_json: serde_json::to_string(&serde_json::to_value(&map).unwrap()).unwrap(),
        manifest_digest: domain::map_manifest_digest(&map).unwrap(),
    });
    record.validate().unwrap();
    let mut projection = ArtifactRetentionProjection::default();
    projection.apply(&record).unwrap();
    let mut changed = record.clone();
    let proof = changed.dependencies[0].typed_map.as_mut().unwrap();
    let mut source: serde_json::Value = serde_json::from_str(&proof.canonical_json).unwrap();
    source["root_frame"] = "different-frame".into();
    proof.canonical_json = serde_json::to_string(&source).unwrap();
    assert!(changed.validate().is_err());
    let changed_map: domain::MapArtifactManifest = serde_json::from_value(source).unwrap();
    assert_eq!(domain::map_memory_manifest(&changed_map).unwrap(), generic);
    changed.dependencies[0]
        .typed_map
        .as_mut()
        .unwrap()
        .manifest_digest = domain::map_manifest_digest(&changed_map).unwrap();
    changed.validate().unwrap();
    assert!(projection.apply(&changed).is_err());
}

/// Indexed observations expose only committed original heads; an older codec cannot hide the new kind.
#[test]
fn reference_index_refuses_private_or_mislabeled_authority() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let mut log = SqliteEventLog::open(&path).unwrap();
    let record = original("committed", '1');
    let correlation = domain::CorrelationId::new("unit-sql-reference").unwrap();
    log.begin_batch().unwrap();
    log.append(
        domain::TimestampMs::new(20),
        &correlation,
        None,
        domain::EventPayload::ArtifactCatalogReferenceTransition {
            record: record.clone(),
        },
    );
    assert!(log.artifact_catalog_reference(&record.selector).is_err());
    log.rollback_batch().unwrap();
    assert_eq!(
        log.artifact_catalog_reference(&record.selector).unwrap(),
        None
    );
    log.begin_batch().unwrap();
    log.append(
        domain::TimestampMs::new(20),
        &correlation,
        None,
        domain::EventPayload::ArtifactCatalogReferenceTransition {
            record: record.clone(),
        },
    );
    log.save_checkpoint("unit-opaque-checkpoint/v1", "{}")
        .unwrap();
    log.commit_batch().unwrap();
    assert_eq!(
        log.artifact_catalog_reference(&record.selector).unwrap(),
        Some(record)
    );
    let events = log.events().unwrap();
    assert_eq!(events[0].payload_schema, "domain.EventPayload.json/v29");
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute(
            "UPDATE events SET payload_schema='domain.EventPayload.json/v28'",
            [],
        )
        .unwrap();
    assert!(
        log.decoded_events()
            .unwrap_err()
            .to_string()
            .contains("requires schema v29")
    );
}
