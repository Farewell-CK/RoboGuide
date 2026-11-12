//! Restricted verifier transport authority cannot become a general operator or leak other tenants.

use super::*;

/// Defines the transport facts produced by a scoped verifier certificate, without any action grant.
fn verifier() -> OperatorPrincipal {
    OperatorPrincipal {
        mtls: true,
        subject: Some("posture-verifier".into()),
        role: Some("verifier".into()),
        tenants: Some(BTreeSet::from(["lab".into()])),
        ..Default::default()
    }
}

/// Checks the real central route gate including exact method, role hint and certificate scope.
#[test]
fn verifier_is_an_allowlist_not_an_operator_tier() {
    let request = ControlHttpRequest {
        method: "GET".into(),
        target: "/".into(),
        headers: BTreeMap::new(),
        body: Vec::new(),
    };
    let principal = verifier();
    for path in [
        "/healthz",
        "/readyz",
        "/v1/inventory",
        "/v1/state/records",
        "/v1/missions/task",
        "/v1/missions/task/tenant",
        "/v1/missions/task/execution-attempts",
        "/v1/missions/task/admission",
    ] {
        assert!(
            authorize_operator_request(&request, &principal, "GET", path).is_ok(),
            "{path}"
        );
    }
    assert!(
        authorize_operator_request(&request, &principal, "POST", "/v1/verifier-evidence").is_ok()
    );
    for (method, path) in [
        ("POST", "/v1/missions"),
        ("POST", "/v1/missions/task/cancel"),
        ("POST", "/v1/executions/task/cancel"),
        ("POST", "/v1/verifier-sources"),
        ("POST", "/v1/mission-goal-evidence"),
        ("POST", "/v1/authority/recover"),
        ("GET", "/v1/events"),
        ("GET", "/v1/resource-allocations"),
        ("GET", "/v1/missions/task/plan"),
        ("GET", "/v1/missions/task/status/extra"),
        ("POST", "/v1/state/records"),
    ] {
        assert!(
            authorize_operator_request(&request, &principal, method, path).is_err(),
            "{method} {path}"
        );
    }
    let mut hinted = ControlHttpRequest {
        method: "POST".into(),
        target: "/v1/missions".into(),
        headers: BTreeMap::new(),
        body: Vec::new(),
    };
    hinted
        .headers
        .insert("x-roboguide-operator-role".into(), "operator".into());
    assert!(authorize_operator_request(&hinted, &principal, "POST", "/v1/missions").is_err());
    let mut invalid = principal.clone();
    invalid.mtls = false;
    assert!(authorize_operator_request(&request, &invalid, "GET", "/v1/inventory").is_err());
    let mut invalid = principal.clone();
    invalid.tenants = Some(BTreeSet::from(["*".into()]));
    assert!(
        authorize_operator_request(&request, &invalid, "POST", "/v1/verifier-evidence").is_err()
    );
    let mut invalid = principal;
    invalid.subject = None;
    assert!(
        authorize_operator_request(&request, &invalid, "POST", "/v1/verifier-evidence").is_err()
    );
}

/// Handler denial happens before any admission body, task ownership or physical dispatch can mutate.
#[tokio::test]
async fn verifier_cannot_admit_actions_through_production_handler() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let principal = verifier();
    for path in [
        "/v1/missions",
        "/v1/missions/unknown/cancel",
        "/v1/authority/recover",
    ] {
        let response = route(
            &controller,
            &log,
            &principal,
            &request("POST", path, b"{}", None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 403"), "{path}: {response}");
    }
    assert!(log.events().unwrap().is_empty());
    assert_eq!(log.control_command_count().unwrap(), 0);
}
