//! Serde-only MissionPlan wire documents.

use super::nullable_millis::NullableMillis;
use serde::{Deserialize, Deserializer};
use std::collections::BTreeMap;

/// Wire MissionPlan accepted by the Phase 1 HTTP boundary.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct PlanDocument {
    /// Exact cross-language contract marker.
    pub(super) schema_version: String,
    /// Mission identity and user-visible objective.
    pub(super) mission: MissionDocument,
    /// Mission Intelligence semantic contexts.
    pub(super) contexts: Vec<ContextDocument>,
    /// Complete Task DAG.
    pub(super) tasks: Vec<TaskDocument>,
}

/// Wire Mission identity and objective.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct MissionDocument {
    /// Stable Mission identity.
    pub(super) id: String,
    /// User-visible outcome.
    pub(super) objective: String,
}

/// Wire semantic context.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ContextDocument {
    /// Stable Context identity.
    pub(super) id: String,
    /// Semantic actor roles.
    pub(super) roles: Vec<ContextRoleDocument>,
    /// Execution-time constraints retained from MissionPlan v0.3.
    #[serde(default)]
    pub(super) relations: Option<Vec<RelationDocument>>,
    /// Optional Context default coupling mode introduced by v0.4.
    #[serde(default)]
    pub(super) coupling_mode: Option<CouplingModeDocument>,
    /// Optional selective Group shared view introduced by v0.4.
    #[serde(default)]
    pub(super) shared_view: Option<SharedViewDocument>,
    /// Optional direct peer channel profile introduced by v0.4.
    #[serde(default)]
    pub(super) peer_channel: Option<PeerChannelDocument>,
}

/// Wire ContextRole-to-Actor declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ContextRoleDocument {
    /// Stable ContextRole identity.
    pub(super) id: String,
    /// Mission actor identity.
    pub(super) actor: String,
}

/// Wire execution-time coordination relation.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RelationDocument {
    /// Stable relation identity within the Mission.
    pub(super) id: String,
    /// Closed relation behavior.
    pub(super) kind: String,
    /// Logical condition-provider endpoint.
    pub(super) source: RelationEndpointDocument,
    /// Logical constrained endpoint.
    pub(super) target: RelationEndpointDocument,
    /// Optional typed state key.
    #[serde(default)]
    pub(super) state_key: Option<String>,
    /// Optional typed spatial reference.
    #[serde(default)]
    pub(super) reference: Option<SpatialReferenceDocument>,
    /// Optional typed coordinate frame.
    #[serde(default)]
    pub(super) frame_id: Option<String>,
    /// Optional state requirement token.
    #[serde(default)]
    pub(super) requirement: Option<RequirementDocument>,
    /// Optional provider-defined freshness policy identity.
    #[serde(default)]
    pub(super) policy_id: Option<String>,
}

/// Wire coupling mode inherited by Task executions.
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(super) enum CouplingModeDocument {
    /// Independent execution.
    Independent,
    /// Sequential handoff execution.
    SequentialHandoff,
    /// Concurrent cooperative execution.
    ConcurrentCooperation,
    /// Tightly coupled cooperative execution.
    TightlyCoupledCooperation,
}

/// Wire selective Group shared view declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SharedViewDocument {
    /// Optional shared map/frame reference.
    #[serde(default)]
    pub(super) spatial_reference: Option<SpatialReferenceDocument>,
    /// Selectively exposed member field/schema bindings.
    #[serde(default)]
    pub(super) bindings: Vec<ViewBindingDocument>,
    /// Whether the view returns Fresh/Stale/Unknown metadata.
    #[serde(default)]
    pub(super) include_freshness: bool,
}

/// Wire typed State binding for a Group member field.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ViewBindingDocument {
    /// Logical Context member owning the export.
    pub(super) context_role_id: String,
    /// Closed semantic field.
    pub(super) field: String,
    /// Exact node-wide State export identity.
    #[serde(default)]
    pub(super) state_export_id: Option<String>,
    /// Exact State payload schema.
    #[serde(default)]
    pub(super) payload_schema: Option<String>,
}

/// Wire map/frame identity.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct SpatialReferenceDocument {
    /// Logical map identity.
    pub(super) map_id: String,
    /// Immutable map revision identity.
    pub(super) revision_id: String,
    /// Common coordinate frame.
    pub(super) frame_id: String,
}

/// Wire direct peer channel profile.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct PeerChannelDocument {
    /// Deployment-resolved channel profile.
    pub(super) profile_id: String,
    /// Versioned peer message schema.
    pub(super) message_schema: String,
}

/// Wire state requirement token.
#[derive(Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(super) enum RequirementDocument {
    /// State must be available.
    Available,
    /// State must be unavailable.
    Unavailable,
}

/// Wire logical Task/Role relation endpoint.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RelationEndpointDocument {
    /// Task containing the endpoint role.
    pub(super) task_id: String,
    /// Role occupying the logical execution slot.
    pub(super) role_id: String,
}

/// Wire Task DAG node.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TaskDocument {
    /// Task identity inside the Mission.
    pub(super) id: String,
    /// Human-readable outcome.
    pub(super) description: String,
    /// Context containing this Task.
    pub(super) context_id: String,
    /// Prerequisite Task identities.
    pub(super) depends_on: Vec<String>,
    /// Role execution requirements.
    pub(super) roles: Vec<RoleDocument>,
    /// Optional Task-level coupling mode override.
    #[serde(default)]
    pub(super) coupling_mode: Option<CouplingModeDocument>,
    /// Relative time constraints introduced by MissionPlan v0.5.
    #[serde(default)]
    pub(super) timing: Option<TimingDocument>,
}

/// Wire relative scheduling constraints anchored to Mission acceptance.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TimingDocument {
    /// Earliest permitted start offset.
    pub(super) earliest_start_offset_ms: u64,
    /// Latest permitted start offset.
    pub(super) latest_start_offset_ms: NullableMillis,
    /// Optional completion deadline offset.
    pub(super) completion_deadline_offset_ms: NullableMillis,
    /// Optional planning duration.
    pub(super) estimated_duration_ms: NullableMillis,
}

/// Wire Task role requirement and continuity declaration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RoleDocument {
    /// Task-local role identity.
    pub(super) id: String,
    /// Mission actor identity.
    pub(super) actor: String,
    /// Canonical coarse capability kind.
    pub(super) capability: CapabilityDocument,
    /// Exact capability contract.
    pub(super) contract: ContractDocument,
    /// Optional exclusive resource category.
    #[serde(default, deserialize_with = "resource_kind_field")]
    pub(super) resource_kind: ResourceKindField,
    /// Quantitative resource requirements introduced by MissionPlan v0.5.
    #[serde(default)]
    pub(super) resources: Option<Vec<ResourceRequirementDocument>>,
    /// Canonical execution operation.
    pub(super) execution: IntentDocument,
    /// Optional ContextRole identity.
    pub(super) context_role: Option<String>,
    /// Resource lifetime for this role.
    pub(super) resource_scope: ScopeDocument,
}

/// Presence-aware legacy resource field so v0.5 rejects even an explicit null value.
#[derive(Default)]
pub(super) enum ResourceKindField {
    /// The JSON object omitted the legacy field.
    #[default]
    Missing,
    /// The JSON object supplied the legacy nullable field.
    Present(Option<ResourceDocument>),
}

/// Decodes one present legacy resource field while Serde default handles absence.
pub(super) fn resource_kind_field<'de, D>(deserializer: D) -> Result<ResourceKindField, D::Error>
where
    D: Deserializer<'de>,
{
    Option::<ResourceDocument>::deserialize(deserializer).map(ResourceKindField::Present)
}

/// Wire quantitative resource demand for one Role.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ResourceRequirementDocument {
    /// Required resource category.
    pub(super) kind: ResourceDocument,
    /// Minimum declared capacity.
    pub(super) units: u32,
}

/// Wire exact capability contract reference.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ContractDocument {
    /// Contract namespace.
    pub(super) namespace: String,
    /// Contract operation name.
    pub(super) name: String,
    /// Contract version.
    pub(super) version: String,
}

/// Wire canonical execution intent.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct IntentDocument {
    /// Exact capability contract invoked by this intent.
    pub(super) capability_contract: ContractDocument,
    /// Transport-neutral scalar parameters.
    pub(super) parameters: BTreeMap<String, serde_json::Value>,
}

/// Supported capability kinds in MissionPlan v0.2.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum CapabilityDocument {
    /// Navigation or locomotion.
    Mobility,
    /// Payload transport.
    Transport,
    /// General computation.
    Compute,
    /// Sensing or verification.
    Observation,
}

/// Supported resource categories in MissionPlan v0.2.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum ResourceDocument {
    /// Exclusive physical space.
    Space,
    /// Exclusive compute capacity.
    Compute,
    /// Exclusive time interval.
    Time,
}

/// Supported role resource lifetimes.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum ScopeDocument {
    /// Release after Task terminal handling.
    Task,
    /// Retain until the containing Context ends.
    Context,
}
