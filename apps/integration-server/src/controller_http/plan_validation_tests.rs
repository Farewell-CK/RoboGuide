//! Production HTTP contract queries preserve authority and keep valid serial/concurrent plans.

use super::*;
use domain::TimestampMs;

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

/// Uses the retained rendezvous intervals to distinguish a risk from a hard admission failure.
#[tokio::test]
async fn timing_preview_replays_rendezvous_with_one_changed_completion_window() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let viewer = OperatorPrincipal {
        role: Some("viewer".into()),
        ..Default::default()
    };
    let before = server_checkpoint_json(&live.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    let ids = [
        "dog2-go",
        "dog2-shoot",
        "dog1-verify",
        "dog1-go",
        "dog1-shoot",
    ];
    let mut value = plan(&[], true, 1, ids.len());
    let source = |index: usize| format!("sha256:{}", if index == 4 { "b" } else { "a" }.repeat(64));
    let estimates: Vec<_> = ids
        .iter()
        .copied()
        .zip([19_160, 4_192, 8_060, 125_137, 2_700])
        .enumerate()
        .map(|(index, (task_id, duration_ms))| {
            serde_json::json!({
                "task_id":task_id, "duration_ms":duration_ms, "sample_count":1,
                "source_digest":source(index),
            })
        })
        .collect();
    for (index, task) in value["tasks"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        task["id"] = ids[index].into();
        task["depends_on"] = index.checked_sub(1).map_or_else(
            || serde_json::json!([]),
            |parent| serde_json::json!([ids[parent]]),
        );
        task["timing"]["completion_deadline_offset_ms"] =
            [300_000, 120_000, 180_000, 300_000, 120_000][index].into();
    }
    for (deadline, expected) in [(120_000, "estimated_risk"), (180_000, "within_estimate")] {
        value["tasks"][4]["timing"]["completion_deadline_offset_ms"] = deadline.into();
        let encoded = serde_json::to_vec(&value).unwrap();
        let input = serde_json::json!({
            "schema_version": "roboguide.plan-timing-history/v0.1",
            "plan": value,
            "estimates": estimates,
        });
        let bytes = serde_json::to_vec(&input).unwrap();
        let reply = route_with_sources(
            &live,
            &log,
            &viewer,
            &request(
                "POST",
                "/v1/missions/validate-plan?timing=history-v0.1",
                &bytes,
                None,
            ),
            None,
            None,
        )
        .await;
        assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
        let result = body(&reply);
        assert_eq!(
            result["schema_version"],
            "roboguide.plan-timing-preview/v0.1"
        );
        assert_eq!(result["assessment"]["decision"], "not_blocked");
        assert_eq!(result["assessment"]["issues"], serde_json::json!([]));
        assert_eq!(
            result["assessment"]["plan_body_sha256"],
            format!("sha256:{:x}", Sha256::digest(&encoded)),
        );
        let row = result["timing"]["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["task_id"] == "dog1-shoot")
            .unwrap();
        assert_eq!(row["status"], expected);
        assert_eq!(row["estimated_ready_ms"], 156_549);
        assert_eq!(row["estimated_completion_ms"], 159_249);
        assert_eq!(row["critical_path"], serde_json::json!(ids));
        assert_eq!(result["timing"]["estimates"][4]["source_digest"], source(4));
        assert_eq!(result["timing"]["estimates"][4]["sample_count"], 1);
    }
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
}

/// Checks exact declaration-only conflicts at admission and the inclusive equality boundary.
#[tokio::test]
async fn timing_preview_and_submission_share_declared_dependency_bounds() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let principal = OperatorPrincipal::default();
    let before = server_checkpoint_json(&live.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    let mut value = plan(&[], false, 1, 3);
    value["tasks"][0]["timing"]["earliest_start_offset_ms"] = 60.into();
    value["tasks"][1]["timing"]["earliest_start_offset_ms"] = 70.into();
    value["tasks"][2]["depends_on"] = serde_json::json!(["delivery-0", "delivery-1"]);
    for (deadline, allowed) in [(69, false), (70, true)] {
        value["tasks"][2]["timing"]["completion_deadline_offset_ms"] = deadline.into();
        let encoded = serde_json::to_vec(&value).unwrap();
        let old = route_with_sources(
            &live,
            &log,
            &principal,
            &request("POST", "/v1/missions/validate-plan", &encoded, None),
            None,
            None,
        )
        .await;
        let old = body(&old);
        assert_eq!(old["schema_version"], "roboguide.plan-validation/v0.2");
        assert_eq!(
            old["decision"],
            if allowed { "not_blocked" } else { "blocked" }
        );
        assert!(old.get("timing").is_none());
        let preview = route_with_sources(
            &live,
            &log,
            &principal,
            &request(
                "POST",
                "/v1/missions/validate-plan?timing=v0.1",
                &encoded,
                None,
            ),
            None,
            None,
        )
        .await;
        let preview = body(&preview);
        assert_eq!(preview["assessment"], old);
        let row = preview["timing"]["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["task_id"] == "delivery-2")
            .unwrap();
        assert_eq!(row["declared_ready_lower_bound_ms"], 70);
        assert_eq!(row["estimated_ready_ms"], serde_json::Value::Null);
        assert_eq!(
            row["status"],
            if allowed {
                "insufficient_estimates"
            } else {
                "declared_conflict"
            }
        );
        if !allowed {
            assert_eq!(
                row["critical_path"],
                serde_json::json!(["delivery-1", "delivery-2"])
            );
            let rejected = route_with_sources(
                &live,
                &log,
                &principal,
                &request("POST", "/v1/missions", &encoded, None),
                None,
                None,
            )
            .await;
            assert!(rejected.starts_with("HTTP/1.1 422"), "{rejected}");
            assert_eq!(body(&rejected)["error"], old["issues"][0]["message"]);
            assert_eq!(
                old["issues"][0]["path"],
                "/tasks/2/timing/completion_deadline_offset_ms"
            );
        }
    }
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
}

/// The real Mission view distinguishes a temporary shortage from an exhausted scheduling window.
#[tokio::test]
async fn mission_detail_reports_the_original_scheduling_deferral() {
    for (has_node, reason, allows_retry) in [
        (false, "no-candidate", true),
        (true, "window-missed", false),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let mut log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let live = controller(&log);
        let mut document = plan(&[], false, 1, 1);
        document["tasks"][0]["timing"] = serde_json::json!({
            "earliest_start_offset_ms": 0,
            "latest_start_offset_ms": 50,
            "completion_deadline_offset_ms": null,
        });
        let operation = serde_json::json!({"namespace":"compute", "name":"work", "version":"v1"});
        let role = &mut document["tasks"][0]["roles"][0];
        role["requirements"] = serde_json::json!({
            "capabilities":[{"contract":operation, "constraints":[]}], "resources":[]
        });
        role["execution_intent"]["operation"] = operation;
        let plan = decode_mission_plan(&document.to_string()).unwrap();
        let mission = plan.goal().mission_id().clone();
        let task = plan.task_graph().tasks()[0]
            .requirement()
            .task_ref()
            .clone();
        let correlation = domain::CorrelationId::new("timing-deferral").unwrap();
        {
            let mut controller = live.lock().unwrap();
            let ControllerState {
                bridge,
                orchestrator,
                ..
            } = &mut *controller;
            let mut node_state = bridge.state().clone();
            if has_node {
                bridge
                    .control_mut()
                    .register_node(
                        &mut node_state,
                        crate::tests::recovery_driver_node(
                            "timer-test-node",
                            "timer-test-resource",
                        ),
                        domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(0)),
                        TimestampMs::new(0),
                        &correlation,
                        &mut log,
                    )
                    .unwrap();
            }
            orchestrator
                .submit(
                    plan,
                    domain::ExecutionGroupId::new("timing-view-group").unwrap(),
                    bridge.control_mut(),
                    TimestampMs::new(0),
                    &correlation,
                    &mut log,
                )
                .unwrap();
            assert!(
                orchestrator
                    .prepare_task(
                        &mission,
                        &task,
                        &node_state,
                        bridge.control_mut(),
                        TimestampMs::new(if has_node { 51 } else { 25 }),
                        &correlation,
                        &mut log,
                    )
                    .is_err()
            );
        }
        let reply = route(
            &live,
            &log,
            &OperatorPrincipal::default(),
            &request("GET", &format!("/v1/missions/{mission}"), b"", None),
        )
        .await;
        assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
        let row = &body(&reply)["tasks"][0];
        assert_eq!(row["status"], "Ready");
        assert_eq!(
            row["scheduling_deferral"],
            serde_json::json!({"reason":reason, "allows_retry":allows_retry}),
        );
    }
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
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let path = directory.path().join("private-node.toml");
    let source = include_str!("../../../../config/node.toml")
        .replace("http://127.0.0.1:8081", &format!("http://{}",listener.local_addr().unwrap()))
        .replace("generic-node", "private-node-identity")
        .replace("compute.noop@v1", "object.relocate@v1")
        .replace(
            "kind = \"pointer\"\npointer = \"/invocation\"",
            "kind = \"function\"\nfunction = \"to_integer\"\narguments = [{ kind = \"pointer\", pointer = \"/invocation/parameters/destination\" }]",
        );
    std::fs::write(&path, source).unwrap();
    let preflight = node_service::NodePlanPreflight::load(std::slice::from_ref(&path)).unwrap();
    let cli_preflight =
        node_service::NodePlanPreflight::load_conformant(std::slice::from_ref(&path)).unwrap();
    std::fs::remove_file(&path).unwrap(); // Every subsequent request must use the frozen catalog.
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
        let decoded =
            orchestration::decode_mission_plan(std::str::from_utf8(&encoded).unwrap()).unwrap();
        let offline = cli_preflight.assess(&decoded, &encoded);
        assert_eq!(result["decision"], offline["decision"]);
        assert_eq!(result["issues"], offline["issues"]);
        assert_eq!(result["plan_body_sha256"], offline["plan_body_sha256"]);
        assert_eq!(
            result["node_preflight"]["source_digest"],
            offline["source_digest"]
        );
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
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
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

/// Complete source negotiation uses v0.4 and never changes the original checkpoint or event log.
#[tokio::test]
async fn complete_private_source_profile_has_distinct_http_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("node.toml");
    std::fs::write(&path, include_str!("../../../../config/node.toml")).unwrap();
    let preflight = node_service::NodePlanPreflight::load_complete(&[path], false).unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let live = controller(&log);
    let before = server_checkpoint_json(&live.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    let encoded = serde_json::to_vec(&plan(&[], false, 1, 1)).unwrap();
    let reply = route_with_node_preflight(
        &live,
        &log,
        &OperatorPrincipal::default(),
        &request("POST", "/v1/missions/validate-plan", &encoded, None),
        None,
        None,
        Some(&preflight),
    )
    .await;
    assert!(reply.starts_with("HTTP/1.1 200"), "{reply}");
    let report = body(&reply);
    assert_eq!(report["schema_version"], "roboguide.plan-validation/v0.4");
    assert_eq!(
        report["node_preflight"]["scope"],
        "declared-node-complete-sources"
    );
    assert_eq!(
        report["node_preflight"]["source_digest"],
        preflight.source_digest()
    );
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
    assert_eq!(log.control_command_count().unwrap(), 0);
}
