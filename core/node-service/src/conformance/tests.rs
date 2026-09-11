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
