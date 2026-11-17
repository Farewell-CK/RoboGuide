//! Offline plan/configuration counterexamples, with no Local EAIOS or Controller process.

use super::*;

/// Writes a complete modern configuration whose private resource is a Compute slot.
fn config(directory: &std::path::Path, name: &str, pointer: &str, capacity: u32) -> PathBuf {
    let source = include_str!("../../../config/node.toml")
        .replace("generic-node", name)
        .replace("compute.noop@v1", "object.relocate@v1")
        .replace(
            "pointer = \"/invocation\"",
            &format!("pointer = \"{pointer}\""),
        )
        .replace("capacity = 1", &format!("capacity = {capacity}"));
    let path = directory.join(format!("{name}.toml"));
    std::fs::write(&path, source).unwrap();
    path
}

/// Supplies an independently parsed canonical plan, retaining exact scalar parameter types.
fn plan_document() -> Value {
    serde_json::from_str(include_str!(
        "../../../scenarios/mission-front-half-v0.7/mission-plan.json"
    ))
    .unwrap()
}

/// Decodes through the production parser so malformed test plans never bypass canonical policy.
fn plan(document: &Value) -> MissionPlan {
    orchestration::decode_mission_plan(&document.to_string()).unwrap()
}

/// Missing demands and insufficient mandatory resources fail even when another ID has capacity.
#[test]
fn checks_exact_mandatory_resources_without_borrowing_alternative_capacity() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(directory.path(), "private-node", "/invocation", 1);
    let mut source = std::fs::read_to_string(&path).unwrap();
    source.push_str("\n[[resources]]\nid = \"other-slot\"\nkind = \"compute\"\ncapacity = 100\nowner = \"local-runtime\"\n");
    std::fs::write(&path, source).unwrap();
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    for resources in [
        serde_json::json!([]),
        serde_json::json!([{"kind":"space", "units":1}]),
        serde_json::json!([{"kind":"compute", "units":2}]),
    ] {
        document["tasks"][0]["roles"][0]["requirements"]["resources"] = resources;
        let issues = preflight.issues(&plan(&document));
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "node_preflight.resources_incompatible");
        assert_eq!(issues[0].path, "/tasks/0/roles/0/requirements/resources");
    }
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    assert!(preflight.issues(&plan(&document)).is_empty());
    assert!(!directory.path().join("node-state").exists());
    assert!(!directory.path().join("artifact-cache").exists());
}

/// Parameters and resources must agree on one Node; two partial declarations cannot be pooled.
#[test]
fn keeps_parameter_and_resource_compatibility_on_the_same_node() {
    let directory = tempfile::tempdir().unwrap();
    let a = config(
        directory.path(),
        "private-a",
        "/invocation/parameters/destination",
        1,
    );
    let b = config(
        directory.path(),
        "private-b",
        "/invocation/parameters/other",
        10,
    );
    let preflight = NodePlanPreflight::load(&[a, b]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":2}]);
    assert_eq!(
        preflight.issues(&plan(&document))[0].code,
        "node_preflight.resources_incompatible"
    );
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["other"] = "actual".into();
    assert!(preflight.issues(&plan(&document)).is_empty());
}

/// RFC 6901 parameter escaping resolves actual scalar keys, including pointers inside functions.
#[test]
fn checks_concrete_parameters_and_preserves_source_provenance() {
    let directory = tempfile::tempdir().unwrap();
    let a = config(
        directory.path(),
        "private-a",
        "/invocation/parameters/route~1target~0frame",
        1,
    );
    let b = config(
        directory.path(),
        "private-b",
        "/invocation/parameters/route~1target~0frame",
        1,
    );
    for path in [&a, &b] {
        let source = std::fs::read_to_string(path).unwrap().replace(
            "kind = \"pointer\"\npointer = \"/invocation/parameters/route~1target~0frame\"",
            "kind = \"function\"\nfunction = \"to_string\"\narguments = [{ kind = \"pointer\", pointer = \"/invocation/parameters/route~1target~0frame\" }]",
        );
        std::fs::write(path, source).unwrap();
    }
    let preflight = NodePlanPreflight::load(&[a.clone(), b.clone()]).unwrap();
    let reverse = NodePlanPreflight::load(&[b, a.clone()]).unwrap();
    assert_eq!(preflight.source_digest(), reverse.source_digest());
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    assert_eq!(
        preflight.issues(&plan(&document))[0].code,
        "node_preflight.parameters_incompatible"
    );
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["route/target~frame"] =
        1.into();
    let body = document.to_string();
    let report = preflight.assess(&plan(&document), body.as_bytes());
    assert_eq!(report["decision"], "not_blocked");
    assert_eq!(
        report["plan_body_sha256"],
        format!("sha256:{:x}", Sha256::digest(body.as_bytes()))
    );
    assert!(!report.to_string().contains("private-a"));
    std::fs::write(
        &a,
        format!("{}\n# new bytes\n", std::fs::read_to_string(&a).unwrap()),
    )
    .unwrap();
    assert_ne!(
        NodePlanPreflight::load(&[a]).unwrap().source_digest(),
        preflight.source_digest()
    );
}

/// Array bounds follow actual declared demand counts without inventing selected IDs or grants.
#[test]
fn checks_plan_known_runtime_array_bounds() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(
        directory.path(),
        "private-array",
        "/invocation/resource_ids/1",
        1,
    );
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    assert_eq!(
        preflight.issues(&plan(&document))[0].code,
        "node_preflight.parameters_incompatible"
    );
}

/// Exact mode and unit class remain mandatory for quantitative pools, beside the original capacity.
#[test]
fn preserves_quantitative_allocation_mode_and_class() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(
        directory.path(),
        "private-pool",
        "/invocation/resource_grants/0/resource_class",
        10,
    );
    let source = std::fs::read_to_string(&path)
        .unwrap()
        .replacen(
            "roboguide.node-config/v0.7",
            "roboguide.node-config/v0.14",
            1,
        )
        .replace(
            "capacity = 10",
            "capacity = 10\nallocation_mode = \"divisible\"\nresource_class = \"gpu.units/v1\"",
        );
    std::fs::write(&path, source).unwrap();
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["schema_version"] = "roboguide.mission-plan/v0.10".into();
    document["contexts"][0]["executor_constraints"] = serde_json::json!([]);
    for (mode, class, units, allowed) in [
        ("divisible", "gpu.units/v1", 2, true),
        ("divisible", "cpu.units/v1", 2, false),
        ("divisible", "gpu.units/v1", 11, false),
    ] {
        document["tasks"][0]["roles"][0]["requirements"]["resources"] = serde_json::json!([
            {"kind":"compute", "units": units, "allocation_mode": mode, "resource_class": class}
        ]);
        assert_eq!(preflight.issues(&plan(&document)).is_empty(), allowed);
    }
    document["tasks"][0]["roles"][0]["requirements"]["resources"] = serde_json::json!([
        {"kind":"compute", "units":2}
    ]);
    assert!(!preflight.issues(&plan(&document)).is_empty());
}

/// Duplicate identities, bad sources and empty or oversized cohorts never produce coverage.
#[test]
fn refuses_unavailable_or_ambiguous_configuration_sources() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(directory.path(), "private-node", "/invocation", 1);
    assert!(NodePlanPreflight::load(&[]).is_err());
    assert!(NodePlanPreflight::load(&vec![path.clone(); 65]).is_err());
    assert!(NodePlanPreflight::load(&[path.clone(), path]).is_err());
    let invalid = directory.path().join("secret-path.toml");
    std::fs::write(&invalid, "secret-token = \"unclosed").unwrap();
    let error = NodePlanPreflight::load(&[invalid]).unwrap_err();
    assert!(!error.contains("secret"));
    let missing = directory.path().join("missing.toml");
    assert!(NodePlanPreflight::load(&[missing]).is_err());
    let oversized = directory.path().join("oversized.toml");
    std::fs::write(&oversized, vec![b' '; MAX_CONFIG_BYTES as usize + 1]).unwrap();
    assert!(NodePlanPreflight::load(&[oversized]).is_err());
}

/// A large incompatible draft retains the original byte digest and bounded diagnostics.
#[test]
fn caps_private_diagnostics_without_erasing_truncation() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(directory.path(), "private-node", "/invocation", 1);
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    let template = document["tasks"][0].clone();
    document["tasks"] = serde_json::json!(
        (0..40)
            .map(|index| {
                let mut task = template.clone();
                task["id"] = format!("task-{index}").into();
                task
            })
            .collect::<Vec<_>>()
    );
    let bytes = serde_json::to_vec(&document).unwrap();
    let report = preflight.assess(&plan(&document), &bytes);
    assert_eq!(report["decision"], "blocked");
    assert_eq!(report["issues_truncated"], true);
    assert_eq!(report["issues"].as_array().unwrap().len(), 32);
}

/// Existence of a scalar parameter cannot make an impossible pure conversion executable.
#[test]
fn preflight_audit_rejects_concrete_parameter_conversion() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(
        directory.path(),
        "private-conversion",
        "/invocation/parameters/destination",
        1,
    );
    let source = std::fs::read_to_string(&path).unwrap().replace(
        "kind = \"pointer\"\npointer = \"/invocation/parameters/destination\"",
        "kind = \"function\"\nfunction = \"to_integer\"\narguments = [{ kind = \"pointer\", pointer = \"/invocation/parameters/destination\" }]",
    );
    std::fs::write(&path, source).unwrap();
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    assert!(!preflight.issues(&plan(&document)).is_empty());
}

/// Artifact-reserved parameters require a compatible explicitly declared local action.
#[test]
fn preflight_audit_rejects_undeclared_artifact_action() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(directory.path(), "private-artifact", "/invocation", 1);
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["artifact_slot"] =
        "undeclared".into();
    assert!(!preflight.issues(&plan(&document)).is_empty());
}

/// Existing Control-generated resource identities remain unknown rather than converted from null.
#[test]
fn generated_resource_values_are_not_fabricated_for_pure_conversion() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(
        directory.path(),
        "private-generated",
        "/invocation/resource_ids/0",
        1,
    );
    let source = std::fs::read_to_string(&path).unwrap().replace(
        "kind = \"pointer\"\npointer = \"/invocation/resource_ids/0\"",
        "kind = \"function\"\nfunction = \"to_string\"\narguments = [{ kind = \"pointer\", pointer = \"/invocation/resource_ids/0\" }]",
    );
    std::fs::write(&path, source).unwrap();
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    assert!(preflight.issues(&plan(&document)).is_empty());
}

/// Declared and selected Artifact slots must agree before any CAS, journal or local call.
#[test]
fn artifact_plan_preflight_checks_binding_selectors_and_selected_path() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(
        directory.path(),
        "private-map",
        "/artifacts/inputs/map~0input",
        1,
    );
    let mut source = std::fs::read_to_string(&path).unwrap().replace(
        "required_resources = [\"compute-slot\"]",
        "artifact_operation = \"import\"\nrequired_resources = [\"compute-slot\"]",
    );
    for (id, target) in [("map~input", "map.bin"), ("other", "other.bin")] {
        source.push_str(&format!("\n[[artifacts.input_bindings]]\nid = \"{id}\"\nmap_id = \"lab-map\"\nrevision_id = \"r1\"\ntarget_path = \"{target}\"\n"));
    }
    std::fs::write(&path, source).unwrap();
    let preflight = NodePlanPreflight::load(&[path]).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    let parameters = serde_json::json!({"artifact_operation": "import", "artifact_slot": "map~input",
        "map_id": "lab-map", "revision_id": "r1", "spatial_anchor_id": "anchor"});
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"] = parameters.clone();
    assert!(preflight.issues(&plan(&document)).is_empty());
    for (field, value) in [
        ("artifact_slot", "other"),
        ("map_id", "different-map"),
        ("revision_id", "other-revision"),
        ("artifact_operation", "verify"),
    ] {
        document["tasks"][0]["roles"][0]["execution_intent"]["parameters"] = parameters.clone();
        document["tasks"][0]["roles"][0]["execution_intent"]["parameters"][field] = value.into();
        assert!(!preflight.issues(&plan(&document)).is_empty(), "{field}");
    }
    assert!(!directory.path().join("node-state").exists());
    assert!(!directory.path().join("artifact-cache").exists());
}

/// A compatible output on one provider cannot supply the selected foreign provider's ownership.
#[test]
fn artifact_output_preflight_retains_exact_producer_owner() {
    let directory = tempfile::tempdir().unwrap();
    let path = config(directory.path(), "private-producer", "/invocation", 1);
    let mut source = std::fs::read_to_string(&path)
        .unwrap()
        .replacen(
            "roboguide.node-config/v0.7",
            "roboguide.node-config/v0.9",
            1,
        )
        .replace("kind = \"artifact\"", "kind = \"spatial\"")
        .replace("scope = \"local\"", "scope = \"global\"")
        .replace(
            "visibility = \"discoverable\"",
            "visibility = \"exchangeable\"",
        )
        .replace("roboguide.example.local-artifact/v0.1", "example.map/v1")
        .replace(
            "required_resources = [\"compute-slot\"]",
            "artifact_operation = \"prepare-output\"\nrequired_resources = [\"compute-slot\"]",
        );
    source.push_str(
        r#"
[[memory_providers]]
id = "foreign-provider"
owner = "local-sensors"
kind = "spatial"
scope = "global"
visibility = "exchangeable"
payload_schema = "example.map/v1"
media_type = "application/octet-stream"
"#,
    );
    for (slot, provider) in [
        ("out-local", "local-artifacts"),
        ("out-foreign", "foreign-provider"),
    ] {
        source.push_str(&format!(
            r#"
[[artifacts.output_bindings]]
id = "{slot}"
map_id = "lab-map"
revision_id = "r1"
source_path = "{slot}.bin"
producer_provider_id = "{provider}"
payload_schema = "example.map/v1"
media_type = "application/octet-stream"
format_name = "bundle"
format_version = "v1"
root_frame = "map"
coordinate_convention = "enu"
spatial_anchor_id = "anchor"
"#
        ));
    }
    std::fs::write(&path, &source).unwrap();
    let preflight = NodePlanPreflight::load(std::slice::from_ref(&path)).unwrap();
    let mut document = plan_document();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        serde_json::json!([{"kind":"compute", "units":1}]);
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"] = serde_json::json!({
        "artifact_operation": "prepare-output", "artifact_slot": "out-local", "map_id": "lab-map",
        "revision_id": "r1", "spatial_anchor_id": "anchor"
    });
    assert!(preflight.issues(&plan(&document)).is_empty());
    document["tasks"][0]["roles"][0]["execution_intent"]["parameters"]["artifact_slot"] =
        "out-foreign".into();
    assert!(!preflight.issues(&plan(&document)).is_empty());
    source = source.replacen(
        "owner = \"local-runtime\"\nkind = \"spatial\"",
        "owner = \"local-sensors\"\nkind = \"spatial\"",
        1,
    );
    std::fs::write(&path, source).unwrap();
    assert!(NodePlanPreflight::load(&[path]).is_err());
}
