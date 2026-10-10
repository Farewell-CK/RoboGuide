//! Exact canonical relocation admission; navigation evidence never proves a manipulation route.

use super::deployment_feasibility::{valid_prefixed_sha256, valid_sha256};
use std::collections::{BTreeMap, BTreeSet};

/// Immutable configured endpoints and reset sources, with no reservation or reachability claim.
#[derive(Debug, Clone)]
pub(super) struct DeploymentOperationAdmission {
    /// Exact initial source associated with each observed object.
    object_sources: BTreeMap<String, String>,
    /// Only observed, typed destination entities may be named by the model.
    destinations: BTreeSet<String>,
    /// Startup coverage narrows Control candidates but does not select an executor.
    endpoints: BTreeSet<domain::NodeId>,
}

impl DeploymentOperationAdmission {
    /// Validate the bounded neutral projection against the matrix's exact Node source coverage.
    pub(super) fn from_json(
        document: &serde_json::Value,
        node_agents: &BTreeMap<domain::NodeId, i64>,
        node_sources: &BTreeMap<domain::NodeId, String>,
        allow_subset: bool,
    ) -> Result<Self, String> {
        let body = document
            .as_object()
            .ok_or("operation admission must be an object")?;
        if body.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from([
                "schema_version",
                "operation",
                "parameter_names",
                "source_basis",
                "source_snapshot_digest",
                "registration_profile_digest",
                "object_sources",
                "destination_entities",
                "endpoint_profiles",
                "route_reachability",
            ])
            || body["schema_version"] != "roboguide.deployment-operation-admission/v0.1"
            || body["operation"] != "object.relocate@v1"
            || body["parameter_names"] != serde_json::json!(["destination", "object", "source"])
            || body["source_basis"] != "observed-initial-location"
            || body["route_reachability"] != "unknown"
            || ["source_snapshot_digest", "registration_profile_digest"]
                .iter()
                .any(|key| !valid_prefixed_sha256(body[*key].as_str().unwrap_or_default()))
        {
            return Err("operation admission schema, source or route claim is invalid".into());
        }
        let sources = body["object_sources"]
            .as_object()
            .ok_or("operation admission lacks object sources")?;
        let mut object_sources = BTreeMap::new();
        let mut source_ids = BTreeSet::new();
        if sources.is_empty() || sources.len() > 64 {
            return Err("operation admission object source coverage is invalid".into());
        }
        for (object, value) in sources {
            let source = value.as_str().unwrap_or_default();
            if object.trim().is_empty()
                || !source
                    .strip_prefix("initial-location:")
                    .is_some_and(valid_sha256)
                || !source_ids.insert(source.to_owned())
            {
                return Err(
                    "operation admission source must identify one observed initial location".into(),
                );
            }
            object_sources.insert(object.clone(), source.to_owned());
        }
        let entities = body["destination_entities"]
            .as_array()
            .ok_or("operation admission lacks destination entities")?;
        let mut destinations = BTreeSet::new();
        if entities.is_empty() || entities.len() > 64 {
            return Err("operation admission destination coverage is invalid".into());
        }
        for value in entities {
            let destination = value.as_str().unwrap_or_default();
            if destination.trim().is_empty()
                || object_sources.contains_key(destination)
                || !destinations.insert(destination.to_owned())
            {
                return Err("operation admission destination entity is invalid".into());
            }
        }
        let profiles = body["endpoint_profiles"]
            .as_array()
            .ok_or("operation admission lacks endpoint profiles")?;
        let mut endpoints = BTreeSet::new();
        let max_endpoints = if allow_subset { 4 } else { 2 };
        if profiles.is_empty() || profiles.len() > max_endpoints {
            return Err("operation admission endpoint coverage is invalid".into());
        }
        for profile in profiles {
            let profile = profile
                .as_object()
                .ok_or("operation admission endpoint must be an object")?;
            if profile.keys().map(String::as_str).collect::<BTreeSet<_>>()
                != BTreeSet::from([
                    "agent_id",
                    "node_id",
                    "node_config_digest",
                    "resource_kind",
                    "resource_capacity",
                ])
                || profile["resource_kind"] != "space"
                || profile["resource_capacity"] != serde_json::json!(1)
            {
                return Err("operation admission needs a capacity-one space endpoint".into());
            }
            let node = domain::NodeId::new(profile["node_id"].as_str().unwrap_or_default())
                .map_err(|error| error.to_string())?;
            if profile["agent_id"].as_i64() != node_agents.get(&node).copied()
                || profile["node_config_digest"].as_str()
                    != node_sources.get(&node).map(String::as_str)
                || !endpoints.insert(node)
            {
                return Err(
                    "operation admission endpoint differs from the navigation Node source".into(),
                );
            }
        }
        if !allow_subset && endpoints != node_agents.keys().cloned().collect() {
            return Err("operation admission endpoint coverage is incomplete".into());
        }
        Ok(Self {
            object_sources,
            destinations,
            endpoints,
        })
    }

    /// Validate all three canonical parameters without rewriting the model's selected object.
    pub(super) fn candidates(
        &self,
        intent: &domain::ExecutionIntent,
    ) -> Result<BTreeSet<domain::NodeId>, String> {
        let parameters = intent.parameters();
        if intent.operation().to_string() != "object.relocate@v1"
            || parameters
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
                != BTreeSet::from(["destination", "object", "source"])
        {
            return Err(
                "relocation admission requires exact object/source/destination parameters".into(),
            );
        }
        let text = |key| match parameters.get(key) {
            Some(domain::ExecutionValue::String(value)) if !value.trim().is_empty() => Ok(value),
            _ => Err(
                "relocation admission parameters must be nonblank entity references".to_string(),
            ),
        };
        let object = text("object")?;
        let source = text("source")?;
        let destination = text("destination")?;
        if self.object_sources.get(object) != Some(source) {
            return Err(
                "relocation admission source differs from this reset's exact object location"
                    .into(),
            );
        }
        if !self.destinations.contains(destination) {
            return Err(
                "relocation admission destination is not an observed destination entity".into(),
            );
        }
        Ok(self.endpoints.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::super::deployment_feasibility::{DeploymentFeasibility, content_digest};
    use super::DeploymentOperationAdmission;
    use crate::*;
    use domain::{NodeId, TimestampMs};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Use synthetic Python-produced evidence, never an archived benchmark outcome.
    fn snapshot() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../../integrations/habitat-local-eaios/tests/fixtures/deployment-operation-admission.json")).unwrap()
    }

    /// Preserve portable float hashing when testing a deliberately resealed invalid source.
    fn seal(document: &mut serde_json::Value) {
        document.as_object_mut().unwrap().remove("digest");
        document["digest"] = content_digest(document).unwrap().into();
    }

    /// Exercise the production file reader, including complete root and nested validation.
    fn load(document: &serde_json::Value) -> Result<DeploymentFeasibility, String> {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("admission.json");
        std::fs::write(&path, document.to_string()).unwrap();
        DeploymentFeasibility::load(&path)
    }

    /// Build generic distinct-object tasks with either parallel Actors or sequential reuse.
    fn plan(sequential: bool) -> serde_json::Value {
        let source = snapshot();
        let operation = serde_json::json!({"namespace":"object","name":"relocate","version":"v1"});
        serde_json::json!({
            "schema_version":"roboguide.mission-plan/v0.8",
            "mission":{"id":"relocation-test","objective":"relocate two objects", "actors":
                (0..if sequential {1} else {2}).map(|i|serde_json::json!({"id":format!("actor-{i}")})).collect::<Vec<_>>()},
            "contexts":[{"id":"context", "coupling_mode":"independent", "relations":[], "executor_constraints":[],
                "roles":(0..2).map(|i|serde_json::json!({"id":format!("participant-{i}"),"actor":format!("actor-{}",if sequential {0} else {i})})).collect::<Vec<_>>() }],
            "tasks":(0..2).map(|i|serde_json::json!({
                "id":format!("task-{i}"), "description":"relocate the exact assigned object", "context_id":"context",
                "coupling_mode":"independent", "depends_on":if sequential && i==1 {vec!["task-0"]} else {Vec::new()},
                "timing":{"earliest_start_offset_ms":0,"latest_start_offset_ms":null,"completion_deadline_offset_ms":null},
                "satisfaction":{"expected_effect":"object relocated", "basis":"execution-report", "verifier":null},
                "roles":[{"id":"worker", "context_role":format!("participant-{i}"), "resource_scope":"task",
                    "requirements":{"capabilities":[{"contract":operation,"constraints":[]}],"resources":[{"kind":"space","units":1}]},
                    "execution_intent":{"operation":operation,"objective":"relocate the exact assigned object", "parameters":{
                        "object":format!("object:{i}"),"source":source["operation_admission"]["object_sources"][format!("object:{i}")],
                        "destination":format!("destination:{i}")}}}]
            })).collect::<Vec<_>>()
        })
    }

    /// Declare a real modern operation and capacity independently of the startup evidence.
    fn registration(suffix: &str, supported: bool, capacity: u32) -> domain::NodeRegistration {
        let owner = domain::LocalSystemId::new("synthetic-local-system").unwrap();
        let operation = domain::OperationRef::new("object", "relocate", "v1").unwrap();
        let resource = domain::Resource::new(
            domain::ResourceId::new(format!("slot-{suffix}")).unwrap(),
            domain::ResourceKind::Space,
            capacity,
        )
        .unwrap();
        let registration = domain::NodeRegistration::new_with_local_systems(
            NodeId::new(format!("node-{suffix}")).unwrap(),
            vec![domain::LocalSystemDescriptor::new(
                owner.clone(),
                domain::LocalRuntime::new("synthetic", "1").unwrap(),
                BTreeMap::from([(
                    integration::EXECUTION_SESSION_METADATA_KEY.into(),
                    integration::EXECUTION_SESSION_METADATA_VALUE.into(),
                )]),
            )],
            domain::NodeContractVersion::v0_6(),
            vec![domain::Capability::new(
                domain::CapabilityKind::Transport,
                true,
            )],
            BTreeMap::from([(operation.as_legacy_contract().clone(), owner.clone())]),
            Vec::new(),
            vec![resource.clone()],
            BTreeMap::from([(resource.id().clone(), owner.clone())]),
        )
        .unwrap();
        registration
            .with_operation_support(if supported {
                vec![domain::OperationSupport::new(operation, owner)]
            } else {
                Vec::new()
            })
            .unwrap()
    }

    /// Own one isolated actual Controller and event log without running any external system.
    struct Fixture {
        /// Keep all synthetic durable files alive until the test ends.
        _directory: tempfile::TempDir,
        /// Production authority and reducer under test.
        controller: Arc<Mutex<ControllerState>>,
        /// Real durable event log, not a mocked admission result.
        events: state::SqliteEventLog,
    }

    impl Fixture {
        /// Register current capabilities/resources and a separate physical-entity registry.
        fn new(second_supported: bool) -> Self {
            let directory = tempfile::tempdir().unwrap();
            let events =
                state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
            let mut control = control::ControlPlane::new();
            let mut state = state::InMemorySharedNodeState::new();
            let correlation = domain::CorrelationId::new("synthetic-relocation").unwrap();
            for (suffix, supported) in [("a", true), ("b", second_supported)] {
                control
                    .register_node(
                        &mut state,
                        registration(suffix, supported, 1),
                        domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
                        TimestampMs::new(1),
                        &correlation,
                        &mut events.clone(),
                    )
                    .unwrap();
            }
            control
                .install_physical_entity_registry(
                    domain::PhysicalEntityRegistrySnapshot::new(
                        domain::PhysicalEntityRegistryId::new("synthetic-deployment").unwrap(),
                        1,
                        domain::PhysicalEntityRoutingProfile::OneRoutableEntityPerNode,
                        ["a", "b"]
                            .into_iter()
                            .map(|suffix| {
                                domain::PhysicalEntityRegistration::new(
                                    domain::PhysicalEntityId::new(format!("entity-{suffix}"))
                                        .unwrap(),
                                    NodeId::new(format!("node-{suffix}")).unwrap(),
                                )
                            })
                            .collect(),
                    )
                    .unwrap(),
                )
                .unwrap();
            Self {
                _directory: directory,
                controller: Arc::new(Mutex::new(ControllerState {
                    bridge: IntegrationRuntimeBridge::new(
                        control,
                        state,
                        events.clone(),
                        integration::GrpcNodeRouter::default(),
                    ),
                    orchestrator: MissionOrchestrator::new(),
                    mission_admissions: BTreeMap::new(),
                    verifier_seen: BTreeSet::new(),
                    verifier_source_digest: None,
                })),
                events,
            }
        }

        /// POST over a real TCP connection through the unchanged Controller HTTP handler.
        async fn submit(
            &self,
            document: &serde_json::Value,
            source: &DeploymentFeasibility,
        ) -> String {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let gate = Arc::new(Mutex::new(()));
            let clock = runtime::FixedClock::new(TimestampMs::new(2));
            let body = document.to_string();
            let request = format!(
                "POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let server = async {
                let (mut stream, _) = listener.accept().await.unwrap();
                handle_http_connection(
                    &mut stream,
                    &self.controller,
                    &self.events,
                    &gate,
                    &clock,
                    Some(source),
                    None,
                )
                .await
                .unwrap();
            };
            let client = async {
                let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
                stream.write_all(request.as_bytes()).await.unwrap();
                let mut response = String::new();
                stream.read_to_string(&mut response).await.unwrap();
                response
            };
            tokio::time::timeout(Duration::from_secs(5), async {
                tokio::join!(server, client)
            })
            .await
            .unwrap()
            .1
        }

        /// Apply real ordered terminal facts; they remain synthetic local execution reports.
        fn fact(
            &self,
            attempt: &runtime::ExecutionAttemptSnapshot,
            sequence: u64,
            phase: integration::grpc::v0_4::ExecutionPhase,
            now: u64,
        ) {
            let mut controller = self.controller.lock().unwrap();
            let correlation = domain::CorrelationId::new("synthetic-relocation").unwrap();
            controller
                .bridge
                .consume(
                    integration::GrpcNodeEvent::NodeMessage {
                        node_id: attempt.command().node_id().to_string(),
                        session_id: "synthetic-session".into(),
                        message: integration::grpc::v0_4::NodeMessage {
                            message: Some(
                                integration::grpc::v0_4::node_message::Message::ExecutionSnapshot(
                                    integration::grpc::v0_4::ExecutionSnapshot {
                                        session_id: "synthetic-session".into(),
                                        execution_id: attempt.execution_id().into(),
                                        last_sequence: sequence,
                                        phase: phase as i32,
                                        reason: "synthetic observed local outcome".into(),
                                    },
                                ),
                            ),
                        },
                    },
                    TimestampMs::new(now),
                    &correlation,
                )
                .unwrap();
            apply_runtime_events(
                &mut controller,
                TimestampMs::new(now),
                &correlation,
                &mut self.events.clone(),
            )
            .unwrap();
            apply_runtime_outcomes(
                &mut controller,
                TimestampMs::new(now),
                &correlation,
                &mut self.events.clone(),
            )
            .unwrap();
            drive_ready_tasks(
                &mut controller,
                TimestampMs::new(now),
                &correlation,
                &mut self.events.clone(),
            )
            .unwrap();
        }
    }

    /// Two distinct-object assignments survive navigation exclusions and use actual exclusive slots.
    #[tokio::test]
    async fn http_relocation_uses_operation_sources_and_control_capacity() {
        let source = load(&snapshot()).unwrap();
        let fixture = Fixture::new(true);
        let response = fixture.submit(&plan(false), &source).await;
        assert!(response.starts_with("HTTP/1.1 202"), "{response}");
        let controller = fixture.controller.lock().unwrap();
        let attempts = controller.bridge.attempt_history();
        assert_eq!(attempts.len(), 2);
        assert_eq!(
            attempts
                .iter()
                .map(|a| a.command().node_id().as_str())
                .collect::<BTreeSet<_>>(),
            BTreeSet::from(["node-a", "node-b"])
        );
        assert_eq!(
            attempts
                .iter()
                .flat_map(|a| controller
                    .bridge
                    .control()
                    .group(a.command().group_id())
                    .unwrap()
                    .task_execution(a.command().task_ref())
                    .unwrap()
                    .assignments()
                    .iter()
                    .flat_map(|assignment| assignment.resource_ids().iter().cloned()))
                .map(|id| id.to_string())
                .collect::<BTreeSet<_>>(),
            BTreeSet::from(["slot-a".to_owned(), "slot-b".to_owned()])
        );
        assert!(attempts.iter().all(|a| a.command().session().is_some()));
    }

    /// Control release and DAG advancement permit one Actor to reuse the same endpoint.
    #[tokio::test]
    async fn http_single_actor_reuses_endpoint_only_after_observed_completion() {
        let source = load(&snapshot()).unwrap();
        let fixture = Fixture::new(true);
        let response = fixture.submit(&plan(true), &source).await;
        assert!(response.starts_with("HTTP/1.1 202"), "{response}");
        let first = fixture.controller.lock().unwrap().bridge.attempt_history();
        assert_eq!(first.len(), 1);
        fixture.fact(
            &first[0],
            1,
            integration::grpc::v0_4::ExecutionPhase::Started,
            3,
        );
        assert_eq!(
            fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .attempt_history()
                .len(),
            1
        );
        fixture.fact(
            &first[0],
            2,
            integration::grpc::v0_4::ExecutionPhase::Completed,
            4,
        );
        let attempts = fixture.controller.lock().unwrap().bridge.attempt_history();
        assert_eq!(attempts.len(), 2);
        assert_eq!(
            attempts[0].command().node_id(),
            attempts[1].command().node_id()
        );
        let controller = fixture.controller.lock().unwrap();
        assert_eq!(
            controller
                .bridge
                .control()
                .group(attempts[1].command().group_id())
                .unwrap()
                .task_execution(attempts[1].command().task_ref())
                .unwrap()
                .assignments()[0]
                .resource_ids(),
            vec![
                domain::ResourceId::new(format!(
                    "slot-{}",
                    attempts[0]
                        .command()
                        .node_id()
                        .as_str()
                        .strip_prefix("node-")
                        .unwrap()
                ))
                .unwrap()
            ]
        );
    }

    /// Startup endpoints do not grant operation support missing from current Node registration.
    #[tokio::test]
    async fn http_current_operation_shortage_is_durable_and_never_fakes_success() {
        let fixture = Fixture::new(false);
        let source = load(&snapshot()).unwrap();
        let response = fixture.submit(&plan(false), &source).await;
        assert!(response.starts_with("HTTP/1.1 202"), "{response}");
        assert_eq!(
            fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .attempt_history()
                .len(),
            1
        );
        let events = format!("{:?}", fixture.events.decoded_events().unwrap());
        assert!(events.contains("TaskSchedulingDeferred"), "{events}");
        assert!(!events.contains("TaskSatisfied"));
        assert!(!events.contains("MissionCompleted"));
    }

    /// Wrong canonical source/destination, missing capacity and same-object concurrency reject atomically.
    #[tokio::test]
    async fn http_invalid_relocation_rejects_without_plan_or_binding_mutation() {
        let source = load(&snapshot()).unwrap();
        for change in ["source", "destination", "extra", "resources", "same-object"] {
            let fixture = Fixture::new(true);
            let before = fixture.events.latest_sequence().unwrap();
            let mut document = plan(false);
            let role = &mut document["tasks"][1]["roles"][0];
            match change {
                "source" => {
                    role["execution_intent"]["parameters"]["source"] =
                        "initial-location:wrong".into()
                }
                "destination" => {
                    role["execution_intent"]["parameters"]["destination"] = "invented".into()
                }
                "extra" => role["execution_intent"]["parameters"]["invented"] = "value".into(),
                "resources" => role["requirements"]["resources"] = serde_json::json!([]),
                "same-object" => {
                    role["execution_intent"]["parameters"]["object"] = "object:0".into();
                    role["execution_intent"]["parameters"]["source"] =
                        snapshot()["operation_admission"]["object_sources"]["object:0"].clone();
                }
                _ => unreachable!(),
            }
            let response = fixture.submit(&document, &source).await;
            assert!(response.starts_with("HTTP/1.1 409"), "{change}: {response}");
            assert_eq!(fixture.events.latest_sequence().unwrap(), before);
            assert!(
                fixture
                    .controller
                    .lock()
                    .unwrap()
                    .bridge
                    .attempt_history()
                    .is_empty()
            );
            assert!(
                fixture
                    .controller
                    .lock()
                    .unwrap()
                    .orchestrator
                    .mission_ids()
                    .is_empty()
            );
        }
    }

    /// Old navigation artifacts do not silently enable relocation; negative navigation evidence remains active.
    #[test]
    fn navigation_default_and_operation_boundaries_remain_fail_closed() {
        let group = domain::ExecutionGroupId::new("synthetic").unwrap();
        let relocation = decode_mission_plan(&plan(false).to_string()).unwrap();
        let mut old = snapshot();
        old.as_object_mut().unwrap().remove("operation_admission");
        old["schema_version"] = "roboguide.deployment-intent-feasibility/v0.3".into();
        seal(&mut old);
        assert!(
            load(&old)
                .unwrap()
                .restrictions_for_plan(&relocation, &group)
                .unwrap_err()
                .contains("reset-bound")
        );
        let mut navigation = plan(false);
        for task in navigation["tasks"].as_array_mut().unwrap() {
            let role = &mut task["roles"][0];
            role["execution_intent"]["operation"] =
                serde_json::json!({"namespace":"mobility","name":"move","version":"v1"});
            role["execution_intent"]["parameters"]
                .as_object_mut()
                .unwrap()
                .retain(|key, _| key == "destination");
            role["requirements"]["capabilities"][0]["contract"] =
                role["execution_intent"]["operation"].clone();
        }
        let navigation = decode_mission_plan(&navigation.to_string()).unwrap();
        assert!(
            load(&snapshot())
                .unwrap()
                .restrictions_for_plan(&navigation, &group)
                .unwrap_err()
                .contains("without a candidate")
        );
    }

    /// A new digest cannot launder malformed profiles, and both sources bind the restore watermark.
    #[test]
    fn operation_schema_source_and_watermark_validation() {
        let original = snapshot();
        let first = load(&original).unwrap();
        for change in [
            "schema",
            "missing-schema",
            "capacity",
            "config",
            "coverage",
            "reachability",
            "source",
        ] {
            let mut document = original.clone();
            match change {
                "schema" => {
                    document["schema_version"] =
                        "roboguide.deployment-intent-feasibility/v0.3".into()
                }
                "missing-schema" => {
                    document.as_object_mut().unwrap().remove("schema_version");
                }
                "capacity" => {
                    document["operation_admission"]["endpoint_profiles"][0]["resource_capacity"] =
                        true.into()
                }
                "config" => {
                    document["operation_admission"]["endpoint_profiles"][0]["node_config_digest"] =
                        format!("sha256:{}", "b".repeat(64)).into()
                }
                "coverage" => {
                    document["operation_admission"]["endpoint_profiles"]
                        .as_array_mut()
                        .unwrap()
                        .pop();
                }
                "reachability" => {
                    document["operation_admission"]["route_reachability"] = "proven".into()
                }
                "source" => {
                    document["operation_admission"]["object_sources"]["object:0"] =
                        "invented".into()
                }
                _ => unreachable!(),
            }
            seal(&mut document);
            assert!(load(&document).is_err(), "{change}");
        }
        let mut changed = original;
        changed["operation_admission"]["source_snapshot_digest"] =
            format!("sha256:{}", "c".repeat(64)).into();
        seal(&mut changed);
        assert_ne!(first.digest(), load(&changed).unwrap().digest());
    }

    /// Live operation coverage accepts at most four actual manipulators while legacy stays dual.
    #[test]
    fn live_manipulator_bound_matches_the_full_endpoint_registry() {
        let node_agents = (0..5)
            .map(|agent| (NodeId::new(format!("node-{agent}")).unwrap(), agent))
            .collect::<BTreeMap<_, _>>();
        let node_sources = node_agents
            .keys()
            .map(|node| (node.clone(), format!("sha256:{}", "a".repeat(64))))
            .collect::<BTreeMap<_, _>>();
        for count in 1..=5 {
            let mut document = snapshot()["operation_admission"].clone();
            document["endpoint_profiles"] = serde_json::Value::Array(
                (0..count)
                    .map(|agent| {
                        serde_json::json!({
                            "agent_id": agent, "node_id": format!("node-{agent}"),
                            "node_config_digest": format!("sha256:{}", "a".repeat(64)),
                            "resource_kind": "space", "resource_capacity": 1
                        })
                    })
                    .collect(),
            );
            let result = DeploymentOperationAdmission::from_json(
                &document,
                &node_agents,
                &node_sources,
                true,
            );
            assert_eq!(result.is_ok(), count <= 4, "live count {count}");
            let legacy_agents = node_agents
                .iter()
                .take(count)
                .map(|(node, agent)| (node.clone(), *agent))
                .collect();
            assert_eq!(
                DeploymentOperationAdmission::from_json(
                    &document,
                    &legacy_agents,
                    &node_sources,
                    false,
                )
                .is_ok(),
                count <= 2,
                "legacy count {count}"
            );
        }
    }

    /// Complete live sources reach the same startup reader used by the Controller application.
    #[test]
    fn live_startup_reader_accepts_three_and_four_source_bound_manipulators() {
        for count in [3, 4] {
            let mut document = snapshot();
            document["schema_version"] = "roboguide.deployment-intent-feasibility/v0.5".into();
            let initial = document["initial_agent_positions"]["0"].clone();
            let original_records = document["records"].as_array().unwrap().clone();
            let profile = document["operation_admission"]["endpoint_profiles"][0].clone();
            for agent in 2..count {
                let node = format!("node-{agent}");
                document["initial_agent_positions"][agent.to_string()] = initial.clone();
                for mut record in original_records
                    .iter()
                    .filter(|record| record["agent_id"] == 0)
                    .cloned()
                {
                    record["agent_id"] = agent.into();
                    record["node_id"] = node.clone().into();
                    record["profile"]["agent_id"] = agent.into();
                    record["profile"]["node_id"] = node.clone().into();
                    document["records"].as_array_mut().unwrap().push(record);
                }
                let mut endpoint = profile.clone();
                endpoint["agent_id"] = agent.into();
                endpoint["node_id"] = node.into();
                document["operation_admission"]["endpoint_profiles"]
                    .as_array_mut()
                    .unwrap()
                    .push(endpoint);
            }
            document["execution_profile"] = serde_json::json!({
                "mode": "independent-live/v0.1", "registry_digest": format!("sha256:{}", "a".repeat(64)),
                "endpoints": document["operation_admission"]["endpoint_profiles"].as_array().unwrap()
                    .iter().map(|endpoint| serde_json::json!({
                        "agent_id": endpoint["agent_id"], "node_id": endpoint["node_id"],
                        "operations": ["mobility.move@v1", "mobility.navigate@v1", "object.relocate@v1"]
                    })).collect::<Vec<_>>()
            });
            seal(&mut document);
            let evidence = load(&document).expect("complete live startup sources load");
            let plan = decode_mission_plan(&plan(false).to_string()).unwrap();
            let restrictions = evidence
                .restrictions_for_plan(&plan, &domain::ExecutionGroupId::new("live-group").unwrap())
                .unwrap();
            assert!(restrictions.iter().all(|(_, nodes)| nodes.len() == count));
            document["operation_admission"]["endpoint_profiles"]
                .as_array_mut()
                .unwrap()
                .pop();
            seal(&mut document);
            assert!(
                load(&document).is_ok(),
                "a typed manipulator subset remains valid"
            );
        }
    }
}
