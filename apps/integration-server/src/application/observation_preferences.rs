//! Advisory same-reset observation geometry; no eligibility, reservation or physical authority.

use super::deployment_document::content_digest;
use std::collections::{BTreeMap, BTreeSet};

/// Finite primary-camera model, independently validated from the published source.
#[derive(Debug, Clone)]
struct Camera {
    /// Optical center above the observed kinematic base point, in metres.
    height: f64,
    /// Optical center offset along the platform's local forward axis, in metres.
    forward: f64,
    /// Fixed camera elevation in radians; no gimbal or articulated mount is inferred.
    pitch: f64,
    /// Tangent of the declared horizontal half field of view.
    horizontal_tangent: f64,
    /// Tangent of the declared vertical half field of view.
    vertical_tangent: f64,
}

/// Original reset evidence retained without exposing concrete providers to MI.
#[derive(Debug, Clone)]
pub(super) struct ObservationPreferences {
    /// Exact original source identity for audit-only candidate-order attribution.
    digest: String,
    /// Positive geometric evidence per exact condition and Node; false means unknown.
    support: BTreeMap<String, BTreeMap<domain::NodeId, bool>>,
    /// One original local receive/expiry window, absent after restore.
    lifetime: Option<(domain::TimestampMs, domain::TimestampMs)>,
    /// Prevents even a restored/disabled source from acquiring another receive clock.
    configured: bool,
}

/// Require closed fields before using any source as an ordering hint.
fn fields(value: &serde_json::Value, expected: &[&str]) -> Result<(), String> {
    if value.as_object().is_none_or(|value| {
        value.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != expected.iter().copied().collect()
    }) {
        return Err("observation preference source has invalid fields".into());
    }
    Ok(())
}

/// Accept bounded finite scalars without coercing null, booleans or strings.
fn scalar(value: &serde_json::Value, maximum: f64) -> Result<f64, String> {
    value
        .as_f64()
        .filter(|number| number.is_finite() && number.abs() <= maximum)
        .ok_or_else(|| "observation geometry scalar is invalid".into())
}

/// Decode an exact three-dimensional scene coordinate.
fn point(value: &serde_json::Value) -> Result<[f64; 3], String> {
    let parts = value
        .as_array()
        .filter(|parts| parts.len() == 3)
        .ok_or("observation geometry position is invalid")?;
    Ok([
        scalar(&parts[0], 1_000_000.0)?,
        scalar(&parts[1], 1_000_000.0)?,
        scalar(&parts[2], 1_000_000.0)?,
    ])
}

/// Reconstruct only the actual declared fixed-primary model; absent calibration stays unknown.
fn camera(value: &serde_json::Value) -> Result<Option<Camera>, String> {
    if value.is_null() {
        return Ok(None);
    }
    fields(
        value,
        &[
            "camera-model",
            "camera-height-above-base-m",
            "camera-forward-offset-m",
            "camera-forward-elevation-degrees",
            "camera-horizontal-fov-degrees",
            "camera-vertical-fov-degrees",
            "camera-pan-tilt-supported",
        ],
    )?;
    let height = scalar(&value["camera-height-above-base-m"], 100.0)?;
    let forward = scalar(&value["camera-forward-offset-m"], 100.0)?;
    let pitch = scalar(&value["camera-forward-elevation-degrees"], 90.0)?;
    let horizontal = scalar(&value["camera-horizontal-fov-degrees"], 180.0)?;
    let vertical = scalar(&value["camera-vertical-fov-degrees"], 180.0)?;
    if value["camera-model"] != "fixed-primary-pinhole/v0.1"
        || value["camera-pan-tilt-supported"] != false
        || height < 0.0
        || !(0.0..180.0).contains(&horizontal)
        || horizontal == 0.0
        || !(0.0..180.0).contains(&vertical)
        || vertical == 0.0
    {
        return Err("observation calibration is unsupported".into());
    }
    Ok(Some(Camera {
        height,
        forward,
        pitch: pitch.to_radians(),
        horizontal_tangent: (horizontal.to_radians() / 2.0).tan(),
        vertical_tangent: (vertical.to_radians() / 2.0).tan(),
    }))
}

/// Recompute the same finite hypothetical stencil without consulting a simulator or inventory.
fn candidates(record: &serde_json::Value, compound: bool) -> Result<Option<u64>, String> {
    let camera = camera(&record["camera"])?;
    let reference = if record["reference_height_above_base_m"].is_null() {
        None
    } else {
        Some(scalar(&record["reference_height_above_base_m"], 100.0)?)
    };
    let radius = if record["arrival_tolerance_m"].is_null() {
        None
    } else {
        let radius = scalar(&record["arrival_tolerance_m"], 100.0)?;
        if radius <= 0.0 {
            return Err("observation arrival tolerance is invalid".into());
        }
        Some(radius)
    };
    if !compound && radius.is_some() {
        return Err("detection-only preference gained an arrival condition".into());
    }
    let (base, target) = (
        point(&record["base_position"])?,
        point(&record["target_position"])?,
    );
    let (Some(camera), Some(reference)) = (camera, reference) else {
        return Ok(None);
    };
    if compound && radius.is_none() {
        return Ok(None);
    }
    let extent = if compound {
        radius.expect("checked radius")
    } else {
        2.0
    };
    let mut count = 0;
    for fraction in [0.25, 0.5, 0.75, 1.0] {
        for direction in 0..8 {
            let angle = f64::from(direction) * std::f64::consts::PI / 4.0;
            let point = [
                target[0] + extent * fraction * angle.cos(),
                base[1],
                target[2] + extent * fraction * angle.sin(),
            ];
            let distance = ((point[0] - target[0]).powi(2)
                + (point[1] + reference - target[1]).powi(2)
                + (point[2] - target[2]).powi(2))
            .sqrt();
            if compound && distance > extent + 1e-7 {
                continue;
            }
            let heading = (-(target[2] - point[2])).atan2(target[0] - point[0]);
            let (dx, dz) = (target[0] - point[0], target[2] - point[2]);
            let forward = dx * heading.cos() - dz * heading.sin() - camera.forward;
            let side = dx * heading.sin() + dz * heading.cos();
            let height = target[1] - point[1] - camera.height;
            let depth = forward * camera.pitch.cos() + height * camera.pitch.sin();
            let vertical = height * camera.pitch.cos() - forward * camera.pitch.sin();
            if depth > 0.0
                && side.abs() <= depth * camera.horizontal_tangent + 1e-7
                && vertical.abs() <= depth * camera.vertical_tangent + 1e-7
            {
                count += 1;
            }
        }
    }
    Ok(Some(count))
}

impl ObservationPreferences {
    /// Validate exact reset/source coverage and recompute every advertised geometric count.
    pub(super) fn load(
        source: &serde_json::Value,
        parent: &serde_json::Value,
        node_agents: &BTreeMap<domain::NodeId, i64>,
        node_sources: &BTreeMap<domain::NodeId, String>,
    ) -> Result<Self, String> {
        fields(
            source,
            &[
                "schema_version",
                "scope",
                "authority",
                "parent_feasibility_digest",
                "identity",
                "frame_id",
                "position_unit",
                "up_axis",
                "target_entities",
                "candidate_count_per_record",
                "route_visibility_and_physical_success",
                "records",
                "digest",
            ],
        )?;
        let mut unsigned = source.clone();
        unsigned.as_object_mut().unwrap().remove("digest");
        let digest = content_digest(&unsigned)?;
        let mut original = parent.clone();
        original
            .as_object_mut()
            .unwrap()
            .remove("observation_preferences");
        original["schema_version"] = "roboguide.deployment-intent-feasibility/v0.6".into();
        if source["schema_version"] != "roboguide.initial-observation-geometry/v0.1"
            || source["scope"] != "hypothetical-upright-yaw-on-reset-base-plane"
            || source["authority"] != "deployment-observed-reset-state"
            || source["digest"] != digest
            || source["parent_feasibility_digest"] != content_digest(&original)?
            || source["identity"] != parent["identity"]
            || source["candidate_count_per_record"] != 32
            || source["position_unit"] != "metres"
            || source["up_axis"] != "y"
            || source["frame_id"]
                != format!(
                    "scene:{}",
                    parent["identity"]["scene_id"].as_str().unwrap_or_default()
                )
            || source["route_visibility_and_physical_success"] != "unknown"
        {
            return Err(
                "observation preferences differ from original reset identity or scope".into(),
            );
        }
        let targets = source["target_entities"]
            .as_object()
            .filter(|targets| targets.len() <= 64)
            .ok_or("observation target coverage is invalid")?;
        let endpoints = parent["execution_profile"]["endpoints"]
            .as_array()
            .ok_or("observation preferences lack live endpoints")?;
        let mut expected = BTreeSet::new();
        for (entity, position) in targets {
            point(position)?;
            if entity.trim().is_empty() || entity.contains(['(', ')', '\n', '\r']) {
                return Err("observation target identity is invalid".into());
            }
            let world = parent["records"]
                .as_array()
                .ok_or("observation preferences lack world geometry")?;
            let relevant: Vec<_> = world
                .iter()
                .filter(|record| record["destination"] == *entity)
                .collect();
            if relevant.is_empty()
                || relevant
                    .iter()
                    .any(|record| record["destination_entity"]["position"] != *position)
            {
                return Err("observation target differs from original world position".into());
            }
            for endpoint in endpoints.iter().filter(|endpoint| {
                endpoint["operations"].as_array().is_some_and(|operations| {
                    operations
                        .iter()
                        .any(|operation| operation == "observation.acquire@v1")
                })
            }) {
                let node = endpoint["node_id"].as_str().unwrap_or_default();
                expected.insert((node.to_owned(), format!("detected({entity})")));
                if endpoint["condition_format"] == "detected(entity-ref) [AND at(same-entity-ref)]"
                {
                    expected.insert((
                        node.to_owned(),
                        format!("detected({entity}) AND at({entity})"),
                    ));
                }
            }
        }
        let records = source["records"]
            .as_array()
            .filter(|records| records.len() <= 512)
            .ok_or("observation preferences exceed their record budget")?;
        let mut observed = BTreeSet::new();
        let mut support = BTreeMap::<String, BTreeMap<domain::NodeId, bool>>::new();
        for record in records {
            fields(
                record,
                &[
                    "node_id",
                    "agent_id",
                    "node_config_digest",
                    "operation",
                    "expected",
                    "entity_id",
                    "base_position",
                    "target_position",
                    "reference_height_above_base_m",
                    "camera",
                    "arrival_tolerance_m",
                    "geometric_candidate_count",
                ],
            )?;
            let node = domain::NodeId::new(record["node_id"].as_str().unwrap_or_default())
                .map_err(|error| error.to_string())?;
            let expected_condition = record["expected"]
                .as_str()
                .filter(|text| text.chars().count() <= 512)
                .ok_or("observation condition is invalid")?;
            let entity = record["entity_id"]
                .as_str()
                .ok_or("observation target is invalid")?;
            if record["agent_id"].as_i64() != node_agents.get(&node).copied()
                || record["node_config_digest"].as_str()
                    != node_sources.get(&node).map(String::as_str)
                || record["base_position"]
                    != parent["initial_agent_positions"][record["agent_id"].to_string()]
                || targets.get(entity) != Some(&record["target_position"])
                || record["operation"] != "observation.acquire@v1"
                || !observed.insert((node.as_str().to_owned(), expected_condition.to_owned()))
                || !expected.contains(&(node.as_str().to_owned(), expected_condition.to_owned()))
            {
                return Err(
                    "observation preference differs from its exact endpoint/condition source"
                        .into(),
                );
            }
            let compound = expected_condition != format!("detected({entity})");
            if compound && expected_condition != format!("detected({entity}) AND at({entity})") {
                return Err("observation condition target differs".into());
            }
            let count = candidates(record, compound)?;
            if record["geometric_candidate_count"] != serde_json::json!(count) {
                return Err("observation geometric count differs from bounded projection".into());
            }
            support
                .entry(expected_condition.to_owned())
                .or_default()
                .insert(node, count.is_some_and(|count| count > 0));
        }
        if expected != observed {
            return Err("observation preferences lack complete declared coverage".into());
        }
        Ok(Self {
            digest,
            support,
            lifetime: None,
            configured: false,
        })
    }

    /// Retain one receive-relative lifetime only for a genuinely fresh Controller.
    pub(super) fn configure(
        &mut self,
        received: domain::TimestampMs,
        fresh: bool,
    ) -> Result<(), String> {
        if self.configured {
            return Err("observation preference receive time cannot be renewed".into());
        }
        if fresh {
            self.lifetime = Some((
                received,
                received
                    .as_millis()
                    .checked_add(control::MAX_INITIAL_PREFERENCE_AGE_MS)
                    .map(domain::TimestampMs::new)
                    .ok_or("observation preference lifetime overflows")?,
            ));
        }
        self.configured = true;
        Ok(())
    }

    /// Rank current eligible initial slots with finite parallel-placement lookahead only.
    pub(super) fn for_plan<S: ports::SharedNodeStateReader, E: ports::EventSink>(
        &self,
        plan: &domain::MissionPlan,
        control: &control::ControlPlane,
        state: &S,
        now: domain::TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) -> Result<Option<control::InitialCandidatePreferences>, String> {
        let Some((received, expires)) = self.lifetime else {
            return Ok(None);
        };
        if now < received || now >= expires {
            return Ok(None);
        }
        let roots: Vec<_> = plan
            .task_graph()
            .tasks()
            .iter()
            .filter(|task| task.dependencies().is_empty())
            .collect();
        let Some(first) = roots.first().copied() else {
            return Ok(None);
        };
        let mut actors = BTreeSet::new();
        let mut slots = Vec::new();
        for task in roots {
            let candidates = match control.match_capabilities_for_mission(
                state,
                plan,
                task.requirement(),
                now,
                correlation,
                events,
            ) {
                Ok(value) => value,
                Err(_) => return Ok(None),
            };
            for role in task.requirement().roles() {
                let Some(actor) = role.actor_id() else {
                    return Ok(None);
                };
                if !actors.insert(actor.clone()) {
                    continue;
                }
                let intent = task
                    .execution_intent(role.role_id())
                    .ok_or("observation preference lacks exact intent")?;
                if intent.operation().to_string() != "observation.acquire@v1"
                    || intent.parameters().len() != 1
                {
                    return Ok(None);
                }
                let Some(domain::ExecutionValue::String(expected)) =
                    intent.parameters().get("expected")
                else {
                    return Ok(None);
                };
                let Some(support) = self.support.get(expected) else {
                    return Ok(None);
                };
                let nodes = candidates
                    .for_role(role.role_id())
                    .expect("matched role")
                    .node_ids();
                if nodes.is_empty() || nodes.len() > 4 {
                    return Ok(None);
                }
                slots.push((
                    task.requirement().task_ref().clone(),
                    role.role_id().clone(),
                    nodes
                        .iter()
                        .map(|node| (node.clone(), support.get(node).copied().unwrap_or(false)))
                        .collect::<Vec<_>>(),
                ));
                if slots.len() > 4 {
                    return Ok(None);
                }
            }
        }
        if slots.is_empty() || slots.len() > 4 {
            return Ok(None);
        }
        let mut combinations = vec![(Vec::<domain::NodeId>::new(), 0_u32)];
        for (_, _, nodes) in &slots {
            let mut next = Vec::new();
            for (chosen, unknown) in combinations {
                for (node, supported) in nodes {
                    if chosen.contains(node) {
                        continue;
                    }
                    let mut selected = chosen.clone();
                    selected.push(node.clone());
                    next.push((selected, unknown + u32::from(!supported)));
                }
            }
            combinations = next;
        }
        if combinations.is_empty() {
            return Ok(None);
        }
        let mut priorities = BTreeMap::new();
        let mut useful = false;
        for (index, (_, role, nodes)) in slots
            .iter()
            .enumerate()
            .filter(|(_, slot)| slot.0 == *first.requirement().task_ref())
        {
            let ranks: BTreeMap<_, _> = nodes
                .iter()
                .map(|(node, _)| {
                    let score = combinations
                        .iter()
                        .filter(|(chosen, _)| chosen[index] == *node)
                        .map(|(_, unknown)| *unknown)
                        .min()
                        .unwrap_or(5);
                    (node.clone(), score)
                })
                .collect();
            useful |= ranks.values().collect::<BTreeSet<_>>().len() > 1;
            priorities.insert(role.clone(), ranks);
        }
        if !useful {
            return Ok(None);
        }
        control::InitialCandidatePreferences::new(
            first.requirement().task_ref().clone(),
            priorities,
            self.digest.clone(),
            received,
            expires,
        )
        .map(Some)
        .map_err(|error| error.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ports::SharedNodeStateWriter;

    /// Read a synthetic Python-produced projection shared across both implementations.
    fn snapshot() -> serde_json::Value {
        serde_json::from_str(include_str!("../../../../integrations/habitat-local-eaios/tests/fixtures/initial-observation-geometry.json")).unwrap()
    }

    /// Preserve the original closed-digest format when deliberately testing altered evidence.
    fn seal(document: &mut serde_json::Value) {
        let source = &mut document["observation_preferences"];
        source.as_object_mut().unwrap().remove("digest");
        source["digest"] = content_digest(source).unwrap().into();
        document.as_object_mut().unwrap().remove("digest");
        document["digest"] = content_digest(document).unwrap().into();
    }

    /// Exercise actual startup source validation without any Node service or simulator.
    fn load(document: &serde_json::Value) -> Result<super::super::DeploymentFeasibility, String> {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source.json");
        std::fs::write(&path, document.to_string()).unwrap();
        super::super::DeploymentFeasibility::load(&path)
    }

    /// Build two generic independent acquisition Tasks through the production decoder.
    fn plan(serial: bool) -> domain::MissionPlan {
        let mut body: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/mission/execution-planning-v0.2/fixtures/regression-cases.json"
        ))
        .unwrap();
        body = body["plan"].clone();
        let mut tasks = vec![body["tasks"][1].clone(), body["tasks"][3].clone()];
        for (index, task) in tasks.iter_mut().enumerate() {
            if serial {
                task["satisfaction"] = serde_json::json!({
                    "basis": "execution-report",
                    "expected_effect": "Local observation acquired; the Mission retains its joint goal."
                });
            }
            task["depends_on"] = if serial && index == 1 {
                serde_json::json!(["observe-a"])
            } else {
                serde_json::json!([])
            };
            task["roles"][0]["execution_intent"]["parameters"] = serde_json::json!({"expected":format!("detected(object:{index}) AND at(object:{index})")});
        }
        body["tasks"] = tasks.into();
        if serial {
            body["mission"]["actors"] = serde_json::json!([{"id":"participant-a"}]);
            body["contexts"][0]["roles"][1]["actor"] = "participant-a".into();
        }
        orchestration::decode_mission_plan(&body.to_string()).unwrap()
    }

    /// Suppress audit-only matching events in this isolated deterministic test.
    struct Events;
    impl ports::EventSink for Events {
        /// Retain no authoritative effects from this pure initial-preference computation.
        fn append(
            &mut self,
            _: domain::TimestampMs,
            _: &domain::CorrelationId,
            _: Option<&domain::EventId>,
            _: domain::EventPayload,
        ) {
        }
    }

    /// Register actual-shaped capability/operation/resource declarations independently of geometry.
    fn nodes() -> (control::ControlPlane, state::InMemorySharedNodeState) {
        let mut control = control::ControlPlane::new();
        let mut state = state::InMemorySharedNodeState::new();
        let owner = domain::LocalSystemId::new("local").unwrap();
        let capability =
            domain::CapabilityContractRef::new("observation", "acquire", "v1").unwrap();
        let operation = domain::OperationRef::new("observation", "acquire", "v1").unwrap();
        for letter in ["a", "b"] {
            let resource = domain::Resource::new(
                domain::ResourceId::new(format!("space-{letter}")).unwrap(),
                domain::ResourceKind::Space,
                1,
            )
            .unwrap();
            let registration = domain::NodeRegistration::new_with_local_systems_and_readiness(
                domain::NodeId::new(format!("node-{letter}")).unwrap(),
                vec![domain::LocalSystemDescriptor::new(
                    owner.clone(),
                    domain::LocalRuntime::new("geometry-test", "1").unwrap(),
                    BTreeMap::new(),
                )],
                domain::NodeContractVersion::v0_6(),
                vec![domain::Capability::new(
                    domain::CapabilityKind::Observation,
                    true,
                )],
                BTreeMap::from([(capability.clone(), owner.clone())]),
                BTreeMap::from([(capability.clone(), domain::CapabilityKind::Observation)]),
                BTreeMap::from([(capability.clone(), true)]),
                Vec::new(),
                vec![resource.clone()],
                BTreeMap::from([(resource.id().clone(), owner.clone())]),
            )
            .unwrap()
            .with_operation_support(vec![domain::OperationSupport::new(
                operation.clone(),
                owner.clone(),
            )])
            .unwrap();
            control
                .register_node(
                    &mut state,
                    registration,
                    domain::NodeStatus::new(
                        domain::NodeHealth::Online,
                        domain::TimestampMs::new(100),
                    ),
                    domain::TimestampMs::new(100),
                    &domain::CorrelationId::new("geometry-test").unwrap(),
                    &mut Events,
                )
                .unwrap();
        }
        (control, state)
    }

    /// Geometry changes order only; ordinary Matching, health and Actor continuity retain authority.
    #[test]
    fn actual_matching_retains_unknown_nodes_and_prefers_positive_joint_geometry() {
        let mut deployment = load(&snapshot()).unwrap();
        deployment
            .configure_observation_preferences(domain::TimestampMs::new(100), true)
            .unwrap();
        let (mut control, mut state) = nodes();
        let plan = plan(false);
        let trace = domain::CorrelationId::new("geometry").unwrap();
        let now = domain::TimestampMs::new(100);
        let before = serde_json::to_string(&control.checkpoint()).unwrap();
        let preference = deployment
            .initial_preferences_for_plan(&plan, &control, &state, now, &trace, &mut Events)
            .unwrap()
            .unwrap();
        assert_eq!(
            before,
            serde_json::to_string(&control.checkpoint()).unwrap()
        );
        control
            .set_initial_candidate_preferences(&plan, preference)
            .unwrap();
        let first = &plan.task_graph().tasks()[0];
        let candidates = control
            .match_capabilities_for_mission(
                &state,
                &plan,
                first.requirement(),
                now,
                &trace,
                &mut Events,
            )
            .unwrap();
        assert_eq!(
            candidates.roles()[0].node_ids().len(),
            2,
            "unknown geometry never excludes a Node"
        );
        let chosen = control::BoundedJointScheduler::new()
            .schedule_task(
                &state,
                first.requirement(),
                &candidates,
                now,
                &trace,
                &mut Events,
            )
            .unwrap();
        assert_eq!(
            chosen.proposed_assignments()[0].node_id(),
            &domain::NodeId::new("node-b").unwrap()
        );
        state
            .record_node_health(domain::NodeHealthObservation::new(
                domain::NodeId::new("node-b").unwrap(),
                domain::NodeStatus::new(domain::NodeHealth::Offline, now),
                now,
            ))
            .unwrap();
        let current = control
            .match_capabilities_for_mission(
                &state,
                &plan,
                first.requirement(),
                now,
                &trace,
                &mut Events,
            )
            .unwrap();
        assert_eq!(
            current.roles()[0].node_ids(),
            &[domain::NodeId::new("node-a").unwrap()]
        );
    }

    /// Source expiry, restore and unsupported scope cannot renew initial observation ordering.
    #[test]
    fn original_lifetime_restore_and_serial_reuse_remain_explicit() {
        let (control, state) = nodes();
        let trace = domain::CorrelationId::new("geometry").unwrap();
        for (fresh, now, expected) in [
            (true, 100, true),
            (false, 100, false),
            (true, 99, false),
            (true, 600_100, false),
        ] {
            let mut deployment = load(&snapshot()).unwrap();
            deployment
                .configure_observation_preferences(domain::TimestampMs::new(100), fresh)
                .unwrap();
            let serial = plan(true);
            assert_eq!(
                deployment
                    .initial_preferences_for_plan(
                        &serial,
                        &control,
                        &state,
                        domain::TimestampMs::new(now),
                        &trace,
                        &mut Events
                    )
                    .unwrap()
                    .is_some(),
                expected
            );
            let group = domain::ExecutionGroupId::new("serial").unwrap();
            assert!(deployment.restrictions_for_plan(&serial, &group).is_ok());
            assert!(
                deployment
                    .configure_observation_preferences(domain::TimestampMs::new(101), true)
                    .is_err()
            );
        }
    }

    /// Zero or missing primary-camera evidence stays tied without changing plan eligibility.
    #[test]
    fn unknown_geometry_and_retained_verifier_resources_keep_their_original_meaning() {
        let mut body = snapshot();
        for record in body["observation_preferences"]["records"]
            .as_array_mut()
            .unwrap()
        {
            record["camera"] = serde_json::Value::Null;
            record["geometric_candidate_count"] = serde_json::Value::Null;
        }
        seal(&mut body);
        let mut deployment = load(&body).unwrap();
        deployment
            .configure_observation_preferences(domain::TimestampMs::new(100), true)
            .unwrap();
        let (control, state) = nodes();
        let independent = plan(false);
        assert!(
            deployment
                .initial_preferences_for_plan(
                    &independent,
                    &control,
                    &state,
                    domain::TimestampMs::new(100),
                    &domain::CorrelationId::new("unknown").unwrap(),
                    &mut Events
                )
                .unwrap()
                .is_none()
        );
        assert!(
            deployment
                .restrictions_for_plan(
                    &independent,
                    &domain::ExecutionGroupId::new("independent").unwrap()
                )
                .is_ok()
        );
        let mut retained: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/mission/execution-planning-v0.2/fixtures/regression-cases.json"
        ))
        .unwrap();
        retained = retained["plan"].clone();
        retained["mission"]["actors"] = serde_json::json!([{"id":"participant-a"}]);
        retained["contexts"][0]["roles"][1]["actor"] = "participant-a".into();
        let mut tasks = vec![retained["tasks"][1].clone(), retained["tasks"][3].clone()];
        for (index, task) in tasks.iter_mut().enumerate() {
            task["depends_on"] = if index == 0 {
                serde_json::json!([])
            } else {
                serde_json::json!(["observe-a"])
            };
            task["roles"][0]["execution_intent"]["parameters"] = serde_json::json!({"expected":format!("detected(object:{index}) AND at(object:{index})")});
        }
        retained["tasks"] = tasks.into();
        let retained = orchestration::decode_mission_plan(&retained.to_string()).unwrap();
        assert!(
            deployment
                .restrictions_for_plan(
                    &retained,
                    &domain::ExecutionGroupId::new("retained").unwrap()
                )
                .unwrap_err()
                .contains("final verifier resource lifetime")
        );
    }

    /// Rehashing cannot launder a wrong reset, endpoint, point, count or incomplete source.
    #[test]
    fn malformed_projection_refuses_before_control_or_node_effects() {
        assert!(load(&snapshot()).is_ok());
        for fault in [
            "count",
            "node",
            "base",
            "target",
            "identity",
            "scope",
            "coverage",
            "duplicate",
            "legacy",
        ] {
            let mut body = snapshot();
            let source = &mut body["observation_preferences"];
            match fault {
                "count" => source["records"][0]["geometric_candidate_count"] = 31.into(),
                "node" => source["records"][0]["node_config_digest"] = "sha256:wrong".into(),
                "base" => source["records"][0]["base_position"][0] = 99.into(),
                "target" => source["records"][0]["target_position"][0] = 99.into(),
                "identity" => source["identity"]["episode_id"] = "other".into(),
                "scope" => source["route_visibility_and_physical_success"] = "proven".into(),
                "coverage" => {
                    source["records"].as_array_mut().unwrap().pop();
                }
                "duplicate" => {
                    let extra = source["records"][0].clone();
                    source["records"].as_array_mut().unwrap().push(extra);
                }
                "legacy" => {
                    body["schema_version"] = "roboguide.deployment-intent-feasibility/v0.6".into()
                }
                _ => unreachable!(),
            }
            seal(&mut body);
            assert!(load(&body).is_err(), "fault {fault}");
        }
    }
}
