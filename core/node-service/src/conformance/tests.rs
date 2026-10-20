use super::*;
use std::path::PathBuf;

/// The checked-in node configuration produces a deterministic report without contacting it.
#[test]
fn checked_in_config_compiles_offline() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/node.toml");
    let report = compile_extension_config(&path).expect("checked-in config compiles");
    assert!(report.offline_compile);
    assert!(!report.controller_contacted);
    assert!(!report.connections.is_empty());
    assert!(report.capabilities.is_empty());
    assert!(!report.capability_profiles.is_empty());
    assert!(!report.operations.is_empty());
    assert!(report.checks.unique_capability_owner);
    assert!(!report.runtime_probes_executed);
    assert!(!report.hardware_probes_executed);
    assert_eq!(report.lifecycle, report.implementation_guarantees);
    assert_eq!(
        report.implementation_guarantees.len(),
        NODE_SERVICE_IMPLEMENTATION_GUARANTEES.len()
    );
}

/// Current authored generations retain exact versions without weakening readiness or mapping checks.
#[test]
fn current_config_generations_compile_without_report_downgrade() {
    let directory = tempfile::tempdir().unwrap();
    let source = std::fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/node.toml"),
    )
    .unwrap();
    for schema in [
        crate::CONFIG_SCHEMA_V0_7,
        crate::CONFIG_SCHEMA_V0_8,
        crate::CONFIG_SCHEMA_V0_9,
        crate::CONFIG_SCHEMA_V0_10,
        crate::CONFIG_SCHEMA_V0_11,
        crate::CONFIG_SCHEMA_V0_12,
        crate::CONFIG_SCHEMA_V0_13,
    ] {
        let path = directory.path().join("current.toml");
        let text = source.replacen(crate::CONFIG_SCHEMA_V0_7, schema, 1);
        std::fs::write(&path, &text).unwrap();
        let report = compile_extension_config(&path).unwrap();
        let compiled = NodeServiceConfig::load_compiled(&path).unwrap();
        if schema != crate::CONFIG_SCHEMA_V0_7 && schema != crate::CONFIG_SCHEMA_V0_8 {
            assert_eq!(report.schema, EXTENSION_CONFORMANCE_SCHEMA_V0_4);
            assert_eq!(report.config_schema.as_deref(), Some(schema));
            assert_eq!(
                report.node_contract_version.as_deref(),
                Some(compiled.node_contract_version())
            );
            assert!(
                report
                    .memory_providers
                    .iter()
                    .all(|provider| provider.original_provider_continuity == Some(false))
            );
        } else {
            assert!(report.config_schema.is_none() && report.node_contract_version.is_none());
        }
        assert!(report.offline_compile && !report.controller_contacted);
        assert!(!report.runtime_probes_executed && !report.hardware_probes_executed);
        assert!(report.checks.exact_readiness && report.checks.execution_state_mapping);
        std::fs::write(
            &path,
            text.replacen("accepted = [\"ACCEPTED\"]", "accepted = []", 1),
        )
        .unwrap();
        assert!(
            matches!(compile_extension_config(&path), Err(ConformanceError::Diagnostic(ConformanceDiagnostic{code,..})) if code == "execution-state-incomplete")
        );
    }
    let path = directory.path().join("future.toml");
    std::fs::write(
        &path,
        source.replacen(crate::CONFIG_SCHEMA_V0_7, "roboguide.node-config/v99", 1),
    )
    .unwrap();
    assert!(compile_extension_config(&path).is_err());
}

/// A future change to maintained deployment schemas must exercise the actual CLI's compiler boundary.
#[test]
fn maintained_habitat_nodes_pass_current_offline_conformance() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    for scenario in [
        "e1-shared-world-episode-51",
        "e1-shared-world-perception",
        "e1-shared-world-rearrangement",
    ] {
        for letter in ["a", "b"] {
            let path = root.join(format!("scenarios/{scenario}/node-{letter}.toml"));
            let compiled = NodeServiceConfig::load_compiled(&path).unwrap();
            let report = compile_extension_config(&path).unwrap();
            assert_eq!(report.schema, EXTENSION_CONFORMANCE_SCHEMA_V0_4);
            assert_eq!(report.config_schema.as_deref(), Some(compiled.schema()));
            assert_eq!(
                report.node_contract_version.as_deref(),
                Some(compiled.node_contract_version())
            );
            assert!(
                !report.controller_contacted
                    && !report.runtime_probes_executed
                    && !report.hardware_probes_executed
            );
        }
    }
}

/// Inheriting schemas retain admitted original-provider facts; mutating queries remain forbidden.
#[test]
fn current_reports_preserve_original_provider_continuity() {
    let directory = tempfile::tempdir().unwrap();
    let mut config: NodeServiceConfig = toml::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../config/node.toml"
    )))
    .unwrap();
    let step = |id: &str, method: &str| crate::WorkflowStepConfig {
        id: id.into(),
        connection: "local-http".into(),
        operation: LocalOperationConfig::Http {
            method: method.into(),
            path: format!("/memory/{id}"),
        },
        request: crate::RequestMappingConfig::default(),
    };
    config.memory_providers[0].import = Some(crate::MemoryWorkflowConfig {
        steps: vec![step("import", "POST")],
        manifests_pointer: None,
        artifact_path_pointer: None,
    });
    config.memory_providers[0].continuity = Some(crate::MemoryContinuityConfig {
        import_result_pointer: "/steps/import/result".into(),
        instance: step("instance", "GET"),
        instance_pointer: "/instance_id".into(),
        query: step("query", "GET"),
        result_pointer: "/result".into(),
    });
    for schema in [
        crate::CONFIG_SCHEMA_V0_8,
        crate::CONFIG_SCHEMA_V0_9,
        crate::CONFIG_SCHEMA_V0_10,
        crate::CONFIG_SCHEMA_V0_11,
        crate::CONFIG_SCHEMA_V0_12,
        crate::CONFIG_SCHEMA_V0_13,
    ] {
        config.schema = schema.into();
        let compiled = CompiledLocalCatalog::compile(config.clone(), directory.path()).unwrap();
        let report = report_for_catalog(Path::new("synthetic-continuity.toml"), &compiled);
        assert_eq!(
            report.memory_providers[0].original_provider_continuity,
            Some(true)
        );
        let mut changed = config.clone();
        changed.memory_providers[0]
            .continuity
            .as_mut()
            .unwrap()
            .query = step("query", "POST");
        assert!(CompiledLocalCatalog::compile(changed, directory.path()).is_err());
    }
}

/// The conformance fixture exercises the shared lifecycle contract for all three drivers.
#[test]
fn all_supported_driver_families_share_lifecycle_shape() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/extension-conformance-v0.1/node.toml");
    let report = compile_extension_config(&path).expect("multi-driver fixture compiles");
    assert_eq!(report.schema, EXTENSION_CONFORMANCE_SCHEMA_V0_1);
    let drivers = report
        .connections
        .iter()
        .map(|connection| connection.driver.as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(drivers, ["grpc", "http", "mcp"].into_iter().collect());
    assert_eq!(report.capabilities.len(), 3);
    for capability in report.capabilities {
        assert!(capability.exact_readiness);
        assert!(capability.readiness.is_some());
        assert!(!capability.workflow.execute.is_empty());
        assert!(!capability.workflow.status.is_empty());
        assert!(!capability.workflow.cancel.is_empty());
        assert!(capability.workflow.local_handle_mapped);
        assert!(capability.workflow.execution_state_mapped);
    }
    assert!(
        report
            .memory_providers
            .iter()
            .all(|provider| provider.node_ledger && !provider.shared_data_plane),
        "conformance distinguishes local workflows from shared exchange readiness"
    );
}

/// Invalid authored files return a path-bearing diagnostic instead of a transport error.
#[test]
fn invalid_config_reports_location() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("invalid.toml");
    std::fs::write(
        &path,
        r#"
schema = "roboguide.node-config/v0.6"
node_id = "node"
server_endpoint = "http://127.0.0.1:50051"
state_directory = "state"

[[local_systems]]
id = "runtime"
runtime_name = "eaios"
runtime_version = "1"
[local_systems.health]
state_pointer = "/state"
online = ["ONLINE"]
degraded = ["DEGRADED"]
offline = ["OFFLINE"]
[local_systems.health.step]
id = "health"
connection = "health"
[local_systems.health.step.operation]
kind = "http"
method = "GET"
path = "/health"

[[connections]]
driver = "http"
id = "health"
local_system = "runtime"
endpoint = "http://127.0.0.1:9000"

[[capabilities]]
contract = "compute.noop@v1"
kind = "compute"
owner = "runtime"
[capabilities.workflow]
execute = []
status = []
cancel = []
"#,
    )
    .expect("invalid config writes");
    let error = compile_extension_config(&path).expect_err("invalid config is rejected");
    assert!(matches!(
        error,
        ConformanceError::Diagnostic(ConformanceDiagnostic { location, .. })
            if location.contains("config")
                || location.contains("capabilities")
                || location.contains("workflow")
    ));
}

/// Conformance rejects a v0.7 workflow that cannot distinguish every execution phase.
#[test]
fn incomplete_execution_state_mapping_is_diagnostic() {
    let source_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/node.toml");
    let source = std::fs::read_to_string(source_path).expect("checked-in config reads");
    let source = source.replacen("accepted = [\"ACCEPTED\"]", "accepted = []", 1);
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("incomplete.toml");
    std::fs::write(&path, source).expect("incomplete config writes");
    assert!(matches!(
        compile_extension_config(&path),
        Err(ConformanceError::Diagnostic(ConformanceDiagnostic {
            code,
            location,
            ..
        })) if code == "execution-state-incomplete"
            && location == "operations.compute.noop@v1.workflow.execution_state"
    ));
}

/// The JSON report does not expose credentials or mutable runtime state.
#[test]
fn json_report_is_stable_and_redacts_runtime_secrets() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/node.toml");
    let json = compile_extension_config_json(&path).expect("report serializes");
    assert!(json.contains("roboguide.extension-conformance/v0.2"));
    assert!(!json.contains("Authorization"));
    assert!(!json.contains("controller_password"));

    let memory_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/extension-conformance-v0.1/node.toml");
    let memory_json =
        compile_extension_config_json(&memory_path).expect("Memory report serializes");
    let memory_report: serde_json::Value =
        serde_json::from_str(&memory_json).expect("Memory report JSON parses");
    assert_eq!(memory_report["schema"], EXTENSION_CONFORMANCE_SCHEMA_V0_1);
    assert!(memory_report.get("capability_profiles").is_none());
    assert!(memory_report.get("operations").is_none());
    assert_eq!(memory_report["memory_providers"][0]["local_backend"], true);
    assert!(memory_report["memory_providers"][0]["node_ledger"].is_null());
}

/// Invalid TOML diagnostics never echo a secret-bearing source line into local or CI logs.
#[test]
fn invalid_config_diagnostic_redacts_source_text() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("secret.toml");
    std::fs::write(
        &path,
        concat!(
            "schema = \"roboguide.node-config/v0.6\"\n",
            "controller_password = \"TOP_SECRET_VALUE\"\n",
        ),
    )
    .expect("invalid secret fixture writes");
    let error = compile_extension_config(&path).expect_err("invalid config is rejected");
    let rendered = error.to_string();
    assert!(!rendered.contains("TOP_SECRET_VALUE"));
    assert!(!rendered.contains("controller_password"));
    assert!(rendered.contains("source text is redacted"));
}
