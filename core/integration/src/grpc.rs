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

/// Generated v0.4 protobuf messages and client/server service bindings.
#[allow(clippy::all, clippy::missing_docs_in_private_items, missing_docs)]
pub mod v0_4 {
    /// Exact stream protocol version advertised during Hello negotiation.
    pub const PROTOCOL_VERSION: &str = "roboguide.node-protocol/v0.4";
    /// Current semantic Node Contract version advertised during Hello negotiation.
    pub const NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.6";
    /// Prior profile-and-intent contract retained for explicit session compatibility.
    pub const PREVIOUS_NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.5";
    /// Legacy combined-declaration contract retained for explicit session compatibility.
    pub const LEGACY_NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.4";

    tonic::include_proto!("roboguide.node.v0_4");
}

#[cfg(test)]
mod tests {
    use super::v0_2::{
        CanonicalInvocation, Capability, Execute, LocalRuntime, LocalSystemDescriptor,
        NodeRegistration, Resource,
    };
    use prost::Message;

    /// Current additive wire fields preserve profiles, operation support, and semantic objectives.
    #[test]
    fn v0_5_semantics_round_trip_without_legacy_fields() {
        use super::v0_4::scalar_value::Value;

        let registration = super::v0_4::NodeRegistration {
            node_id: "arm-a".to_string(),
            node_contract_version: super::v0_4::NODE_CONTRACT_VERSION.to_string(),
            capability_profiles: vec![super::v0_4::CapabilityProfile {
                contract: "manipulation.grasp@v1".to_string(),
                kind: "transport".to_string(),
                local_system_id: "manipulator".to_string(),
                ready: true,
                attributes: std::collections::HashMap::from([(
                    "max-payload-grams".to_string(),
                    super::v0_4::ScalarValue {
                        value: Some(Value::IntegerValue(5_000)),
                    },
                )]),
            }],
            operation_support: vec![super::v0_4::OperationSupport {
                operation: Some(super::v0_4::OperationRef {
                    namespace: "object".to_string(),
                    name: "relocate".to_string(),
                    version: "v1".to_string(),
                }),
                local_system_id: "manipulator".to_string(),
            }],
            ..Default::default()
        };
        let decoded =
            super::v0_4::NodeRegistration::decode(registration.encode_to_vec().as_slice())
                .expect("current registration decodes");
        assert_eq!(decoded, registration);
        assert!(decoded.capabilities.is_empty());
        assert_eq!(decoded.operation_support.len(), 1);

        let invocation = super::v0_4::CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "task-a".to_string(),
            group_id: "group-a".to_string(),
            role_id: "grasper".to_string(),
            intent: Some(super::v0_4::ExecutionIntent {
                operation: Some(super::v0_4::OperationRef {
                    namespace: "object".to_string(),
                    name: "relocate".to_string(),
                    version: "v1".to_string(),
                }),
                objective: "Move the emergency kit to reception".to_string(),
                parameters: Default::default(),
            }),
            ..Default::default()
        };
        let decoded =
            super::v0_4::CanonicalInvocation::decode(invocation.encode_to_vec().as_slice())
                .expect("current semantic invocation decodes");
        assert_eq!(decoded, invocation);
        assert!(decoded.capability_contract.is_empty());
    }

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

        let readiness = super::v0_3::PeerChannelReadiness {
            session_id: "session-1".to_string(),
            sequence: 5,
            group_id: "group-1".to_string(),
            context_id: "guidance".to_string(),
            context_role_id: "dog".to_string(),
            local_system_id: "motion".to_string(),
            channel_instance_id: "peer-channel-1".to_string(),
            profile_id: "guidance-peer".to_string(),
            message_schema: "guidance/v1".to_string(),
            ready: true,
            valid_for_ms: 5_000,
        };
        let decoded =
            super::v0_3::PeerChannelReadiness::decode(readiness.encode_to_vec().as_slice())
                .expect("v0.3 peer readiness decodes");
        assert_eq!(decoded, readiness);
    }

    /// Proves v0.4 command identity and durable receipt evidence survive protobuf encoding.
    #[test]
    fn v0_4_round_trip_preserves_command_receipt_identity() {
        let execute = super::v0_4::Execute {
            session_id: "session-1".to_string(),
            execution_id: "attempt-1".to_string(),
            invocation: None,
            resource_ids: vec!["motor".to_string()],
            command_id: "dispatch-attempt-1".to_string(),
        };
        let decoded = super::v0_4::Execute::decode(execute.encode_to_vec().as_slice())
            .expect("v0.4 Execute decodes");
        assert_eq!(decoded, execute);

        let receipt = super::v0_4::CommandReceipt {
            session_id: "session-1".to_string(),
            sequence: 7,
            command_id: "dispatch-attempt-1".to_string(),
            execution_id: "attempt-1".to_string(),
            kind: super::v0_4::CommandKind::CommandExecute as i32,
            status: super::v0_4::CommandReceiptStatus::CommandPersisted as i32,
            reason: String::new(),
        };
        let decoded = super::v0_4::CommandReceipt::decode(receipt.encode_to_vec().as_slice())
            .expect("v0.4 command receipt decodes");
        assert_eq!(decoded, receipt);
    }
}
