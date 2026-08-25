//! Generated formal gRPC bidirectional streaming contract for Node Protocol v0.2.

/// Generated v0.2 protobuf messages and client/server service bindings.
#[allow(clippy::all, clippy::missing_docs_in_private_items, missing_docs)]
pub mod v0_2 {
    /// Exact stream protocol version advertised during Hello negotiation.
    pub const PROTOCOL_VERSION: &str = "roboguide.node-protocol/v0.2";
    /// Exact semantic Node Contract version advertised during Hello negotiation.
    pub const NODE_CONTRACT_VERSION: &str = "roboguide.node.v0.2";

    tonic::include_proto!("roboguide.node.v0_2");
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
}
