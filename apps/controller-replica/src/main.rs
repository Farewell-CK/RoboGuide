#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Opt-in trusted-loopback consensus supervisor for exactly one owned Controller child.

mod config;
mod metrics;
mod supervisor;

use controller_replication::ReplicaAdminClient;
use std::collections::BTreeSet;

/// Validates configuration before opening consensus storage; standalone Controller remains unchanged.
#[tokio::main(worker_threads = 4)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let first = arguments
        .next()
        .ok_or("usage: controller-replica [--policy-digest] <config.json>")?;
    if first == "--admin" {
        let path = arguments.next().ok_or("missing process config.json")?;
        if arguments.next().as_deref() != Some("--leader") {
            return Err("admin mode requires --leader <member-id>".into());
        }
        let leader = arguments
            .next()
            .ok_or("missing administration leader id")?
            .parse::<u64>()?;
        let operation = arguments.next().ok_or("missing administration operation")?;
        let configuration: config::ProcessConfig = serde_json::from_slice(&std::fs::read(&path)?)?;
        configuration.validate()?;
        let client = ReplicaAdminClient::new(configuration.replica, leader)?;
        match operation.as_str() {
            "--membership" => {
                if arguments.next().is_some() {
                    return Err("unexpected admin arguments".into());
                }
                println!("{}", serde_json::to_string(&client.membership().await?)?);
            }
            "--add-learner" => {
                let member = arguments
                    .next()
                    .ok_or("missing learner member id")?
                    .parse::<u64>()?;
                if arguments.next().is_some() {
                    return Err("unexpected admin arguments".into());
                }
                client.add_learner(member).await?;
                println!("{{\"state\":\"learner_caught_up\",\"member\":{member}}}");
            }
            "--change-voters" => {
                let voters = arguments.next().ok_or("missing voter set")?;
                let retain = match arguments.next().as_deref() {
                    None => false,
                    Some("--retain") => true,
                    Some(_) => return Err("unexpected admin arguments".into()),
                };
                if arguments.next().is_some() {
                    return Err("unexpected admin arguments".into());
                }
                let voters = voters
                    .split(',')
                    .map(str::parse::<u64>)
                    .collect::<Result<BTreeSet<_>, _>>()?;
                client.change_membership(voters, retain).await?;
                println!("{{\"state\":\"membership_committed\"}}");
            }
            "--transfer-leader" => {
                let target = arguments
                    .next()
                    .ok_or("missing transfer target")?
                    .parse::<u64>()?;
                if arguments.next().as_deref() != Some("--transfer-id") {
                    return Err("leader transfer requires --transfer-id <id>".into());
                }
                let transfer_id = arguments.next().ok_or("missing transfer id")?;
                if arguments.next().is_some() {
                    return Err("unexpected admin arguments".into());
                }
                let receipt = client.request_leader_transfer(target, transfer_id).await?;
                println!("{}", serde_json::to_string(&receipt)?);
            }
            _ => return Err("unknown administration operation".into()),
        }
        return Ok(());
    }
    if first == "--application-schema" {
        if arguments.next().is_some() {
            return Err("schema inspection accepts no configuration or storage path".into());
        }
        println!("{}", state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA);
        return Ok(());
    }
    let digest_only = first == "--policy-digest";
    let path = if digest_only {
        arguments.next().ok_or("missing config.json")?
    } else {
        first
    };
    if arguments.next().is_some() {
        return Err("unexpected controller-replica argument".into());
    }
    let configuration: config::ProcessConfig = serde_json::from_slice(&std::fs::read(&path)?)?;
    if digest_only {
        println!("{}", configuration.policy_digest()?);
        return Ok(());
    }
    configuration.validate()?;
    supervisor::run(configuration).await.map_err(Into::into)
}
