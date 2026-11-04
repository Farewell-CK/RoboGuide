//! Offline operator reconciliation of an evidenced rejection before local task admission.
//!
//! Stop the Node first. An operator must establish absence of local execution independently.
//! HTTP rejection alone is insufficient evidence. This tool retains that attestation and the
//! original rejection in the journal, using its writer lock and normal sequenced fact API.

use node_service::{ExecutionJournal, JournalExecution, JournalStatus};
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::path::Path;

/// Exact immutable identity and explicit operator evidence for one rejected invocation.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Evidence {
    /// Version of this local maintenance attestation.
    schema_version: String,
    /// Original RG execution identity.
    execution_id: String,
    /// Digest of the original invocation bytes.
    invocation_digest: String,
    /// Digest of the original configured workflow.
    workflow_digest: String,
    /// Exact sequence observed before this maintenance operation.
    expected_sequence: u64,
    /// Original rejection text retained verbatim.
    original_reason: String,
    /// Deployment operator taking responsibility for the no-dispatch conclusion.
    operator: String,
    /// Independently established absence of a local execution.
    confirmed_no_local_execution: bool,
    /// Retained observations and their origin, including the local admission boundary.
    evidence: String,
}

/// Refuse stale identities, handle-bearing work and unsubstantiated attestations.
fn validate(record: &JournalExecution, evidence: &Evidence) -> Result<(), Box<dyn Error>> {
    if evidence.schema_version != "roboguide.rejected-dispatch-attestation/v0.1"
        || !evidence.confirmed_no_local_execution
        || evidence.operator.trim().is_empty()
        || evidence.operator.len() > 256
        || evidence.evidence.trim().len() < 32
        || evidence.evidence.len() > 8192
        || record.execution_id() != evidence.execution_id
        || record.spec().invocation_digest() != evidence.invocation_digest
        || record.spec().workflow_digest() != evidence.workflow_digest
        || record.sequence() != evidence.expected_sequence
        || record.reason() != evidence.original_reason
        || record.local_handle().is_some()
        || record.status() != JournalStatus::ReconciliationRequired
        || !matches!(
            record.reason(),
            "local transport failed: local HTTP endpoint returned status 400 Bad Request"
                | "local transport failed: local HTTP endpoint returned status 404 Not Found"
        )
    {
        return Err("attestation does not match an unresolved rejected dispatch".into());
    }
    Ok(())
}

/// Record an operator-established no-dispatch failure through the existing journal authority.
fn apply(journal_path: &Path, evidence: &Evidence) -> Result<(), Box<dyn Error>> {
    let journal = ExecutionJournal::open(journal_path)?;
    let record = journal
        .get(&evidence.execution_id)?
        .ok_or("execution is absent")?;
    validate(&record, evidence)?;
    let sequence = record
        .sequence()
        .checked_add(1)
        .ok_or("sequence overflow")?;
    journal.record_status(
        &evidence.execution_id,
        sequence,
        JournalStatus::Failed,
        serde_json::to_string(evidence)?,
    )?;
    println!("recorded Failed at sequence {sequence}; Node reconnect reports the original attempt");
    Ok(())
}

/// Require explicit local maintenance intent and bounded evidence before opening the writer.
fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 3 || args[0] != "--apply-no-dispatch" {
        return Err(
            "usage: reconcile_rejected_dispatch --apply-no-dispatch JOURNAL EVIDENCE.json".into(),
        );
    }
    let bytes = std::fs::read(&args[2])?;
    if bytes.len() > 16384 {
        return Err("attestation exceeds 16 KiB".into());
    }
    let evidence: Evidence = serde_json::from_slice(&bytes)?;
    apply(Path::new(&args[1]), &evidence)
}

#[cfg(test)]
mod tests {
    use super::*;
    use node_service::ExecutionSpec;

    /// A rejected fixture requires exact evidence and preserves its original immutable input.
    #[test]
    fn exact_rejection_only() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("journal.sqlite3");
        let journal = ExecutionJournal::open(&path).unwrap();
        let spec = ExecutionSpec::new(b"{}".to_vec(), "fixture-workflow", vec![]).unwrap();
        journal.prepare_dispatch("original", &spec).unwrap();
        journal.authorize_local_dispatch("original").unwrap();
        let reason = "local transport failed: local HTTP endpoint returned status 404 Not Found";
        let record = journal
            .record_status("original", 1, JournalStatus::ReconciliationRequired, reason)
            .unwrap();
        let mut evidence = Evidence {
            schema_version: "roboguide.rejected-dispatch-attestation/v0.1".into(),
            execution_id: "original".into(),
            invocation_digest: spec.invocation_digest().into(),
            workflow_digest: spec.workflow_digest().into(),
            expected_sequence: 1,
            original_reason: reason.into(),
            operator: "fixture operator".into(),
            confirmed_no_local_execution: true,
            evidence: "Fixture rejected before admission; fixture effect sink is empty.".into(),
        };
        assert!(validate(&record, &evidence).is_ok());
        evidence.confirmed_no_local_execution = false;
        assert!(validate(&record, &evidence).is_err());
        evidence.confirmed_no_local_execution = true;
        evidence.expected_sequence = 2;
        assert!(validate(&record, &evidence).is_err());
        evidence.expected_sequence = 1;
        evidence.invocation_digest.push('x');
        assert!(validate(&record, &evidence).is_err());
        evidence.invocation_digest = spec.invocation_digest().into();
        drop(journal);
        apply(&path, &evidence).unwrap();
        assert!(apply(&path, &evidence).is_err());
        let journal = ExecutionJournal::open(&path).unwrap();
        let result = journal.get("original").unwrap().unwrap();
        assert_eq!(result.status(), JournalStatus::Failed);
        assert_eq!(result.spec().invocation_digest(), spec.invocation_digest());
        assert_eq!(result.sequence(), 2);
        assert!(result.reason().contains(reason));
    }
}
