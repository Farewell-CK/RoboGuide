#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Opt-in trusted-loopback consensus supervisor for exactly one owned Controller child.

mod config;
mod supervisor;

/// Validates configuration before opening consensus storage; standalone Controller remains unchanged.
#[tokio::main(worker_threads = 4)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let first = arguments
        .next()
        .ok_or("usage: controller-replica [--policy-digest] <config.json>")?;
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
