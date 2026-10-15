//! Explicit scalar and conservative Gaussian Belief derivation retaining original evidence identities.

use crate::{GaussianEstimate, covariance_intersection};
use domain::{
    BeliefAlgorithm, BeliefPolicy, BeliefScalarKind, ConflictReducer, DecisionKind,
    EvidenceDisposition, StateRecord, StateSemantic, StateSource, TimestampMs,
};
use sha2::{Digest, Sha256};

/// Derives a changed named Belief only; duplicates never receive a new freshness interval or sequence.
pub fn derive_belief(
    policy: &BeliefPolicy,
    records: &[StateRecord],
    previous: Option<&StateRecord>,
    now: TimestampMs,
) -> Result<Option<StateRecord>, String> {
    let mut admitted = records
        .iter()
        .filter(|record| {
            policy
                .admission
                .evaluate(
                    std::slice::from_ref(*record),
                    DecisionKind::BeliefFusion,
                    now,
                )
                .disposition
                == EvidenceDisposition::Accepted
        })
        .collect::<Vec<_>>();
    admitted.sort_by_key(|record| record.key());
    let independent_sources = admitted
        .iter()
        .map(|record| record.key().source())
        .collect::<std::collections::BTreeSet<_>>()
        .len();
    let mut status = if !policy.enabled {
        "disabled"
    } else if independent_sources < usize::from(policy.minimum_sources) {
        "unavailable"
    } else if independent_sources != admitted.len() {
        "conflict_or_invalid_schema"
    } else {
        "accepted"
    };
    let mut value = serde_json::Value::Null;
    let mut weights = Vec::new();
    if status == "accepted" {
        if admitted.len() > 8
            || admitted.iter().any(|record| {
                serde_json::to_vec(record).map_or(true, |encoded| encoded.len() > 16 * 1024)
            })
        {
            status = "budget_exceeded";
        } else {
            match derive_value(policy, &admitted) {
                Ok((derived, used_weights)) => {
                    value = derived;
                    weights = used_weights;
                }
                Err(_) => {
                    status = "conflict_or_invalid_schema";
                }
            }
        }
    }
    let inputs = admitted.iter().map(|record| Ok(serde_json::json!({
        "key":record.key(), "record_digest":digest(*record)?, "received_at_ms":record.received_at().as_millis(),
        "source_epoch":record.source_epoch(), "sequence":record.sequence(), "valid_for_ms":record.valid_for_ms(),
    }))).collect::<Result<Vec<_>,String>>()?;
    let mut payload = serde_json::json!({"status":status,"value":value,"weights":weights,
        "policy_id":policy.policy_id,"policy_revision":policy.revision,"policy_digest":digest(policy)?,"inputs":inputs});
    let derivation_digest = digest(&payload)?;
    if previous.is_some_and(|old| {
        old.value()
            .get("derivation_digest")
            .and_then(serde_json::Value::as_str)
            == Some(&derivation_digest)
    }) {
        return Ok(None);
    }
    payload["derivation_digest"] = serde_json::json!(derivation_digest);
    let valid_for_ms = if status == "accepted" {
        admitted
            .iter()
            .map(|record| {
                record
                    .received_at()
                    .as_millis()
                    .saturating_add(record.valid_for_ms().min(policy.admission.max_age_ms))
            })
            .min()
            .unwrap_or(now.as_millis())
            .checked_sub(now.as_millis())
            .filter(|remaining| *remaining > 0)
            .ok_or("Belief inputs have no remaining original validity")?
    } else {
        1
    };
    let confidence = if status == "accepted" {
        admitted
            .iter()
            .map(|record| record.confidence_millionths())
            .collect::<Option<Vec<_>>>()
            .and_then(|scores| scores.into_iter().min())
    } else {
        None
    };
    let sequence = previous.map_or(Ok(1), |record| {
        record
            .sequence()
            .checked_add(1)
            .ok_or("Belief sequence exhausted")
    })?;
    StateRecord::new(
        policy.admission.object.clone(),
        StateSemantic::Belief,
        StateSource::RoboGuide {
            component: format!("belief/{}/{}", policy.tenant_id, policy.policy_id),
        },
        "belief",
        "roboguide.belief/v0.1",
        payload,
        None,
        now,
        valid_for_ms,
        confidence,
        sequence,
    )
    .map(Some)
    .map_err(|error| error.to_string())
}

/// Computes the declared value while treating invalid/missing covariance, units and scalar types as unknown.
fn derive_value(
    policy: &BeliefPolicy,
    records: &[&StateRecord],
) -> Result<(serde_json::Value, Vec<f64>), String> {
    match &policy.algorithm {
        BeliefAlgorithm::Scalar {
            value_kind,
            pointer,
        } => {
            let values = records
                .iter()
                .map(|record| {
                    record
                        .value()
                        .pointer(pointer)
                        .ok_or("Belief scalar pointer is absent")
                })
                .collect::<Result<Vec<_>, _>>()?;
            if values.iter().any(|value| match value_kind {
                BeliefScalarKind::Boolean => !value.is_boolean(),
                BeliefScalarKind::Enum => value
                    .as_str()
                    .is_none_or(|text| text.trim().is_empty() || text.len() > 128),
            }) {
                return Err("Belief scalar type is invalid".into());
            }
            let selected = match policy.admission.reducer {
                ConflictReducer::RequireEquivalent => {
                    if values.iter().any(|value| *value != values[0]) {
                        return Err("independent scalar sources conflict".into());
                    }
                    0
                }
                ConflictReducer::HighestConfidence => {
                    let highest = records
                        .iter()
                        .map(|record| record.confidence_millionths().unwrap_or(0))
                        .max()
                        .unwrap_or(0);
                    let selected = records
                        .iter()
                        .position(|record| record.confidence_millionths().unwrap_or(0) == highest)
                        .ok_or("no scalar source")?;
                    if records.iter().enumerate().any(|(index, record)| {
                        record.confidence_millionths().unwrap_or(0) == highest
                            && values[index] != values[selected]
                    }) {
                        return Err("equal-confidence scalar sources conflict".into());
                    }
                    selected
                }
            };
            Ok((values[selected].clone(), Vec::new()))
        }
        BeliefAlgorithm::GaussianCi {
            unit,
            frame,
            dimensions,
            weight_steps,
            max_separation_squared_millionths,
        } => {
            let estimates = records
                .iter()
                .map(|record| {
                    serde_json::from_value::<GaussianEstimate>(record.value().clone())
                        .map_err(|error| error.to_string())
                })
                .collect::<Result<Vec<_>, _>>()?;
            for estimate in &estimates {
                if estimate.mean.len() != usize::from(*dimensions)
                    || &estimate.unit != unit
                    || &estimate.frame != frame
                {
                    return Err("Gaussian source frame/unit/dimension conflict".into());
                }
            }
            let limit = *max_separation_squared_millionths as f64 / 1_000_000.0;
            for (index, left) in estimates.iter().enumerate() {
                for right in &estimates[index + 1..] {
                    let separation = left
                        .mean
                        .iter()
                        .zip(&right.mean)
                        .map(|(a, b)| (a - b) * (a - b))
                        .sum::<f64>();
                    if !separation.is_finite() || separation > limit {
                        return Err("Gaussian disagreement exceeds explicit policy bound".into());
                    }
                }
            }
            let result = covariance_intersection(&estimates, *weight_steps)?;
            Ok((
                serde_json::to_value(result.estimate).map_err(|error| error.to_string())?,
                result.weights,
            ))
        }
    }
}

/// Computes exact original interpretation/input identity without embedding raw payloads in derived State.
fn digest(value: &impl serde::Serialize) -> Result<String, String> {
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(&serde_json::to_value(value).map_err(|error| error.to_string())?)
                .map_err(|error| error.to_string())?
        )
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{DecisionEvidencePolicy, StateObjectClass, StateObjectRef};
    use std::collections::BTreeSet;

    /// Names two software observations and a configurable conservative consumer, without physical claims.
    fn policy() -> BeliefPolicy {
        let mut admission = DecisionEvidencePolicy::new(
            "fusion",
            1,
            StateObjectRef::new(
                StateObjectClass::World,
                "software-condition",
                "observed-object",
            )
            .unwrap(),
            "measurements",
            BTreeSet::from([StateSemantic::Observed]),
            ["source-a", "source-b"].into_iter().map(source).collect(),
            20,
            None,
            BTreeSet::from([DecisionKind::BeliefFusion]),
            ConflictReducer::RequireEquivalent,
        )
        .unwrap();
        admission.payload_schema = Some("software-observation/v1".into());
        BeliefPolicy {
            policy_id: "fusion".into(),
            revision: 1,
            tenant_id: "local".into(),
            enabled: true,
            minimum_sources: 2,
            admission,
            algorithm: BeliefAlgorithm::Scalar {
                value_kind: BeliefScalarKind::Boolean,
                pointer: "/clear".into(),
            },
        }
    }

    /// Retains an independently named raw source whose payload never chooses the fusion policy.
    fn source(id: &str) -> StateSource {
        StateSource::Node {
            node_id: domain::NodeId::new(id).unwrap(),
            local_system_id: domain::LocalSystemId::new("software-observer").unwrap(),
        }
    }

    /// Creates a source-authored mathematical observation with an original receive time and TTL.
    fn observed(id: &str, value: serde_json::Value, at: u64) -> StateRecord {
        let policy = policy();
        StateRecord::new(
            policy.admission.object,
            StateSemantic::Observed,
            source(id),
            "measurements",
            "software-observation/v1",
            value,
            None,
            TimestampMs::new(at),
            20,
            Some(900_000),
            1,
        )
        .unwrap()
    }

    /// Equivalent sources preserve original expiration and raw evidence; repetition cannot refresh a Belief.
    #[test]
    fn scalar_sources_do_not_refresh_freshness_or_replace_raw_evidence() {
        let raw = vec![
            observed("source-a", serde_json::json!({"clear":true}), 2),
            observed("source-b", serde_json::json!({"clear":true}), 4),
        ];
        let first = derive_belief(&policy(), &raw, None, TimestampMs::new(5))
            .unwrap()
            .unwrap();
        assert_eq!(first.valid_for_ms(), 17);
        assert_eq!(first.value()["value"], true);
        assert!(
            derive_belief(&policy(), &raw, Some(&first), TimestampMs::new(10))
                .unwrap()
                .is_none()
        );
        assert_eq!(first.value()["inputs"][0]["received_at_ms"], 2);
        assert_eq!(raw[0].value(), &serde_json::json!({"clear":true}));
        let expired = derive_belief(&policy(), &raw, Some(&first), TimestampMs::new(22))
            .unwrap()
            .unwrap();
        assert_eq!(expired.value()["status"], "unavailable");
        assert!(expired.value()["value"].is_null());
    }

    /// Conflicting, missing and incorrectly typed evidence cannot become an affirmative safety Boolean.
    #[test]
    fn source_conflict_unknown_and_scalar_coercion_are_explicit() {
        let positive = observed("source-a", serde_json::json!({"clear":true}), 2);
        let negative = observed("source-b", serde_json::json!({"clear":false}), 2);
        let conflict = derive_belief(
            &policy(),
            &[positive.clone(), negative],
            None,
            TimestampMs::new(3),
        )
        .unwrap()
        .unwrap();
        assert_eq!(conflict.value()["status"], "conflict_or_invalid_schema");
        let missing = derive_belief(
            &policy(),
            std::slice::from_ref(&positive),
            None,
            TimestampMs::new(3),
        )
        .unwrap()
        .unwrap();
        assert_eq!(missing.value()["status"], "unavailable");
        let duplicate = derive_belief(
            &policy(),
            &[positive.clone(), positive.clone()],
            None,
            TimestampMs::new(3),
        )
        .unwrap()
        .unwrap();
        assert_eq!(duplicate.value()["status"], "unavailable");
        let malformed = observed("source-b", serde_json::json!({"clear":"true"}), 2);
        let invalid = derive_belief(&policy(), &[positive, malformed], None, TimestampMs::new(3))
            .unwrap()
            .unwrap();
        assert_eq!(invalid.value()["status"], "conflict_or_invalid_schema");
    }

    /// Actual covariance and frame fields, rather than scalar confidence, determine conservative numeric fusion.
    #[test]
    fn gaussian_policy_uses_explicit_covariance_and_rejects_missing_measurements() {
        let mut policy = policy();
        policy.algorithm = BeliefAlgorithm::GaussianCi {
            unit: "m".into(),
            frame: "survey".into(),
            dimensions: 2,
            weight_steps: 32,
            max_separation_squared_millionths: 20_000_000,
        };
        let first = observed(
            "source-a",
            serde_json::json!({"mean":[0.0,0.0],"covariance":[[1.0,0.0],[0.0,9.0]],"unit":"m","frame":"survey"}),
            2,
        );
        let second = observed(
            "source-b",
            serde_json::json!({"mean":[2.0,2.0],"covariance":[[9.0,0.0],[0.0,1.0]],"unit":"m","frame":"survey"}),
            2,
        );
        let fused = derive_belief(&policy, &[first.clone(), second], None, TimestampMs::new(3))
            .unwrap()
            .unwrap();
        assert_eq!(fused.value()["status"], "accepted");
        assert!((fused.value()["value"]["covariance"][0][0].as_f64().unwrap() - 1.8).abs() < 1e-12);
        let missing = observed(
            "source-b",
            serde_json::json!({"mean":[2.0,2.0],"unit":"m","frame":"survey"}),
            2,
        );
        let unavailable = derive_belief(&policy, &[first, missing], None, TimestampMs::new(3))
            .unwrap()
            .unwrap();
        assert_eq!(unavailable.value()["status"], "conflict_or_invalid_schema");
    }
}
