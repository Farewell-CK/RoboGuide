//! Generated formal gRPC bidirectional streaming contracts for Node Protocol migration.

/// Generated v0.2 protobuf messages and client/server service bindings.
#[allow(clippy::all, clippy::missing_docs_in_private_items, missing_docs)]
pub mod v0_2 {
    /// Exact stream protocol version advertised during Hello negotiation.
    pub const PROTOCOL_VERSION: &str = "roboguide.node-protocol/v0.2";
    /// Exact semantic Node Contract version advertised during Hello negotiation.
    pub const NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.2";

    tonic::include_proto!("roboguide.node.v0_2");
}

/// Generated v0.3 protobuf messages and client/server service bindings.
#[allow(clippy::all, clippy::missing_docs_in_private_items, missing_docs)]
pub mod v0_3 {
    /// Exact stream protocol version advertised during Hello negotiation.
    pub const PROTOCOL_VERSION: &str = "roboguide.node-protocol/v0.3";
    /// Exact semantic Node Contract version advertised during Hello negotiation.
    pub const NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.3";

    tonic::include_proto!("roboguide.node.v0_3");
}

#[cfg(test)]
mod tests {
    use super::v0_2::{
        CanonicalInvocation, Capability, Execute, LocalRuntime, LocalSystemDescriptor,
        NodeRegistration, Resource,
    };
    use prost::Message;

    /// Proves v0.2 preserves multiple local-system owners across protobuf encoding.
    #[test]
    fn registration_round_trip_preserves_multiple_local_systems() {
        let registration = NodeRegistration {
            node_id: "mixed-node".to_string(),
            local_systems: vec![
                LocalSystemDescriptor {
                    id: "motion".to_string(),
                    runtime: Some(LocalRuntime {
                        name: "runtime-a".to_string(),
                        version: "1".to_string(),
                    }),
                    metadata: Default::default(),
                },
                LocalSystemDescriptor {
                    id: "vision".to_string(),
                    runtime: Some(LocalRuntime {
                        name: "runtime-b".to_string(),
                        version: "2".to_string(),
                    }),
                    metadata: Default::default(),
                },
            ],
            capabilities: vec![Capability {
                kind: "mobility".to_string(),
                available: true,
                contracts: vec!["mobility.reach_region@v1".to_string()],
                local_system_id: "motion".to_string(),
            }],
            sensors: vec![],
            resources: vec![Resource {
                id: "camera-front".to_string(),
                kind: "observation".to_string(),
                capacity: 1,
                metadata: Default::default(),
                local_system_id: "vision".to_string(),
            }],
            metadata: Default::default(),
            node_contract_version: super::v0_2::NODE_CONTRACT_VERSION.to_string(),
        };

        let decoded = NodeRegistration::decode(registration.encode_to_vec().as_slice())
            .expect("v0.2 registration decodes");

        assert_eq!(decoded, registration);
        assert_eq!(decoded.local_systems.len(), 2);
        assert_eq!(decoded.capabilities[0].local_system_id, "motion");
        assert_eq!(decoded.resources[0].local_system_id, "vision");
    }

    /// Proves v0.2 Execute transports the Control-committed resource identities.
    #[test]
    fn execute_round_trip_preserves_committed_resource_ids() {
        let execute = Execute {
            session_id: "session-1".to_string(),
            execution_id: "execution-1".to_string(),
            invocation: Some(CanonicalInvocation {
                mission_id: "mission-1".to_string(),
                task_id: "task-1".to_string(),
                group_id: "group-1".to_string(),
                role_id: "carrier".to_string(),
                capability_contract: "mobility.reach_region@v1".to_string(),
                parameters: Default::default(),
            }),
            resource_ids: vec!["body".to_string(), "navigation".to_string()],
        };

        let decoded =
            Execute::decode(execute.encode_to_vec().as_slice()).expect("v0.2 execute decodes");

        assert_eq!(decoded, execute);
        assert_eq!(decoded.resource_ids, ["body", "navigation"]);
    }

    /// Proves v0.3 preserves selective providers and bounded State observations on the wire.
    #[test]
    fn v0_3_round_trip_preserves_state_and_memory_extensions() {
        let registration = super::v0_3::NodeRegistration {
            node_id: "cane-a".to_string(),
            local_systems: vec![super::v0_3::LocalSystemDescriptor {
                id: "safety".to_string(),
                runtime: Some(super::v0_3::LocalRuntime {
                    name: "safety-runtime".to_string(),
                    version: "1".to_string(),
                }),
                metadata: Default::default(),
            }],
            capabilities: Vec::new(),
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: Default::default(),
            node_contract_version: super::v0_3::NODE_CONTRACT_VERSION.to_string(),
            state_exports: vec![super::v0_3::StateExportDescriptor {
                export_id: "hazards".to_string(),
                local_system_id: "safety".to_string(),
                object_class: super::v0_3::StateObjectClass::World as i32,
                object_type: "hazard".to_string(),
                object_id: "crossing-a".to_string(),
                semantic: super::v0_3::StateSemantic::Observed as i32,
                payload_schema: "example.hazard/v1".to_string(),
                valid_for_ms: 1_000,
            }],
            memory_providers: vec![super::v0_3::MemoryProviderDescriptor {
                provider_id: "experience".to_string(),
                local_system_id: "safety".to_string(),
                kind: super::v0_3::MemoryKind::Experience as i32,
                scope: super::v0_3::MemoryScopeKind::Global as i32,
                execution_group_id: String::new(),
                visibility: super::v0_3::MemoryVisibility::Exchangeable as i32,
                payload_schema: "example.experience/v1".to_string(),
                media_type: "application/json".to_string(),
            }],
        };
        let decoded =
            super::v0_3::NodeRegistration::decode(registration.encode_to_vec().as_slice())
                .expect("v0.3 registration decodes");
        assert_eq!(decoded, registration);

        let batch = super::v0_3::StateObservationBatch {
            session_id: "session-1".to_string(),
            sequence: 4,
            observations: vec![super::v0_3::StateObservation {
                export_id: "hazards".to_string(),
                json_value: br#"{"present":true}"#.to_vec(),
                has_source_observed_at: true,
                source_observed_at_ms: 99,
                has_confidence: true,
                confidence_millionths: 800_000,
            }],
        };
        let decoded = super::v0_3::StateObservationBatch::decode(batch.encode_to_vec().as_slice())
            .expect("v0.3 State batch decodes");
        assert_eq!(decoded, batch);
    }
}
