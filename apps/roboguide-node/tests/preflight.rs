//! Real offline CLI checks, with no Controller, Local EAIOS, models or device workers.

use serde_json::{Value, json};
use std::process::Command;

/// Launches the built executable against owned temporary inputs, checking its real exit status.
#[test]
fn offline_cli_reports_known_conflicts_and_leaves_runtime_storage_absent() {
    let directory = tempfile::tempdir().unwrap();
    let config_path = directory.path().join("node.toml");
    let plan_path = directory.path().join("plan.json");
    let source = include_str!("../../../config/node.toml")
        .replace("compute.noop@v1", "object.relocate@v1")
        .replace(
            "pointer = \"/invocation\"",
            "pointer = \"/invocation/parameters/destination\"",
        );
    std::fs::write(&config_path, &source).unwrap();
    let mut plan: Value = serde_json::from_str(include_str!(
        "../../../scenarios/mission-front-half-v0.7/mission-plan.json"
    ))
    .unwrap();
    for (resources, expected) in [
        (json!([]), "blocked"),
        (json!([{"kind":"compute", "units":1}]), "not_blocked"),
    ] {
        plan["tasks"][0]["roles"][0]["requirements"]["resources"] = resources;
        std::fs::write(&plan_path, plan.to_string()).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
            .args(["--validate"])
            .arg(&config_path)
            .arg("--plan")
            .arg(&plan_path)
            .output()
            .unwrap();
        assert_eq!(output.status.success(), expected == "not_blocked");
        let report: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            report["schema_version"],
            "roboguide.node-plan-preflight/v0.1"
        );
        assert_eq!(report["decision"], expected);
        assert_eq!(report["runtime_probes_executed"], false);
    }
    let output = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
        .arg("--validate")
        .arg(&config_path)
        .output()
        .unwrap();
    assert!(output.status.success());
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["schema"], "roboguide.extension-conformance/v0.2");
    assert_eq!(report["controller_contacted"], false);

    std::fs::write(
        &config_path,
        source.replace("accepted = [\"ACCEPTED\"]", "accepted = []"),
    )
    .unwrap();
    for with_plan in [false, true] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_roboguide-node"));
        command.arg("--validate").arg(&config_path);
        if with_plan {
            command.arg("--plan").arg(&plan_path);
        }
        let output = command.output().unwrap();
        assert!(
            !output.status.success(),
            "a plan cannot bypass original conformance gates"
        );
    }

    std::fs::write(
        &config_path,
        source.replace(
            "/invocation/parameters/destination",
            "/invocation/intent/parameters/destination",
        ),
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
        .arg("--validate")
        .arg(&config_path)
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("/invocation/intent/parameters/destination")
    );

    let second_path = directory.path().join("second-node.toml");
    std::fs::write(
        &config_path,
        source.replace("object.relocate@v1", "compute.noop@v1"),
    )
    .unwrap();
    std::fs::write(&second_path, source.replace("generic-node", "second-node")).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
        .arg("--validate")
        .arg(&config_path)
        .arg("--node-config")
        .arg(&second_path)
        .arg("--plan")
        .arg(&plan_path)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report["decision"], "not_blocked");
    assert!(!directory.path().join("node-state").exists());
    assert!(!directory.path().join("artifact-cache").exists());
}
