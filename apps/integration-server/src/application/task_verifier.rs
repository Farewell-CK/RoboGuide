//! Neutral accepted-plan and current-attempt consumers of validated verifier evidence.

pub(crate) use super::verifier_source::TaskVerifierFeed;
use super::verifier_source::VerifierVerdict;
#[cfg(test)]
use super::verifier_source::{MAX_VERIFIER_DOCUMENT_BYTES, SOURCE_SCHEMA, VERDICT_SCHEMA};
use crate::*;
use ports::EventSink;
#[cfg(test)]
use sha2::{Digest, Sha256};

impl TaskVerifierFeed {
    /// Requires every verifier-backed Task to have a real supported final source.
    pub(crate) fn validate_plan(&self, plan: &domain::MissionPlan) -> Result<(), String> {
        for task in plan.task_graph().tasks() {
            let domain::TaskSatisfactionBasis::VerifierEvidence(spec) = task.satisfaction_basis()
            else {
                continue;
            };
            if !self.supports(spec) {
                return Err(format!(
                    "Task {} requires verifier evidence absent from this deployment source",
                    task.task_id()
                ));
            }
            if plan.task_graph().tasks().iter().any(|other| {
                other
                    .dependencies()
                    .iter()
                    .any(|dependency| dependency == task.task_id())
            }) {
                return Err(format!(
                    "Task {} requires a final verifier verdict before dependent Tasks can run",
                    task.task_id()
                ));
            }
            if plan.actors().len() == 1 && plan.task_graph().tasks().len() > 1 {
                let mut prerequisites = BTreeSet::new();
                let mut pending = task.dependencies().to_vec();
                while let Some(dependency) = pending.pop() {
                    if !prerequisites.insert(dependency.clone()) {
                        continue;
                    }
                    let predecessor = plan
                        .task_graph()
                        .tasks()
                        .iter()
                        .find(|candidate| candidate.task_id() == &dependency)
                        .ok_or_else(|| "verifier Task has an unknown prerequisite".to_string())?;
                    pending.extend_from_slice(predecessor.dependencies());
                }
                if plan.task_graph().tasks().iter().any(|other| {
                    other.task_id() != task.task_id() && !prerequisites.contains(other.task_id())
                }) {
                    return Err(format!(
                        "Task {} needs a final verifier verdict but single-Actor execution has an unordered Task that could await the same terminal world",
                        task.task_id()
                    ));
                }
            }
        }
        Ok(())
    }
}

/// Checks the frozen source and current attempts without changing satisfaction or ownership.
pub(crate) fn validate_task_verifier(
    controller: &ControllerState,
    feed: &TaskVerifierFeed,
    verdict: &VerifierVerdict,
) -> Result<(), String> {
    if controller.verifier_source_digest.as_deref() != Some(feed.source_digest())
        || !feed.matches_verdict(verdict)
    {
        return Err("verifier verdict does not match the frozen source".into());
    }
    for row in &verdict.tasks {
        if controller.verifier_seen.contains(&(
            verdict.digest.clone(),
            row.mission_id.clone(),
            row.task_id.clone(),
        )) {
            continue;
        }
        let mission_id = domain::MissionId::new(&row.mission_id)
            .map_err(|error| format!("verifier Mission identity is invalid: {error}"))?;
        let task_id = domain::TaskId::new(&row.task_id)
            .map_err(|error| format!("verifier Task identity is invalid: {error}"))?;
        let task_ref = domain::TaskRef::new(mission_id.clone(), task_id);
        let execution = controller
            .orchestrator
            .execution(&mission_id)
            .ok_or_else(|| "verifier verdict refers to an unknown Mission".to_string())?;
        let planned = execution
            .plan()
            .task_graph()
            .tasks()
            .iter()
            .find(|task| task.requirement().task_ref() == &task_ref)
            .ok_or_else(|| "verifier verdict refers to an unknown Task".to_string())?;
        if let domain::TaskSatisfactionBasis::VerifierEvidence(spec) = planned.satisfaction_basis()
            && !feed.supports(spec)
        {
            return Err("verifier verdict differs from the accepted Task specification".into());
        }
        let mut expected = BTreeSet::new();
        for role in planned.requirement().roles() {
            let attempt_id = controller
                .bridge
                .current_task_attempt_id(execution.group_id(), &task_ref, role.role_id())
                .ok_or_else(|| {
                    "verifier verdict arrived before a current physical attempt".to_string()
                })?;
            expected.insert((role.role_id().as_str().to_string(), attempt_id.to_string()));
        }
        let observed = row
            .attempts
            .iter()
            .map(|attempt| (attempt.role_id.clone(), attempt.attempt_id.clone()))
            .collect::<BTreeSet<_>>();
        if observed != expected || observed.len() != row.attempts.len() {
            return Err("verifier verdict does not cover the exact current Role attempts".into());
        }
    }
    Ok(())
}

/// Fences a restored database to the same verifier producer snapshot.
pub(crate) fn validate_restored_verifier_source(
    checkpoint: &ServerCheckpoint,
    configured_digest: Option<&str>,
) -> Result<(), String> {
    if matches!(
        checkpoint.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
            | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
            | PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA
            | PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA
            | PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA
            | PRE_TIMING_SERVER_CHECKPOINT_SCHEMA
            | PRE_ISOLATION_SERVER_CHECKPOINT_SCHEMA
            | PRE_RECOVERY_SERVER_CHECKPOINT_SCHEMA
            | ADMISSION_SERVER_CHECKPOINT_SCHEMA
            | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
    ) && checkpoint.verifier_source_digest.as_deref() != configured_digest
    {
        return Err("controller verifier source changed across checkpoint restore".into());
    }
    if !checkpoint.verifier_seen.is_empty() && checkpoint.verifier_source_digest.is_none() {
        return Err("controller verifier receipts lack a source identity".into());
    }
    Ok(())
}

/// Advances one validated verifier verdict inside the Controller timer transaction.
pub(crate) fn apply_task_verifier(
    controller: &mut ControllerState,
    feed: &TaskVerifierFeed,
    verdict: &VerifierVerdict,
    now: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), String> {
    for row in &verdict.tasks {
        let mission_id = domain::MissionId::new(&row.mission_id)
            .expect("verdict identities were validated before mutation");
        let task_ref = domain::TaskRef::new(
            mission_id.clone(),
            domain::TaskId::new(&row.task_id)
                .expect("verdict identities were validated before mutation"),
        );
        let key = (
            verdict.digest.clone(),
            row.mission_id.clone(),
            row.task_id.clone(),
        );
        if controller.verifier_seen.contains(&key) {
            continue;
        }
        let Some(execution) = controller.orchestrator.execution(&mission_id) else {
            continue;
        };
        if execution.isolation().is_some() || safety_stop::blocks_release(controller, &mission_id) {
            // Keep the original verdict unconsumed; the configured durable feed can present it
            // again after explicit resume, with current source and attempt validation intact.
            continue;
        }
        if matches!(
            execution.lifecycle(),
            orchestration::MissionExecutionLifecycle::Completed
                | orchestration::MissionExecutionLifecycle::Failed
                | orchestration::MissionExecutionLifecycle::Cancelling
                | orchestration::MissionExecutionLifecycle::Cancelled
        ) {
            // A terminal Runtime failure may release the Group before the
            // final-world producer publishes its verdict. That verdict cannot
            // reopen Mission satisfaction or fail the released Group again.
            continue;
        }
        let group_id = execution.group_id().clone();
        let awaiting = controller
            .bridge
            .control()
            .group(&group_id)
            .and_then(|group| group.task_execution(&task_ref))
            .is_some_and(|task| {
                task.lifecycle() == domain::TaskExecutionLifecycle::AwaitingSatisfaction
            });
        if !awaiting {
            continue;
        }
        let Some(domain::TaskSatisfactionBasis::VerifierEvidence(spec)) = controller
            .orchestrator
            .task_satisfaction_basis(&mission_id, &task_ref)
        else {
            continue;
        };
        if !verdict.satisfied && safety_stop::has_live_group_attempts(controller, &group_id) {
            safety_stop::admit_group_stop(controller, &group_id, now, 10_000)?;
            continue;
        }
        if verdict.satisfied {
            if !super::semantic_verifier::task_policy_allows(
                controller,
                &task_ref,
                domain::DecisionKind::Satisfaction,
                now,
            ) {
                continue;
            }
            super::semantic_verifier::audit_satisfaction(
                controller,
                &task_ref,
                now,
                correlation_id,
                events,
            );
        }
        let evidence = domain::TaskSatisfactionEvidence::new(
            task_ref.clone(),
            spec.verifier().clone(),
            verdict.predicate().to_owned(),
            feed.source(),
            domain::TimestampMs::new(verdict.source_observed_at_ms),
            now,
            verdict.satisfied,
        )
        .map_err(|error| error.to_string())?;
        controller
            .bridge
            .record_task_satisfaction_evidence(evidence.clone())
            .map_err(|error| error.to_string())?;
        events.append(
            now,
            correlation_id,
            None,
            domain::EventPayload::TaskVerifierVerdictObserved {
                task_ref: task_ref.clone(),
                source_id: feed.source_id().to_string(),
                source_revision: feed.source_revision().to_string(),
                verdict_digest: verdict.digest.clone(),
                satisfied: verdict.satisfied,
            },
        );
        let ControllerState {
            bridge,
            orchestrator,
            verifier_seen,
            ..
        } = controller;
        if verdict.satisfied {
            orchestrator
                .satisfy_task_from_verifier(
                    &mission_id,
                    &evidence,
                    bridge.control_mut(),
                    now,
                    correlation_id,
                    events,
                )
                .map_err(|error| error.to_string())?;
        } else {
            orchestrator
                .reject_task_satisfaction_from_verifier(
                    &mission_id,
                    &evidence,
                    bridge.control_mut(),
                    now,
                    correlation_id,
                    events,
                )
                .map_err(|error| error.to_string())?;
        }
        verifier_seen.insert(key);
        close_terminal_mission_coordination(controller, &mission_id);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Adds the canonical digest used by both deployment producer and Controller decoder.
    fn signed(mut value: serde_json::Value) -> serde_json::Value {
        let encoded = serde_json::to_vec(&value).expect("finite fixture JSON");
        value["digest"] =
            serde_json::Value::String(format!("sha256:{:x}", Sha256::digest(encoded)));
        value
    }

    /// Freezes one neutral source supporting only one exact predicate.
    fn source() -> serde_json::Value {
        signed(serde_json::json!({
            "schema_version": SOURCE_SCHEMA,
            "source_id": "test-official-goal",
            "source_revision": format!("sha256:{}", "a".repeat(64)),
            "identity": {
                "run_id": "run-1", "episode_id": "episode-1", "scene_id": "scene-1",
                "dataset_revision": "dataset-1", "dataset_sha256": "b".repeat(64)
            },
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "supported_predicates": ["goal-predicate"],
            "verdict_finality": "terminal"
        }))
    }

    /// Installs one isolated source with an absent verdict until the producer publishes.
    fn feed(directory: &tempfile::TempDir) -> TaskVerifierFeed {
        let source_path = directory.path().join("source.json");
        std::fs::write(&source_path, source().to_string()).expect("source written");
        TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json"))
            .expect("source admitted")
    }

    /// A missing source cannot be inferred from a MissionPlan verifier contract alone.
    #[test]
    fn source_requires_exact_schema_and_digest() {
        let directory = tempfile::tempdir().expect("directory");
        let source_path = directory.path().join("source.json");
        let mut altered = source();
        altered["identity"]["scene_id"] = "other-scene".into();
        std::fs::write(&source_path, altered.to_string()).expect("tampered source written");
        assert!(
            TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json")).is_err()
        );
        let mut unsupported = source();
        unsupported["schema_version"] = "future".into();
        let unsupported = signed(serde_json::json!({
            "schema_version": unsupported["schema_version"],
            "source_id": unsupported["source_id"],
            "source_revision": unsupported["source_revision"],
            "identity": unsupported["identity"],
            "verifier": unsupported["verifier"],
            "supported_predicates": unsupported["supported_predicates"],
            "verdict_finality": unsupported["verdict_finality"]
        }));
        std::fs::write(&source_path, unsupported.to_string()).expect("future source written");
        assert!(
            TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json")).is_err()
        );
    }

    /// Rejected, oversized, and stale-source verdict files never become positive evidence.
    #[test]
    fn verdict_rejects_tamper_duplicate_and_oversize() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        assert!(feed.read_verdict().expect("absence is explicit").is_none());
        let path = directory.path().join("verdict.json");
        let body = signed(serde_json::json!({
            "schema_version": VERDICT_SCHEMA,
            "source_digest": source()["digest"],
            "source_id": "test-official-goal",
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicate": "goal-predicate", "source_observed_at_ms": 10,
            "satisfied": true,
            "tasks": [{"mission_id": "mission", "task_id": "task",
                "attempts": [{"role_id": "role", "attempt_id": "attempt"}]}]
        }));
        std::fs::write(&path, body.to_string()).expect("verdict written");
        assert!(feed.read_verdict().expect("valid verdict").is_some());
        let mut altered = body.clone();
        altered["satisfied"] = false.into();
        std::fs::write(&path, altered.to_string()).expect("tamper written");
        assert!(feed.read_verdict().is_err());
        let mut duplicate = body.clone();
        duplicate["tasks"][0]["attempts"]
            .as_array_mut()
            .expect("array")
            .push(serde_json::json!({"role_id": "role", "attempt_id": "other"}));
        let duplicate = signed(serde_json::json!({
            "schema_version": duplicate["schema_version"],
            "source_digest": duplicate["source_digest"],
            "source_id": duplicate["source_id"],
            "verifier": duplicate["verifier"],
            "predicate": duplicate["predicate"],
            "source_observed_at_ms": duplicate["source_observed_at_ms"],
            "satisfied": duplicate["satisfied"],
            "tasks": duplicate["tasks"]
        }));
        std::fs::write(&path, duplicate.to_string()).expect("duplicate written");
        assert!(feed.read_verdict().is_err());
        std::fs::write(&path, vec![b' '; MAX_VERIFIER_DOCUMENT_BYTES as usize + 1])
            .expect("oversized document written");
        assert!(feed.read_verdict().is_err());
    }

    /// An exact source is required for verifier plans; ordinary execution reports still work.
    #[test]
    fn plan_admission_requires_actual_supported_predicate() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        let mut plan: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scenarios/e1-shared-world-episode-51/mission-plan.json"
        ))
        .expect("plan fixture");
        let ordinary = orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&ordinary).is_ok());
        plan["tasks"][0]["satisfaction"] = serde_json::json!({
            "expected_effect": "goal-predicate", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": "goal-predicate", "max_evidence_age_ms": 5000
            }
        });
        let supported =
            orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&supported).is_ok());
        plan["tasks"][0]["satisfaction"]["verifier"]["predicate"] = "other".into();
        let unsupported =
            orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&unsupported).is_err());
    }

    /// A final-world verdict cannot be required before a serial Actor's next segment runs.
    #[test]
    fn serial_verifier_task_must_be_last_in_accepted_dag() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        let mut plan: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scenarios/e1-shared-world-episode-51/mission-plan.json"
        ))
        .expect("plan fixture");
        plan["mission"]["actors"] = serde_json::json!([{"id": "shared-robot-a"}]);
        plan["contexts"][1]["roles"][0]["actor"] = "shared-robot-a".into();
        plan["tasks"][1]["satisfaction"] = serde_json::json!({
            "expected_effect": "goal-predicate", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": "goal-predicate", "max_evidence_age_ms": 5000
            }
        });
        let unordered =
            orchestration::decode_mission_plan(&plan.to_string()).expect("same Actor plan decodes");
        assert!(feed.validate_plan(&unordered).is_err());
        plan["tasks"][1]["depends_on"] = serde_json::json!(["task-navigate-goal-a"]);
        let ordered =
            orchestration::decode_mission_plan(&plan.to_string()).expect("ordered plan decodes");
        assert!(feed.validate_plan(&ordered).is_ok());
        plan["tasks"][0]["satisfaction"] = plan["tasks"][1]["satisfaction"].clone();
        let premature = orchestration::decode_mission_plan(&plan.to_string())
            .expect("premature verifier plan decodes");
        assert!(feed.validate_plan(&premature).is_err());
    }

    /// A new wrapper cannot silently replay old verdict receipts under another source.
    #[test]
    fn checkpoint_restore_requires_the_same_verifier_source() {
        let checkpoint = ServerCheckpoint {
            replication_profile: None,
            schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
            integration_json: String::new(),
            orchestration_json: String::new(),
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            safety_stops: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: Some("sha256:source-a".to_string()),
        };
        assert!(validate_restored_verifier_source(&checkpoint, Some("sha256:source-a")).is_ok());
        assert!(validate_restored_verifier_source(&checkpoint, Some("sha256:source-b")).is_err());
        assert!(validate_restored_verifier_source(&checkpoint, None).is_err());
        for schema in [
            PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA,
            PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA,
            PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA,
        ] {
            let mut legacy = checkpoint.clone();
            legacy.schema = schema.into();
            assert!(validate_restored_verifier_source(&legacy, Some("sha256:source-b")).is_err());
        }
        let mut missing = checkpoint;
        missing.verifier_source_digest = None;
        missing
            .verifier_seen
            .insert(("verdict".into(), "mission".into(), "task".into()));
        assert!(validate_restored_verifier_source(&missing, None).is_err());
    }
}
