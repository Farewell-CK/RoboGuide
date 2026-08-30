//! Strong, transport-neutral evidence for one completed map localization verification.

use crate::{
    ContentDigest, DomainError, ExecutionGroupId, MapArtifactRef, MapId, MapRevisionId,
    MapRevisionSelector, MissionId, NodeId, RoleId, SpatialAnchorId, TaskId, TaskRef, TimestampMs,
};
use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// Version identifier for strong localization verification evidence.
pub const LOCALIZATION_EVIDENCE_SCHEMA_V0_1: &str =
    "roboguide.localization-verification-evidence/v0.1";

/// Comparison applied to one adapter-reported pose-quality metric.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PoseQualityComparison {
    /// The observed value must be no greater than the threshold.
    AtMost,
    /// The observed value must be no less than the threshold.
    AtLeast,
}

/// One canonical pose-quality result with an explicit acceptance threshold.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "PoseQualityWire", deny_unknown_fields)]
pub struct PoseQualityEvidence {
    /// Deployment-mapped metric name.
    metric: String,
    /// Finite decimal observation encoded as text for deterministic equality.
    value: String,
    /// Finite decimal acceptance threshold encoded as text.
    threshold: String,
    /// Unit shared by the value and threshold.
    unit: String,
    /// Direction of the threshold comparison.
    comparison: PoseQualityComparison,
}

/// Unvalidated wire fields used to enforce the pose-quality constructor on decode.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct PoseQualityWire {
    /// Deployment-mapped metric name.
    metric: String,
    /// Decimal observation.
    value: String,
    /// Decimal threshold.
    threshold: String,
    /// Shared unit.
    unit: String,
    /// Threshold direction.
    comparison: PoseQualityComparison,
}

impl TryFrom<PoseQualityWire> for PoseQualityEvidence {
    type Error = DomainError;

    /// Validates decoded pose-quality fields through the public constructor.
    fn try_from(value: PoseQualityWire) -> Result<Self, Self::Error> {
        Self::new(
            value.metric,
            value.value,
            value.threshold,
            value.unit,
            value.comparison,
        )
    }
}

impl PoseQualityEvidence {
    /// Creates a validated pose-quality observation.
    pub fn new(
        metric: impl Into<String>,
        value: impl Into<String>,
        threshold: impl Into<String>,
        unit: impl Into<String>,
        comparison: PoseQualityComparison,
    ) -> Result<Self, DomainError> {
        let metric = required_text(metric, "pose-quality metric")?;
        let value = finite_decimal(value, "pose-quality value")?;
        let threshold = finite_decimal(threshold, "pose-quality threshold")?;
        let unit = required_text(unit, "pose-quality unit")?;
        Ok(Self {
            metric,
            value,
            threshold,
            unit,
            comparison,
        })
    }

    /// Returns the canonical metric name.
    pub fn metric(&self) -> &str {
        &self.metric
    }

    /// Returns the exact observed decimal spelling.
    pub fn value(&self) -> &str {
        &self.value
    }

    /// Returns the exact threshold decimal spelling.
    pub fn threshold(&self) -> &str {
        &self.threshold
    }

    /// Returns the shared metric unit.
    pub fn unit(&self) -> &str {
        &self.unit
    }

    /// Returns the required comparison direction.
    pub const fn comparison(&self) -> PoseQualityComparison {
        self.comparison
    }

    /// Returns whether the reported metric satisfies its declared threshold.
    pub fn passes(&self) -> bool {
        let value = self
            .value
            .parse::<f64>()
            .expect("validated pose-quality value remains finite");
        let threshold = self
            .threshold
            .parse::<f64>()
            .expect("validated pose-quality threshold remains finite");
        match self.comparison {
            PoseQualityComparison::AtMost => value <= threshold,
            PoseQualityComparison::AtLeast => value >= threshold,
        }
    }
}

/// Coordinate frames asserted by one localization verification.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "LocalizationFramesWire", deny_unknown_fields)]
pub struct LocalizationFrames {
    /// Map frame associated with the selected artifact and anchor.
    map: String,
    /// Local odometry frame used by the localization runtime.
    odom: String,
    /// Robot base frame whose pose quality was observed.
    base: String,
}

/// Unvalidated wire fields used to enforce frame validation on decode.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalizationFramesWire {
    /// Map frame.
    map: String,
    /// Odometry frame.
    odom: String,
    /// Base frame.
    base: String,
}

impl TryFrom<LocalizationFramesWire> for LocalizationFrames {
    type Error = DomainError;

    /// Validates decoded frame fields through the public constructor.
    fn try_from(value: LocalizationFramesWire) -> Result<Self, Self::Error> {
        Self::new(value.map, value.odom, value.base)
    }
}

impl LocalizationFrames {
    /// Creates a nonblank map/odom/base frame relation.
    pub fn new(
        map: impl Into<String>,
        odom: impl Into<String>,
        base: impl Into<String>,
    ) -> Result<Self, DomainError> {
        Ok(Self {
            map: required_text(map, "map frame")?,
            odom: required_text(odom, "odom frame")?,
            base: required_text(base, "base frame")?,
        })
    }

    /// Returns the map frame.
    pub fn map(&self) -> &str {
        &self.map
    }

    /// Returns the odometry frame.
    pub fn odom(&self) -> &str {
        &self.odom
    }

    /// Returns the robot base frame.
    pub fn base(&self) -> &str {
        &self.base
    }
}

/// Complete strong evidence for one exact localization verification attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalizationVerificationEvidence {
    /// Immutable map artifact whose bytes were staged locally.
    artifact: MapArtifactRef,
    /// Mission requesting verification.
    mission_id: MissionId,
    /// Mission-scoped Task that performed verification.
    task_ref: TaskRef,
    /// Mission-level Execution Group carrying the TaskExecution.
    group_id: ExecutionGroupId,
    /// Committed role whose execution produced the evidence.
    role_id: RoleId,
    /// Physical node reporting the local fact.
    node_id: NodeId,
    /// Stable RoboGuide logical execution identity.
    execution_id: String,
    /// Durable node-local physical execution handle.
    local_attempt_id: String,
    /// Active Local EAIOS map identity observed after load.
    active_local_map_id: String,
    /// Canonical execution mode; v0.1 accepts only `localization`.
    mode: String,
    /// Pose-quality result and explicit threshold.
    pose_quality: PoseQualityEvidence,
    /// Observed map/odom/base frame relation.
    frames: LocalizationFrames,
    /// Fixed spatial anchor associated with the artifact.
    anchor_id: SpatialAnchorId,
    /// Source-local observation time, never compared across nodes.
    source_observed_at: TimestampMs,
}

impl LocalizationVerificationEvidence {
    /// Creates and validates a complete strong verification result.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        artifact: MapArtifactRef,
        mission_id: MissionId,
        task_ref: TaskRef,
        group_id: ExecutionGroupId,
        role_id: RoleId,
        node_id: NodeId,
        execution_id: impl Into<String>,
        local_attempt_id: impl Into<String>,
        active_local_map_id: impl Into<String>,
        mode: impl Into<String>,
        pose_quality: PoseQualityEvidence,
        frames: LocalizationFrames,
        anchor_id: SpatialAnchorId,
        source_observed_at: TimestampMs,
    ) -> Result<Self, DomainError> {
        if task_ref.mission_id() != &mission_id {
            return Err(invalid(
                "localization evidence Task belongs to another Mission",
            ));
        }
        for (value, field) in [
            (mission_id.as_str(), "mission identity"),
            (task_ref.task_id().as_str(), "task identity"),
            (group_id.as_str(), "execution group identity"),
            (role_id.as_str(), "role identity"),
            (node_id.as_str(), "node identity"),
            (anchor_id.as_str(), "spatial anchor identity"),
        ] {
            required_text(value, field)?;
        }
        let mode = required_text(mode, "localization mode")?;
        if mode != "localization" {
            return Err(invalid("localization evidence mode must be `localization`"));
        }
        if !pose_quality.passes() {
            return Err(invalid(
                "localization pose-quality observation does not satisfy its threshold",
            ));
        }
        Ok(Self {
            artifact,
            mission_id,
            task_ref,
            group_id,
            role_id,
            node_id,
            execution_id: required_text(execution_id, "execution identity")?,
            local_attempt_id: required_text(local_attempt_id, "local attempt identity")?,
            active_local_map_id: required_text(active_local_map_id, "active local map identity")?,
            mode,
            pose_quality,
            frames,
            anchor_id,
            source_observed_at,
        })
    }

    /// Returns the exact immutable map artifact.
    pub const fn artifact(&self) -> &MapArtifactRef {
        &self.artifact
    }

    /// Returns the Mission identity.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the Mission-scoped Task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the Mission-level Execution Group identity.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the committed role identity.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the reporting node identity.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the stable RoboGuide execution identity.
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }

    /// Returns the node-local physical attempt identity.
    pub fn local_attempt_id(&self) -> &str {
        &self.local_attempt_id
    }

    /// Returns the active Local EAIOS map identity.
    pub fn active_local_map_id(&self) -> &str {
        &self.active_local_map_id
    }

    /// Returns the canonical localization mode.
    pub fn mode(&self) -> &str {
        &self.mode
    }

    /// Returns the pose-quality result.
    pub const fn pose_quality(&self) -> &PoseQualityEvidence {
        &self.pose_quality
    }

    /// Returns the observed frame relation.
    pub const fn frames(&self) -> &LocalizationFrames {
        &self.frames
    }

    /// Returns the fixed spatial anchor.
    pub const fn anchor_id(&self) -> &SpatialAnchorId {
        &self.anchor_id
    }

    /// Returns the source-local observation time.
    pub const fn source_observed_at(&self) -> TimestampMs {
        self.source_observed_at
    }
}

/// Strict wire representation of localization evidence v0.1.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LocalizationEvidenceWire {
    /// Evidence schema identity.
    schema: String,
    /// Logical map identity.
    map_id: String,
    /// Immutable map revision identity.
    revision_id: String,
    /// Canonical SHA-256 content digest.
    content_digest: String,
    /// Exact artifact byte count.
    byte_size: u64,
    /// Mission identity.
    mission_id: String,
    /// Task identity within the Mission.
    task_id: String,
    /// Mission-level Group identity.
    group_id: String,
    /// Committed role identity.
    role_id: String,
    /// Reporting node identity.
    node_id: String,
    /// Stable RoboGuide execution identity.
    execution_id: String,
    /// Durable node-local execution handle.
    local_attempt_id: String,
    /// Active Local EAIOS map identity.
    active_local_map_id: String,
    /// Canonical `localization` mode.
    mode: String,
    /// Pose-quality result.
    pose_quality: PoseQualityEvidence,
    /// Observed coordinate frames.
    frames: LocalizationFrames,
    /// Fixed spatial anchor identity.
    anchor_id: String,
    /// Source-local observation time.
    source_observed_at_ms: u64,
}

impl Serialize for LocalizationVerificationEvidence {
    /// Serializes evidence into the strict cross-language v0.1 envelope.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        LocalizationEvidenceWire {
            schema: LOCALIZATION_EVIDENCE_SCHEMA_V0_1.to_string(),
            map_id: self.artifact.selector().map_id().as_str().to_string(),
            revision_id: self.artifact.selector().revision_id().as_str().to_string(),
            content_digest: self.artifact.content_digest().as_str().to_string(),
            byte_size: self.artifact.byte_size(),
            mission_id: self.mission_id.as_str().to_string(),
            task_id: self.task_ref.task_id().as_str().to_string(),
            group_id: self.group_id.as_str().to_string(),
            role_id: self.role_id.as_str().to_string(),
            node_id: self.node_id.as_str().to_string(),
            execution_id: self.execution_id.clone(),
            local_attempt_id: self.local_attempt_id.clone(),
            active_local_map_id: self.active_local_map_id.clone(),
            mode: self.mode.clone(),
            pose_quality: self.pose_quality.clone(),
            frames: self.frames.clone(),
            anchor_id: self.anchor_id.as_str().to_string(),
            source_observed_at_ms: self.source_observed_at.as_millis(),
        }
        .serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for LocalizationVerificationEvidence {
    /// Deserializes and validates the strict cross-language v0.1 envelope.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let wire = LocalizationEvidenceWire::deserialize(deserializer)?;
        if wire.schema != LOCALIZATION_EVIDENCE_SCHEMA_V0_1 {
            return Err(serde::de::Error::custom(
                "unsupported localization evidence schema",
            ));
        }
        let mission_id = MissionId::new(wire.mission_id).map_err(serde::de::Error::custom)?;
        LocalizationVerificationEvidence::new(
            MapArtifactRef::new(
                MapRevisionSelector::new(
                    MapId::new(wire.map_id).map_err(serde::de::Error::custom)?,
                    MapRevisionId::new(wire.revision_id).map_err(serde::de::Error::custom)?,
                ),
                ContentDigest::new(wire.content_digest).map_err(serde::de::Error::custom)?,
                wire.byte_size,
            ),
            mission_id.clone(),
            TaskRef::new(
                mission_id,
                TaskId::new(wire.task_id).map_err(serde::de::Error::custom)?,
            ),
            ExecutionGroupId::new(wire.group_id).map_err(serde::de::Error::custom)?,
            RoleId::new(wire.role_id).map_err(serde::de::Error::custom)?,
            NodeId::new(wire.node_id).map_err(serde::de::Error::custom)?,
            wire.execution_id,
            wire.local_attempt_id,
            wire.active_local_map_id,
            wire.mode,
            PoseQualityEvidence::new(
                wire.pose_quality.metric,
                wire.pose_quality.value,
                wire.pose_quality.threshold,
                wire.pose_quality.unit,
                wire.pose_quality.comparison,
            )
            .map_err(serde::de::Error::custom)?,
            LocalizationFrames::new(wire.frames.map, wire.frames.odom, wire.frames.base)
                .map_err(serde::de::Error::custom)?,
            SpatialAnchorId::new(wire.anchor_id).map_err(serde::de::Error::custom)?,
            TimestampMs::new(wire.source_observed_at_ms),
        )
        .map_err(serde::de::Error::custom)
    }
}

/// Validates one required text field without silently trimming identity-bearing evidence.
fn required_text(value: impl Into<String>, field: &'static str) -> Result<String, DomainError> {
    let value = value.into();
    if value.trim().is_empty() || value.trim() != value {
        return Err(invalid(format!(
            "{field} must be nonblank without surrounding whitespace"
        )));
    }
    Ok(value)
}

/// Validates one finite decimal string while preserving its exact source spelling.
fn finite_decimal(value: impl Into<String>, field: &'static str) -> Result<String, DomainError> {
    let value = required_text(value, field)?;
    if !value.parse::<f64>().is_ok_and(f64::is_finite) {
        return Err(invalid(format!("{field} must be a finite decimal")));
    }
    Ok(value)
}

/// Creates a spatial-memory validation error for malformed evidence.
fn invalid(reason: impl Into<String>) -> DomainError {
    DomainError::InvalidSpatialMemory {
        reason: reason.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds one complete passing evidence fixture.
    fn evidence() -> LocalizationVerificationEvidence {
        let mission_id = MissionId::new("mission-localize").expect("mission id is valid");
        LocalizationVerificationEvidence::new(
            MapArtifactRef::new(
                MapRevisionSelector::new(
                    MapId::new("lab-map").expect("map id is valid"),
                    MapRevisionId::new("r1").expect("revision id is valid"),
                ),
                ContentDigest::new("a".repeat(64)).expect("digest is valid"),
                42,
            ),
            mission_id.clone(),
            TaskRef::new(
                mission_id,
                TaskId::new("verify-map").expect("task id is valid"),
            ),
            ExecutionGroupId::new("group-localize").expect("group id is valid"),
            RoleId::new("localizer").expect("role id is valid"),
            NodeId::new("dog-b").expect("node id is valid"),
            "execution-localize",
            "local-attempt-1",
            "lab-map-local",
            "localization",
            PoseQualityEvidence::new(
                "translation_stddev",
                "0.08",
                "0.10",
                "m",
                PoseQualityComparison::AtMost,
            )
            .expect("quality is valid"),
            LocalizationFrames::new("map", "odom", "base_link").expect("frames are valid"),
            SpatialAnchorId::new("anchor-lab").expect("anchor is valid"),
            TimestampMs::new(123),
        )
        .expect("evidence is valid")
    }

    /// Strong evidence round-trips through its strict cross-language envelope.
    #[test]
    fn localization_evidence_round_trips() {
        let evidence = evidence();
        let encoded = serde_json::to_value(&evidence).expect("evidence serializes");
        assert_eq!(encoded["schema"], LOCALIZATION_EVIDENCE_SCHEMA_V0_1);
        assert_eq!(encoded["active_local_map_id"], "lab-map-local");
        let decoded: LocalizationVerificationEvidence =
            serde_json::from_value(encoded).expect("evidence deserializes");
        assert_eq!(decoded, evidence);
    }

    /// Missing fields, failed quality thresholds, and smoke-only facts fail closed.
    #[test]
    fn localization_evidence_rejects_incomplete_or_failed_proof() {
        let mut incomplete = serde_json::to_value(evidence()).expect("evidence serializes");
        incomplete
            .as_object_mut()
            .expect("evidence is an object")
            .remove("active_local_map_id");
        assert!(serde_json::from_value::<LocalizationVerificationEvidence>(incomplete).is_err());

        let failed = PoseQualityEvidence::new(
            "translation_stddev",
            "0.11",
            "0.10",
            "m",
            PoseQualityComparison::AtMost,
        )
        .expect("finite quality values parse");
        assert!(!failed.passes());

        let mut invalid_identity = serde_json::to_value(evidence()).expect("evidence serializes");
        invalid_identity["group_id"] = serde_json::Value::String(" group-localize".to_string());
        assert!(
            serde_json::from_value::<LocalizationVerificationEvidence>(invalid_identity).is_err()
        );
    }

    /// Checked-in schema required fields stay aligned with the domain wire envelope.
    #[test]
    fn localization_evidence_schema_matches_wire_fields() {
        let schema: serde_json::Value = serde_json::from_str(include_str!(
            "../../../contracts/spatial/localization-evidence-v0.1/localization-verification-evidence.schema.json"
        ))
        .expect("checked-in evidence schema is valid JSON");
        let required = schema["required"]
            .as_array()
            .expect("schema required fields are an array")
            .iter()
            .map(|value| value.as_str().expect("required field is text"))
            .collect::<std::collections::BTreeSet<_>>();
        let encoded = serde_json::to_value(evidence()).expect("evidence serializes");
        let fields = encoded
            .as_object()
            .expect("evidence is an object")
            .keys()
            .map(String::as_str)
            .collect::<std::collections::BTreeSet<_>>();

        assert_eq!(required, fields);
        assert_eq!(
            schema["properties"]["schema"]["const"],
            LOCALIZATION_EVIDENCE_SCHEMA_V0_1
        );
    }
}
