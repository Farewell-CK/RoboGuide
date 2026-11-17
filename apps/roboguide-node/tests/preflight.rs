//! Real offline CLI checks, with no Controller, Local EAIOS, models or device workers.

use serde_json::{Value, json};
use std::process::Command;

/// Launches the built executable against owned temporary inputs, checking its real exit status.
#[test]
fn offline_cli_reports_known_conflicts_and_leaves_runtime_storage_absent() {
    let directory = tempfile::tempdir().unwrap();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let config_path = directory.path().join("node.toml");
    let plan_path = directory.path().join("plan.json");
    let source = include_str!("../../../config/node.toml")
        .replace(
            "http://127.0.0.1:8081",
            &format!("http://{}", listener.local_addr().unwrap()),
        )
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
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock,
        "offline CLI must not connect to the actual configured local endpoint"
    );
}

/// Both CLI modes refuse malformed or unavailable input before producing a validation report.
#[test]
fn cli_source_errors_are_configuration_failures() {
    let directory = tempfile::tempdir().unwrap();
    let config = directory.path().join("invalid.toml");
    let plan = directory.path().join("plan.json");
    std::fs::write(
        &plan,
        include_str!("../../../scenarios/mission-front-half-v0.7/mission-plan.json"),
    )
    .unwrap();
    for source in [
        "inline_secret = \"do-not-echo",
        "schema = \"roboguide.node-config/v99\"",
    ] {
        std::fs::write(&config, source).unwrap();
        for with_plan in [false, true] {
            let mut command = Command::new(env!("CARGO_BIN_EXE_roboguide-node"));
            command.arg("--validate").arg(&config);
            if with_plan {
                command.arg("--plan").arg(&plan);
            }
            let output = command.output().unwrap();
            assert!(!output.status.success());
            assert!(output.stdout.is_empty());
            assert!(!String::from_utf8_lossy(&output.stderr).contains("do-not-echo"));
        }
    }
    std::fs::remove_file(&config).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
        .arg("--validate")
        .arg(&config)
        .arg("--plan")
        .arg(&plan)
        .output()
        .unwrap();
    assert!(!output.status.success() && output.stdout.is_empty());
    assert!(!directory.path().join("node-state").exists());
}

/// Legacy CLI receipts retain native Unix paths while explicit complete receipts refuse them.
#[cfg(unix)]
#[test]
fn cli_legacy_profile_accepts_non_utf8_config_directory() {
    use std::os::unix::ffi::OsStringExt;

    let root = tempfile::tempdir().unwrap();
    let directory = root
        .path()
        .join(std::ffi::OsString::from_vec(b"node-\xff".to_vec()));
    std::fs::create_dir(&directory).unwrap();
    let config = directory.join("node.toml");
    std::fs::write(
        &config,
        include_str!("../../../config/node.toml").replace("compute.noop@v1", "object.relocate@v1"),
    )
    .unwrap();
    let plan = root.path().join("plan.json");
    let mut document: Value = serde_json::from_str(include_str!(
        "../../../scenarios/mission-front-half-v0.7/mission-plan.json"
    ))
    .unwrap();
    document["tasks"][0]["roles"][0]["requirements"]["resources"] =
        json!([{"kind":"compute", "units":1}]);
    std::fs::write(&plan, document.to_string()).unwrap();
    for complete in [false, true] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_roboguide-node"));
        command
            .arg("--validate")
            .arg(&config)
            .arg("--plan")
            .arg(&plan);
        if complete {
            command.args(["--source-profile", "complete-sources/v0.1"]);
        }
        let output = command.output().unwrap();
        assert_eq!(output.status.success(), !complete);
        if complete {
            assert!(output.stdout.is_empty());
            assert!(String::from_utf8_lossy(&output.stderr).contains("UTF-8"));
        } else {
            let report: Value = serde_json::from_slice(&output.stdout).unwrap();
            assert_eq!(
                report["schema_version"],
                "roboguide.node-plan-preflight/v0.1"
            );
            assert_eq!(report["decision"], "not_blocked");
        }
    }
    assert!(!directory.join("node-state").exists());
}

/// Main's explicit cancellation observations remain bounded alongside offline validation flags.
#[test]
fn offline_preflight_preserves_explicit_cancellation_observation_options() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("node.toml");
    std::fs::write(&path, include_str!("../../../config/node.toml")).unwrap();
    for count in [1, 16, 17] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_roboguide-node"));
        for _ in 0..count {
            command.args(["--observe-unadmitted-cancellation", "original-cancellation"]);
        }
        let result = command.arg("--validate").arg(&path).output().unwrap();
        assert_eq!(
            result.status.success(),
            count <= 16,
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        if count <= 16 {
            let report: Value = serde_json::from_slice(&result.stdout).unwrap();
            assert_eq!(report["controller_contacted"], false);
        } else {
            assert!(result.stdout.is_empty());
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStringExt;
        let result = Command::new(env!("CARGO_BIN_EXE_roboguide-node"))
            .arg("--observe-unadmitted-cancellation")
            .arg(std::ffi::OsString::from_vec(b"invalid-\xff".to_vec()))
            .arg("--validate")
            .arg(&path)
            .output()
            .unwrap();
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
        assert!(
            String::from_utf8_lossy(&result.stderr)
                .contains("invalid exact cancellation observation")
        );
        assert!(!String::from_utf8_lossy(&result.stderr).contains("panicked"));
    }
    assert!(!directory.path().join("node-state").exists());
}
