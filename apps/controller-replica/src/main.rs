#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Opt-in trusted-loopback consensus supervisor for exactly one owned Controller child.

mod config;
mod content_maintenance;
mod metrics;
mod projection;
mod supervisor;

use controller_replication::ReplicaAdminClient;
use std::collections::BTreeSet;
use std::io::Read as _;

/// Validates configuration before opening consensus storage; standalone Controller remains unchanged.
#[tokio::main(worker_threads = 4)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let first = arguments
        .next()
        .ok_or("usage: controller-replica [--policy-digest] <config.json>")?;
    if first == "--inspect-initialization-source" {
        let path = arguments
            .next()
            .ok_or("source inspection requires an explicit input.json")?;
        if arguments.next().is_some() {
            return Err("unexpected source inspection arguments".into());
        }
        let mut raw = Vec::new();
        std::fs::File::open(path)?
            .take(16 * 1024 + 1)
            .read_to_end(&mut raw)?;
        if raw.len() > 16 * 1024 {
            return Err("source input exceeds its bound".into());
        }
        let input: controller_replication::ContentInitializationInput =
            serde_json::from_slice(&raw)?;
        let observation = tokio::task::spawn_blocking(move || {
            controller_replication::inspect_archived_source(&input)
        })
        .await??;
        println!("{}", serde_json::to_string(&observation)?);
        return Ok(());
    }
    if first == "--content-maintenance" {
        let path = arguments
            .next()
            .ok_or("content maintenance requires an explicit request.json")?;
        if arguments.next().is_some() {
            return Err("unexpected content maintenance arguments".into());
        }
        let result = content_maintenance::run(std::path::Path::new(&path));
        match result {
            Ok(result) => println!("{}", serde_json::json!({"result":result})),
            Err(error) => {
                println!("{}", serde_json::json!({"error":error}));
                return Err(error.into());
            }
        }
        return Ok(());
    }
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
        let client = if configuration.replica.rotates_certificates() {
            ReplicaAdminClient::with_trust_database(
                configuration.replica,
                leader,
                &configuration.storage_directory.join("replica.sqlite3"),
            )?
        } else {
            ReplicaAdminClient::new(configuration.replica, leader)?
        };
        match operation.as_str() {
            "--abort-application-upgrade" => {
                let original = arguments
                    .next()
                    .ok_or("missing original application upgrade ID")?;
                let command = arguments
                    .next()
                    .ok_or("missing original abandonment command ID")?;
                if arguments.next().is_some() {
                    return Err("unexpected application upgrade abandonment arguments".into());
                }
                println!(
                    "{}",
                    serde_json::to_string(
                        &client
                            .abort_application_generation(original, command)
                            .await?
                    )?
                );
            }
            "--application-upgrade" => {
                let path = arguments
                    .next()
                    .ok_or("application upgrade requires original request.json")?;
                if arguments.next().is_some() {
                    return Err("unexpected application upgrade arguments".into());
                }
                let mut bytes = Vec::new();
                std::fs::File::open(path)?
                    .take(16 * 1024 + 1)
                    .read_to_end(&mut bytes)?;
                if bytes.len() > 16 * 1024 {
                    return Err("original application upgrade request exceeds its bound".into());
                }
                let request = serde_json::from_slice(&bytes)?;
                println!(
                    "{}",
                    serde_json::to_string(&client.upgrade_application_generation(request).await?)?
                );
            }
            "--observe-application-upgrade" => {
                if arguments.next().is_some() {
                    return Err("unexpected application upgrade observation arguments".into());
                }
                let (authority, pending) = client.observe_application_upgrade().await?;
                println!(
                    "{}",
                    serde_json::json!({"authority":authority,"pending":pending})
                );
            }
            "--observe-leader-transfer" => {
                let transfer_id = arguments.next().ok_or("missing original transfer id")?;
                if arguments.next().is_some() {
                    return Err("unexpected transfer observation arguments".into());
                }
                println!(
                    "{}",
                    serde_json::to_string(&client.leader_transfer_record(&transfer_id).await?)?
                );
            }
            "--certificate-observation" => {
                if arguments.next().is_some() {
                    return Err("unexpected certificate observation arguments".into());
                }
                println!(
                    "{}",
                    serde_json::to_string(&client.certificate_observation().await?)?
                );
            }
            "--certificate-trust" => {
                if arguments.next().is_some() {
                    return Err("unexpected certificate trust arguments".into());
                }
                println!(
                    "{}",
                    serde_json::to_string(&client.certificate_trust().await?)?
                );
            }
            "--rotate-certificate" => {
                let path = arguments
                    .next()
                    .ok_or("certificate rotation requires a closed request.json")?;
                if arguments.next().is_some() {
                    return Err("unexpected certificate rotation arguments".into());
                }
                let mut bytes = Vec::new();
                std::fs::File::open(path)?
                    .take(4097)
                    .read_to_end(&mut bytes)?;
                if bytes.len() > 4096 {
                    return Err("certificate request exceeds bounds".into());
                }
                let request = serde_json::from_slice(&bytes)?;
                println!(
                    "{}",
                    serde_json::to_string(&client.rotate_certificate(request).await?)?
                );
            }
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
    let upgrade_digest_only = first == "--upgrade-policy-digest";
    let profile_only = first == "--profile-identity";
    let upgrade_only = first == "--upgrade-initialization-parent";
    let path = if digest_only || upgrade_digest_only || profile_only || upgrade_only {
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
    if upgrade_digest_only {
        let upgrade = configuration
            .application_upgrade
            .as_ref()
            .ok_or("explicit target executable is absent")?;
        println!(
            "{}",
            configuration
                .policy_digest_for_binary(&upgrade.binary, &upgrade.target.application_schema)?
        );
        return Ok(());
    }
    if profile_only {
        configuration.replica.validate()?;
        println!("{}", configuration.replica.profile_identity());
        return Ok(());
    }
    configuration.validate()?;
    if upgrade_only {
        if configuration.schema != "roboguide.controller-replica-process/v0.7" {
            return Err("parent9 upgrade requires explicit process v0.7".into());
        }
        let content = configuration
            .content
            .as_ref()
            .ok_or("explicit Root8 content is missing")?;
        let root = controller_replication::ReplicaContentStore::open_with_staging_gc(
            &content.root,
            &configuration.replica.profile_identity(),
            configuration.replica.replica_id,
            content.restore_binding_digest.as_deref(),
        )?;
        let parent = controller_replication::SqliteRaftStore::upgrade_content_initialization(
            &configuration.storage_directory.join("replica.sqlite3"),
            &configuration.replica.profile_identity(),
            &configuration.replica.application_schema,
            root,
            content.initialization.clone(),
        )?;
        println!(
            "{}",
            serde_json::json!({"schema":"roboguide.replica-storage/v0.9","revision":parent.accepted_view()?.revision})
        );
        return Ok(());
    }
    supervisor::run(configuration).await.map_err(Into::into)
}
