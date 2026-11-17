//! Production HTTP contract queries preserve authority and keep valid serial/concurrent plans.

use super::*;

/// Builds neutral delivery slots with explicit Actor reuse and evidence publication dependencies.
fn plan(final_tasks: &[usize], ordered: bool, actors: usize, count: usize) -> serde_json::Value {
    let mut plan: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../scenarios/mission-front-half-v0.7/mission-plan.json"
    ))
    .unwrap();
    let template = plan["tasks"][0].clone();
    plan["mission"]["actors"] = serde_json::json!(
        (0..actors)
            .map(|index| serde_json::json!({"id":format!("courier-{index}")}))
            .collect::<Vec<_>>()
    );
    plan["contexts"] = serde_json::json!((0..actors).map(|index| serde_json::json!({
        "id":format!("context-{index}"), "roles":[{"id":"carrier","actor":format!("courier-{index}")}],
        "relations":[],"coupling_mode":"independent"
    })).collect::<Vec<_>>());
    plan["tasks"] = serde_json::json!(
        (0..count)
            .map(|index| {
                let mut task = template.clone();
                task["id"] = format!("delivery-{index}").into();
                task["context_id"] = format!("context-{}", index % actors).into();
                task["depends_on"] = if ordered && index > 0 {
                    serde_json::json!([format!("delivery-{}", index - 1)])
                } else {
                    serde_json::json!([])
                };
                if !final_tasks.contains(&index) {
                    task["satisfaction"]["basis"] = "execution-report".into();
                    task["satisfaction"]["verifier"] = serde_json::Value::Null;
                }
                task
            })
            .collect::<Vec<_>>()
    );
    plan
}

/// Loads a digest-checked terminal feed without creating a verdict or simulator world.
fn feed(directory: &tempfile::TempDir) -> TaskVerifierFeed {
    let mut source = serde_json::json!({
        "schema_version":"roboguide.task-verifier-source/v0.1","source_id":"independent-final-source",
        "source_revision":format!("sha256:{}","a".repeat(64)),
        "identity":{"run_id":"run","episode_id":"workload","scene_id":"world","dataset_revision":"revision","dataset_sha256":"b".repeat(64)},
        "verifier":{"namespace":"observation","name":"verify","version":"v1"},
        "supported_predicates":["object first-aid-kit-2f is in reception-1f"],"verdict_finality":"terminal"
    });
    let digest = format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&source).unwrap())
    );
    source["digest"] = digest.into();
    let path = directory.path().join("source.json");
    std::fs::write(&path, source.to_string()).unwrap();
    TaskVerifierFeed::load(&path, directory.path().join("verdict.json")).unwrap()
}

/// Decodes only an actual handler response, never a synthetic accepted-plan receipt.
fn body(response: &str) -> serde_json::Value {
    serde_json::from_str(response.split_once("\r\n\r\n").unwrap().1).unwrap()
}

/// Queries cannot write checkpoints/events, and serial reuse remains distinct from final-world waiting.
#[tokio::test]
async fn published_plan_queries_share_submission_rejections_without_effects() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let feed = feed(&directory);
    let viewer = OperatorPrincipal {
        role: Some("viewer".into()),
        ..Default::default()
    };
    let before = server_checkpoint_json(&live.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    for (final_tasks, ordered, actors, count, allowed) in [
        (vec![], false, 1, 2, true),
        (vec![], true, 1, 3, true),
        (vec![1], true, 1, 2, true),
        (vec![2], true, 1, 3, true),
        (vec![0, 1], false, 2, 2, true),
        (vec![0, 1], false, 1, 2, false),
        (vec![0, 1], true, 1, 2, false),
        (vec![0], false, 1, 2, false),
        (vec![0], true, 2, 2, false),
    ] {
        let value = plan(&final_tasks, ordered, actors, count);
        let encoded = serde_json::to_vec(&value).unwrap();
        let reply = route_with_sources(
            &live,
            &log,
            &viewer,
            &request("POST", "/v1/missions/validate-plan", &encoded, None),
            None,
            Some(&feed),
        )
        .await;
        assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
        let result = body(&reply);
        assert_eq!(
            result["decision"],
            if allowed { "not_blocked" } else { "blocked" },
            "{result}"
        );
        assert_eq!(
            result["plan_body_sha256"],
            format!("sha256:{:x}", Sha256::digest(&encoded))
        );
        assert_eq!(result["verifier_source_digest"], feed.source_digest());
        assert!(!reply.contains("node_id") && !reply.contains("resource_id"));
        if !allowed {
            let rejected = route_with_sources(
                &live,
                &log,
                &OperatorPrincipal::default(),
                &request("POST", "/v1/missions", &encoded, None),
                None,
                Some(&feed),
            )
            .await;
            assert!(rejected.starts_with("HTTP/1.1 422"), "{rejected}");
            assert_eq!(body(&rejected)["error"], result["issues"][0]["message"]);
        }
    }
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
}

/// An unused Actor declaration cannot hide reuse of another Actor's retained endpoint.
#[tokio::test]
async fn published_plan_queries_check_actual_actor_slots_not_declared_actor_count() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let feed = feed(&directory);
    let mut value = plan(&[0], false, 2, 2);
    value["contexts"][1]["roles"][0]["actor"] = "courier-0".into();
    let encoded = serde_json::to_vec(&value).unwrap();
    let reply = route_with_sources(
        &live,
        &log,
        &OperatorPrincipal::default(),
        &request("POST", "/v1/missions/validate-plan", &encoded, None),
        None,
        Some(&feed),
    )
    .await;
    assert_eq!(
        body(&reply)["issues"][0]["code"],
        "verifier.blocks_actor_reuse"
    );
    value["tasks"][0]["satisfaction"]["basis"] = "execution-report".into();
    value["tasks"][0]["satisfaction"]["verifier"] = serde_json::Value::Null;
    value["tasks"][1]["satisfaction"] = plan(&[1], true, 2, 2)["tasks"][1]["satisfaction"].clone();
    value["tasks"][1]["depends_on"] = serde_json::json!(["delivery-0"]);
    let encoded = serde_json::to_vec(&value).unwrap();
    let reply = route_with_sources(
        &live,
        &log,
        &OperatorPrincipal::default(),
        &request("POST", "/v1/missions/validate-plan", &encoded, None),
        None,
        Some(&feed),
    )
    .await;
    assert_eq!(body(&reply)["decision"], "not_blocked");
}

/// Absent sources, invalid plans and excess diagnostics never look like successful admission.
#[tokio::test]
async fn published_plan_queries_keep_unknown_and_diagnostic_bounds_explicit() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let principal = OperatorPrincipal::default();
    let missing = route(
        &live,
        &log,
        &principal,
        &request(
            "POST",
            "/v1/missions/validate-plan",
            &serde_json::to_vec(&plan(&[0], false, 1, 1)).unwrap(),
            None,
        ),
    )
    .await;
    assert_eq!(
        body(&missing)["issues"][0]["code"],
        "verifier.source_unavailable"
    );
    let malformed = route(
        &live,
        &log,
        &principal,
        &request("POST", "/v1/missions/validate-plan", b"{}", None),
    )
    .await;
    assert!(malformed.starts_with("HTTP/1.1 400"));
    let feed = feed(&directory);
    let all = (0..40).collect::<Vec<_>>();
    let reply = route_with_sources(
        &live,
        &log,
        &principal,
        &request(
            "POST",
            "/v1/missions/validate-plan",
            &serde_json::to_vec(&plan(&all, false, 1, 40)).unwrap(),
            None,
        ),
        None,
        Some(&feed),
    )
    .await;
    let result = body(&reply);
    assert_eq!(result["issues"].as_array().unwrap().len(), 32);
    assert_eq!(result["issues_truncated"], true);
    assert!(live.lock().unwrap().orchestrator.mission_ids().is_empty());
}

/// Private declarations add only source-bound diagnostics; queries never create live Node facts.
#[tokio::test]
async fn private_node_preflight_rejects_mismatches_without_inventory_or_writes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("private-node.toml");
    let source = include_str!("../../../../config/node.toml")
        .replace("generic-node", "private-node-identity")
        .replace("compute.noop@v1", "object.relocate@v1")
        .replace(
            "kind = \"pointer\"\npointer = \"/invocation\"",
            "kind = \"function\"\nfunction = \"to_integer\"\narguments = [{ kind = \"pointer\", pointer = \"/invocation/parameters/destination\" }]",
        );
    std::fs::write(&path, source).unwrap();
    let preflight = node_service::NodePlanPreflight::load(&[path]).unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let before = server_checkpoint_json(&live.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    let viewer = OperatorPrincipal {
        role: Some("viewer".into()),
        ..Default::default()
    };
    let mut value = plan(&[], false, 1, 1);
    for (resources, parameter, expected) in [
        (
            serde_json::json!([]),
            Some("5"),
            Some("node_preflight.resources_incompatible"),
        ),
        (
            serde_json::json!([{"kind":"compute","units":1}]),
            None,
            Some("node_preflight.parameters_incompatible"),
        ),
        (
            serde_json::json!([{"kind":"compute","units":1}]),
            Some("not-a-number"),
            Some("node_preflight.parameters_incompatible"),
        ),
        (
            serde_json::json!([{"kind":"compute","units":1}]),
            Some("5"),
            None,
        ),
    ] {
        value["tasks"][0]["roles"][0]["requirements"]["resources"] = resources;
        value["tasks"][0]["roles"][0]["execution_intent"]["parameters"] =
            if let Some(parameter) = parameter {
                serde_json::json!({"destination":parameter})
            } else {
                serde_json::json!({"destinaton":"reception-1f"})
            };
        let encoded = serde_json::to_vec(&value).unwrap();
        let reply = route_with_node_preflight(
            &live,
            &log,
            &viewer,
            &request("POST", "/v1/missions/validate-plan", &encoded, None),
            None,
            None,
            Some(&preflight),
        )
        .await;
        assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
        let result = body(&reply);
        assert_eq!(result["schema_version"], "roboguide.plan-validation/v0.3");
        assert_eq!(
            result["node_preflight"]["source_digest"],
            preflight.source_digest()
        );
        assert_eq!(
            result["decision"],
            if expected.is_some() {
                "blocked"
            } else {
                "not_blocked"
            }
        );
        if let Some(code) = expected {
            assert_eq!(result["issues"][0]["code"], code);
        }
        for private in [
            "private-node-identity",
            "compute-slot",
            "local-runtime",
            "127.0.0.1:8081",
        ] {
            assert!(!reply.contains(private), "{reply}");
        }
    }
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
    assert_eq!(log.control_command_count().unwrap(), 0);
    assert!(!directory.path().join("node-state").exists());
    assert!(!directory.path().join("artifact-cache").exists());
    let legacy = route(
        &live,
        &log,
        &viewer,
        &request(
            "POST",
            "/v1/missions/validate-plan",
            &serde_json::to_vec(&value).unwrap(),
            None,
        ),
    )
    .await;
    assert_eq!(
        body(&legacy)["schema_version"],
        "roboguide.plan-validation/v0.2"
    );
    assert!(body(&legacy).get("node_preflight").is_none());
}
